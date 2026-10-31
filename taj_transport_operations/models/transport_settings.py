"""Transport Operations settings and the dispatch worklist cutoff date."""

from datetime import datetime, time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.safe_eval import safe_eval

CUTOFF_PARAM = "taj_transport_operations.dispatch_cutoff_date"
DISPATCH_ACTIONS = {
    "taj_transport_operations.action_sale_order_line_without_trip",
    "taj_transport_operations.action_sale_order_line_without_truck",
}


def get_dispatch_cutoff_date(env):
    """Cutoff date from the configuration, or False when unset/invalid."""
    value = env["ir.config_parameter"].sudo().get_param(CUTOFF_PARAM)
    if not value:
        return False
    try:
        return fields.Date.to_date(value)
    except (TypeError, ValueError):
        return False


def get_dispatch_cutoff_start(env, cutoff):
    """Cutoff as a UTC datetime, starting at the user's local midnight."""
    if not cutoff:
        return False
    if env.user.tz:
        try:
            start = pytz.timezone(env.user.tz).localize(
                datetime.combine(cutoff, time.min),
                is_dst=False,
            )
            return start.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")
        except (pytz.UnknownTimeZoneError, ValueError, OverflowError):
            pass
    return fields.Date.to_string(cutoff)


class TajTransportSettings(models.TransientModel):
    _name = "taj.transport.settings"
    _description = "Transport Operations Settings"

    dispatch_cutoff_date = fields.Date(
        string="Dispatch Cutoff Date",
        help="Confirmed sales orders dated before this day are hidden from "
        "the Sale Orders Without Trip and Sale Orders Without Truck worklists, whatever "
        "filters are applied. Leave empty to show every confirmed order.",
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, readonly=True, required=True)
    inactivity_alerts_enabled = fields.Boolean(string="Enable Inactivity Alerts")
    inactivity_hours = fields.Float(string="Inactivity Hours", default=24, required=True)
    inactivity_recipient = fields.Char(string="Alert Recipient Email")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "dispatch_cutoff_date" in fields_list:
            values["dispatch_cutoff_date"] = get_dispatch_cutoff_date(self.env)
        company = self.env.company
        for field, source in (
            ("inactivity_alerts_enabled", "transport_inactivity_enabled"),
            ("inactivity_hours", "transport_inactivity_hours"),
            ("inactivity_recipient", "transport_inactivity_recipient"),
        ):
            if field in fields_list:
                values[field] = company[source]
        return values

    def action_save(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("taj_transport_operations.group_transport_manager"):
            raise AccessError(_("Only a Transport Manager can change transport settings."))
        if self.company_id != self.env.company:
            raise AccessError(_("Open settings in the company you want to configure."))
        self.company_id.sudo().write({
            "transport_inactivity_enabled": self.inactivity_alerts_enabled,
            "transport_inactivity_hours": self.inactivity_hours,
            "transport_inactivity_recipient": (self.inactivity_recipient or "").strip(),
        })
        self.env["ir.config_parameter"].sudo().set_param(
            CUTOFF_PARAM,
            fields.Date.to_string(self.dispatch_cutoff_date)
            if self.dispatch_cutoff_date
            else "",
        )
        return {"type": "ir.actions.act_window_close"}


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    def _get_action_dict(self):
        result = super()._get_action_dict()
        if result.get("res_model") != "sale.order.line":
            return result
        if self.xml_id not in DISPATCH_ACTIONS:
            return result
        cutoff = get_dispatch_cutoff_start(
            self.env, get_dispatch_cutoff_date(self.env))
        if cutoff:
            domain = safe_eval(result.get("domain") or "[]")
            domain.append(("order_id.date_order", ">=", cutoff))
            result["domain"] = repr(domain)
        return result
