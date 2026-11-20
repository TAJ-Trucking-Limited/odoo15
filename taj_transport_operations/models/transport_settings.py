"""Transport Operations settings and schedules."""

import json
import re
from datetime import datetime, time, timedelta

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval

CUTOFF_PARAM = "taj_transport_operations.dispatch_cutoff_date"
CUSTOMER_REPORT_SCHEDULE_PARAM = "taj_transport_operations.customer_report_schedule"
DISPATCH_ACTIONS = {
    "taj_transport_operations.action_sale_order_line_without_trip",
    "taj_transport_operations.action_sale_order_line_without_truck",
}

CONFIGURABLE_SHIPMENT_FIELDS = (
    "container_num", "srn", "consignee", "size", "weight", "file_name",
    "file_type_id", "clearing_service", "bill_of_lading", "eta", "etol",
    "shipping_line", "cargo_type_id", "cargo_description",
    "special_handling_instructions", "analytic_account_id",
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
                raise ValidationError(
                    _("Only the %s Shipment and Cargo fields can be configured.")
                    % len(CONFIGURABLE_SHIPMENT_FIELDS)
                )

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


def next_customer_report_at(times, timezone, now):
    """Find the next configured local send time and return it in Odoo's UTC format."""
    tz = pytz.timezone(timezone)
    now_utc = pytz.utc.localize(now)
    today = now_utc.astimezone(tz).date()
    for day in (today, today + timedelta(days=1)):
        candidates = []
        for send_time in times:
            local = datetime.combine(day, time.fromisoformat(send_time))
            candidate = tz.localize(local, is_dst=False).astimezone(pytz.utc)
            if candidate > now_utc:
                candidates.append(candidate)
        if candidates:
            return fields.Datetime.to_string(min(candidates).replace(tzinfo=None))
    return False


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
    customer_report_send_times = fields.Char(
        string="Daily Send Times",
        help="Separate 24-hour times (HH:MM) with commas. Leave empty to disable scheduled reports.",
    )
    customer_report_timezone = fields.Selection(
        [(tz, tz) for tz in pytz.common_timezones], string="Report Timezone", default="UTC", required=True,
    )
    required_shipment_field_ids = fields.Many2many(
        "ir.model.fields",
        string="Sale Order Required Fields",
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
        if {"customer_report_send_times", "customer_report_timezone"}.intersection(fields_list):
            schedule = json.loads(self.env["ir.config_parameter"].sudo().get_param(
                CUSTOMER_REPORT_SCHEDULE_PARAM, "{}",
            ))
            if "customer_report_send_times" in fields_list:
                values["customer_report_send_times"] = ", ".join(schedule.get("times", []))
            if "customer_report_timezone" in fields_list:
                values["customer_report_timezone"] = schedule.get("timezone", "UTC")
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
            raise ValidationError(
                _("Only the %s Shipment and Cargo fields can be configured.")
                % len(CONFIGURABLE_SHIPMENT_FIELDS)
            )
        self._save_customer_report_schedule()
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

    def _save_customer_report_schedule(self):
        raw = (self.customer_report_send_times or "").strip()
        times = sorted({value.strip() for value in raw.split(",")}) if raw else []
        if any(not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) for value in times):
            raise ValidationError(_("Enter daily send times as HH:MM, separated by commas."))

        params = self.env["ir.config_parameter"].sudo()
        previous = json.loads(params.get_param(CUSTOMER_REPORT_SCHEDULE_PARAM, "{}"))
        timezone = self.customer_report_timezone
        if times == previous.get("times", []) and timezone == previous.get("timezone", "UTC"):
            return
        params.set_param(CUSTOMER_REPORT_SCHEDULE_PARAM, json.dumps({
            "times": times,
            "timezone": timezone,
            "next_send_at": next_customer_report_at(times, timezone, fields.Datetime.now()),
        }))


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
