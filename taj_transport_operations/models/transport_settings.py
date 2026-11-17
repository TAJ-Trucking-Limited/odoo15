"""Transport Operations settings and the dispatch worklist cutoff date."""

from datetime import datetime, time

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval

CUTOFF_PARAM = "taj_transport_operations.dispatch_cutoff_date"
DISPATCH_ACTIONS = {
    "taj_transport_operations.action_sale_order_line_without_trip",
    "taj_transport_operations.action_sale_order_line_without_truck",
}

CONFIGURABLE_SHIPMENT_FIELDS = (
    "container_num", "srn", "consignee", "size", "weight", "file_name",
    "file_type_id", "clearing_service", "bill_of_lading", "eta", "etol",
    "shipping_line", "cargo_type_id", "cargo_description",
    "special_handling_instructions", "transport_planned_departure_at",
    "transport_planned_arrival_at", "analytic_account_id",
)


class ResCompany(models.Model):
    _inherit = "res.company"

    transport_required_shipment_fields = fields.Json(
        string="Required Shipment Fields",
        default=lambda self: ["file_type_id"],
        help="Shipment fields required when saving a quotation or sales order. "
        "File Type applies only to Goods product lines.",
    )

    @api.constrains("transport_required_shipment_fields")
    def _check_required_shipment_field_names(self):
        for company in self:
            names = company.transport_required_shipment_fields or []
            if not isinstance(names, list) or any(
                not isinstance(name, str) or name not in CONFIGURABLE_SHIPMENT_FIELDS
                for name in names
            ):
                raise ValidationError(_("Only the 18 Shipment and Cargo fields can be configured."))

    def write(self, vals):
        if "transport_required_shipment_fields" in vals and not self.env.su:
            if not self.env.user.has_group("taj_transport_operations.group_transport_manager"):
                raise AccessError(_("Only a Transport Manager can change shipment requirements."))
            if any(company not in self.env.companies for company in self):
                raise AccessError(_("Configure requirements only for an allowed company."))
        return super().write(vals)


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
        "the Sale Orders Without Trip and Sale Orders without Allocation worklists, whatever "
        "filters are applied. Leave empty to show every confirmed order.",
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        readonly=True,
        required=True,
    )
    inactivity_alerts_enabled = fields.Boolean(string="Enable Inactivity Alerts")
    inactivity_hours = fields.Float(
        string="Inactivity Hours", default=24, required=True
    )
    inactivity_recipient = fields.Char(string="Alert Recipient Email")
    required_shipment_field_ids = fields.Many2many(
        "ir.model.fields", string="Required Shipment and Cargo Fields",
        domain=lambda self: [
            ("model", "=", "sale.order.line"),
            ("name", "in", CONFIGURABLE_SHIPMENT_FIELDS),
        ],
        help="Checked fields are required on save. Unchecked fields are optional. "
        "File Type applies only to Goods product lines.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "dispatch_cutoff_date" in fields_list:
            values["dispatch_cutoff_date"] = get_dispatch_cutoff_date(self.env)
        company = self.env.company
        if "required_shipment_field_ids" in fields_list:
            selected = self.env["ir.model.fields"].search([
                ("model", "=", "sale.order.line"),
                ("name", "in", company.transport_required_shipment_fields or []),
            ])
            values["required_shipment_field_ids"] = [Command.set(selected.ids)]
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
        if not self.env.su and not self.env.user.has_group(
            "taj_transport_operations.group_transport_manager"
        ):
            raise AccessError(
                _("Only a Transport Manager can change transport settings.")
            )
        if self.company_id != self.env.company:
            raise AccessError(_("Open settings in the company you want to configure."))
        if any(
            field.model != "sale.order.line" or field.name not in CONFIGURABLE_SHIPMENT_FIELDS
            for field in self.required_shipment_field_ids
        ):
            raise ValidationError(_("Only the 18 Shipment and Cargo fields can be configured."))
        self.company_id.sudo().write(
            {
                "transport_required_shipment_fields": self.required_shipment_field_ids.mapped("name"),
                "transport_inactivity_enabled": self.inactivity_alerts_enabled,
                "transport_inactivity_hours": self.inactivity_hours,
                "transport_inactivity_recipient": (
                    self.inactivity_recipient or ""
                ).strip(),
            }
        )
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
        cutoff = get_dispatch_cutoff_start(self.env, get_dispatch_cutoff_date(self.env))
        if cutoff:
            domain = safe_eval(result.get("domain") or "[]")
            domain.append(("order_id.date_order", ">=", cutoff))
            result["domain"] = repr(domain)
        return result
