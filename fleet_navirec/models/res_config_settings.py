from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .navirec_api import DEFAULT_API_URL, NavirecAPIError, NavirecClient


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    navirec_api_token = fields.Char(config_parameter="fleet_navirec.api_token")
    navirec_account_id = fields.Char(config_parameter="fleet_navirec.account_id")
    navirec_api_url = fields.Char(
        config_parameter="fleet_navirec.api_url", default=DEFAULT_API_URL
    )
    navirec_timezone = fields.Char(config_parameter="fleet_navirec.timezone")
    navirec_stale_hours = fields.Integer(
        config_parameter="fleet_navirec.stale_hours", default=24
    )
    navirec_use_area_names = fields.Boolean(
        string="Use Navirec Area / POI Names",
        config_parameter="fleet_navirec.use_area_names",
        default=False,
        help=(
            "When enabled, the state sync also reads active Navirec areas and "
            "uses a matching area/POI name as the human-readable position. "
            "GPS synchronization still succeeds if area access is unavailable."
        ),
    )
    navirec_vehicle_url_template = fields.Char(
        string="Vehicle Deep-Link Template",
        config_parameter="fleet_navirec.vehicle_url_template",
        help=(
            "Optional verified Navirec vehicle URL containing {uuid}. "
            "When empty, Open Navirec opens the Navirec application home page."
        ),
    )
    navirec_last_state_sync_success = fields.Datetime(
        compute="_compute_navirec_monitoring",
        string="Last Successful State Sync",
    )
    navirec_last_state_sync_count = fields.Integer(
        compute="_compute_navirec_monitoring",
        string="Vehicles Updated",
    )
    navirec_last_state_sync_error = fields.Char(
        compute="_compute_navirec_monitoring",
        string="Last State Sync Error",
    )
    navirec_last_state_sync_error_time = fields.Datetime(
        compute="_compute_navirec_monitoring",
        string="State Sync Error Time",
    )
    navirec_last_vehicle_match_success = fields.Datetime(
        compute="_compute_navirec_monitoring",
        string="Last Successful Vehicle Match",
    )
    navirec_last_vehicle_match_count = fields.Integer(
        compute="_compute_navirec_monitoring",
        string="Vehicles Matched",
    )
    navirec_last_vehicle_match_error = fields.Char(
        compute="_compute_navirec_monitoring",
        string="Last Vehicle Match Error",
    )
    navirec_last_vehicle_match_error_time = fields.Datetime(
        compute="_compute_navirec_monitoring",
        string="Vehicle Match Error Time",
    )

    @api.constrains("navirec_vehicle_url_template")
    def _check_navirec_vehicle_url_template(self):
        for settings in self:
            template = (settings.navirec_vehicle_url_template or "").strip()
            if not template:
                continue
            if not template.startswith("https://") or "{uuid}" not in template:
                raise ValidationError(_(
                    "The Navirec vehicle deep-link template must be an HTTPS URL "
                    "containing the {uuid} placeholder."
                ))

    @api.constrains("navirec_stale_hours")
    def _check_navirec_stale_hours(self):
        for settings in self:
            if settings.navirec_stale_hours <= 0:
                raise ValidationError(
                    _("The stale GPS threshold must be greater than zero.")
                )

    def _compute_navirec_monitoring(self):
        params = self.env["ir.config_parameter"].sudo()

        def _datetime(key):
            value = params.get_param(key)
            if not value:
                return False
            try:
                return fields.Datetime.to_datetime(value)
            except (TypeError, ValueError):
                return False

        def _integer(key):
            try:
                return int(params.get_param(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        for settings in self:
            settings.navirec_last_state_sync_success = _datetime(
                "fleet_navirec.last_state_sync_success"
            )
            settings.navirec_last_state_sync_count = _integer(
                "fleet_navirec.last_state_sync_count"
            )
            settings.navirec_last_state_sync_error = (
                params.get_param("fleet_navirec.last_state_sync_error")
                or False
            )
            settings.navirec_last_state_sync_error_time = _datetime(
                "fleet_navirec.last_state_sync_error_time"
            )
            settings.navirec_last_vehicle_match_success = _datetime(
                "fleet_navirec.last_vehicle_match_success"
            )
            settings.navirec_last_vehicle_match_count = _integer(
                "fleet_navirec.last_vehicle_match_count"
            )
            settings.navirec_last_vehicle_match_error = (
                params.get_param("fleet_navirec.last_vehicle_match_error")
                or False
            )
            settings.navirec_last_vehicle_match_error_time = _datetime(
                "fleet_navirec.last_vehicle_match_error_time"
            )

    def _navirec_run_sync_action(self, operation):
        self.ensure_one()
        if not self.navirec_api_token:
            raise UserError(_("Please enter a Navirec API token first."))

        # Persist config_parameter-backed fields before the cron-style helpers
        # read them. This makes Sync/Match Now honor unsaved Settings changes.
        self.set_values()

        vehicle_model = self.env["fleet.vehicle"]
        params = self.env["ir.config_parameter"].sudo()
        if operation == "vehicle_match":
            vehicle_model._cron_navirec_match_vehicles()
            label = _("Vehicle matching")
        else:
            vehicle_model._cron_navirec_sync_states()
            label = _("Vehicle state synchronization")

        error = params.get_param(
            f"fleet_navirec.last_{operation}_error"
        )
        count = params.get_param(
            f"fleet_navirec.last_{operation}_count", "0"
        )
        if error:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Navirec"),
                    "message": _("%(label)s failed: %(error)s") % {
                        "label": label,
                        "error": error,
                    },
                    "type": "danger",
                    "sticky": True,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Navirec"),
                "message": _("%(label)s completed. Records updated: %(count)s")
                % {"label": label, "count": count},
                "type": "success",
            },
        }

    def action_navirec_match_now(self):
        return self._navirec_run_sync_action("vehicle_match")

    def action_navirec_mapping_audit(self):
        self.ensure_one()
        if not self.navirec_api_token:
            raise UserError(_("Please enter a Navirec API token first."))
        self.set_values()
        client = self.env["fleet.vehicle"]._navirec_client()
        if not client:
            raise UserError(_("Navirec API token is not configured."))
        try:
            audit = self.env["fleet.vehicle"]._navirec_build_mapping_audit(client)
        except NavirecAPIError as exc:
            raise UserError(str(exc)) from exc

        details = []
        if audit["duplicate_remote"]:
            details.append(_("Duplicate Navirec plates: %(plates)s") % {
                "plates": ", ".join(audit["duplicate_remote"][:10]),
            })
        if audit["duplicate_local"]:
            details.append(_("Duplicate Odoo plates: %(plates)s") % {
                "plates": ", ".join(audit["duplicate_local"][:10]),
            })
        if audit["unmatched_remote"]:
            details.append(_("Unmatched Navirec: %(plates)s") % {
                "plates": ", ".join(audit["unmatched_remote"][:10]),
            })
        if audit["odoo_only"]:
            details.append(_("Odoo-only plate candidates: %(plates)s") % {
                "plates": ", ".join(audit["odoo_only"][:10]),
            })

        summary = _(
            "Navirec vehicles: %(remote)s | Odoo plate candidates: %(local)s | "
            "Deterministic matches: %(matched)s | Currently mapped: %(mapped)s"
        ) % {
            "remote": audit["remote_vehicle_count"],
            "local": audit["odoo_plate_candidate_count"],
            "matched": audit["matched_count"],
            "mapped": audit["currently_mapped_count"],
        }
        if details:
            summary += "\n" + "\n".join(details)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Navirec Mapping Audit"),
                "message": summary,
                "type": "warning" if details else "success",
                "sticky": True,
            },
        }

    def action_navirec_sync_all_now(self):
        return self._navirec_run_sync_action("state_sync")

    def action_navirec_open_sync_logs(self):
        return self.env["ir.actions.actions"]._for_xml_id(
            "fleet_navirec.fleet_navirec_sync_log_action"
        )

    def action_navirec_test_connection(self):
        self.ensure_one()
        if not self.navirec_api_token:
            raise UserError(_("Please enter a Navirec API token first."))
        params = self.env["ir.config_parameter"].sudo()
        try:
            client = NavirecClient(
                api_url=self.navirec_api_url,
                token=self.navirec_api_token,
                version=params.get_param("fleet_navirec.api_version") or None,
                timezone=self.navirec_timezone,
            )
            client.test_connection()
            if self.navirec_use_area_names:
                client.get_areas(account_id=self.navirec_account_id or None)
        except NavirecAPIError as exc:
            raise UserError(str(exc)) from exc
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Navirec"), "message": _("Connection successful."), "type": "success"},
        }
