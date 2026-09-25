from odoo import _, fields, models
from odoo.exceptions import UserError

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
