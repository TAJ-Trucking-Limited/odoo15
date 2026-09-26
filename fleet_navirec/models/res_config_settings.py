from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .navirec_api import (
    DEFAULT_API_URL, NavirecAPIError, NavirecClient, public_navirec_error,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    navirec_api_token = fields.Char(config_parameter="fleet_navirec.api_token")
    navirec_account_id = fields.Char(config_parameter="fleet_navirec.account_id")
    navirec_integration_user_id = fields.Char(
        string="Integration User UUID",
        config_parameter="fleet_navirec.integration_user_id",
        help=(
            "Optional token-owner UUID for configuration/geocoding. Leave empty "
            "when the API token contains a UUID user_id claim. For opaque tokens, "
            "enter the UUID of the user that owns this API token, not an admin."
        ),
    )
    navirec_api_url = fields.Char(
        config_parameter="fleet_navirec.api_url", default=DEFAULT_API_URL
    )
    navirec_timezone = fields.Char(config_parameter="fleet_navirec.timezone")
    navirec_stale_hours = fields.Integer(
        config_parameter="fleet_navirec.stale_hours", default=24
    )
    navirec_use_area_names = fields.Boolean(
        string="Use Navirec Human-readable Locations",
        config_parameter="fleet_navirec.use_area_names",
        default=False,
        help=(
            "When enabled, the state sync prefers a matching Navirec Area/POI "
            "name, then a nearby Navirec event or trip address, then Navirec's "
            "own reverse geocoder. GPS synchronization still succeeds if "
            "location enrichment is unavailable."
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

    @api.constrains("navirec_integration_user_id")
    def _check_navirec_integration_user_id(self):
        for settings in self:
            value = settings.navirec_integration_user_id
            if value and not NavirecClient._uuid(value):
                raise ValidationError(_("Navirec Integration User UUID must be a valid UUID."))

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
        message = _("%(label)s completed. Records updated: %(count)s") % {
            "label": label,
            "count": count,
        }
        note = self.env["fleet.navirec.sync.log"].sudo().search(
            [("operation", "=", operation)],
            limit=1,
        ).message
        if note:
            message = "%s\n%s" % (message, note)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Navirec"),
                "message": message,
                "type": "warning" if note else "success",
                "sticky": bool(note),
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
                user_id=self.navirec_integration_user_id or None,
            )
            client.test_connection()
        except NavirecAPIError as exc:
            raise UserError(public_navirec_error(exc)) from exc
        notes = []
        if self.navirec_use_area_names:
            # Base GPS access and readable-location permissions are different.
            # Test both optional services, but never report a GPS failure for them.
            for label, check in (
                (_("Areas"), client.get_areas),
                (_("Geocoding configuration"), client.get_geocoding_context),
            ):
                try:
                    check(account_id=self.navirec_account_id or None)
                except NavirecAPIError as exc:
                    notes.append("%s: %s" % (label, public_navirec_error(exc)))
        message = _("Navirec connection successful.")
        if notes:
            message += "\n" + "\n".join(notes)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Navirec"),
                "message": message,
                "type": "warning" if notes else "success",
                "sticky": bool(notes),
            },
        }
