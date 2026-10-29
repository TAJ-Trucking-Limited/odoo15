"""Transport Operations settings and the dispatch worklist cutoff date."""

from datetime import datetime, time

import pytz

from odoo import api, fields, models
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
        "the Lines Without Trip and Lines Without Truck worklists, whatever "
        "filters are applied. Leave empty to show every confirmed order.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "dispatch_cutoff_date" in fields_list:
            values["dispatch_cutoff_date"] = get_dispatch_cutoff_date(self.env)
        return values

    def action_save(self):
        self.ensure_one()
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
