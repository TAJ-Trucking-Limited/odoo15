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
            NavirecClient(
                api_url=self.navirec_api_url,
                token=self.navirec_api_token,
                version=params.get_param("fleet_navirec.api_version") or None,
                timezone=self.navirec_timezone,
            ).test_connection()
        except NavirecAPIError as exc:
            raise UserError(str(exc)) from exc
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Navirec"), "message": _("Connection successful."), "type": "success"},
        }
