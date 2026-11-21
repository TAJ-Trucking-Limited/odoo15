"""Customer trip status reports and the trip's current position preview."""

import json
import logging
from io import BytesIO

import pytz
import xlsxwriter
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import email_normalize

from .transport_settings import CUSTOMER_REPORT_SCHEDULE_PARAM, next_customer_report_at

_logger = logging.getLogger(__name__)
CUSTOMER_REPORT_FILENAME = "Trip_Status_{:%Y-%m-%d_%H%M%S}.xlsx"
REPORT_XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ResPartner(models.Model):
    _inherit = "res.partner"

    transport_report_additional_emails = fields.Char(
        string="Additional Emails",
        copy=False,
        help="Comma-separated CC addresses for this customer's trip status report.",
    )

    @api.constrains("transport_report_additional_emails")
    def _check_transport_report_additional_emails(self):
        for partner in self:
            partner._transport_report_cc_addresses()

    def _transport_report_cc_addresses(self):
        self.ensure_one()
        value = self.transport_report_additional_emails or ""
        if "\r" in value or "\n" in value:
            raise ValidationError(_("Additional Emails must be a comma-separated list of addresses."))
        addresses = [email_normalize(item.strip()) for item in value.split(",") if item.strip()]
        if not all(addresses):
            raise ValidationError(_("Enter a valid email address for each Additional Emails entry."))
        return list(dict.fromkeys(addresses))

    def _customer_trip_report_recipients(self):
        self.ensure_one()
        email = self.email or ""
        primary = email_normalize(email) if "\r" not in email and "\n" not in email else False
        return primary, [address for address in self._transport_report_cc_addresses() if address != primary]


class TajTransportTrip(models.Model):
    _inherit = "taj.transport.trip"

    commercial_partner_id = fields.Many2one(
        "res.partner",
        string="Customer Commercial Entity",
        related="partner_id.commercial_partner_id",
        store=True,
        readonly=True,
        index=True,
    )

    email_dispatch_ids = fields.One2many(
        "taj.transport.trip.email.dispatch",
        "trip_id",
        string="Email Dispatches",
        readonly=True,
        copy=False,
    )

    # Live position preview on the trip form
    email_position_preview = fields.Char(
        string="Position Preview",
        compute="_compute_email_position_preview",
    )
    email_position_source = fields.Char(
        string="Position Source",
        compute="_compute_email_position_preview",
    )
    email_position_updated_at = fields.Datetime(
        string="Position Recorded At",
        compute="_compute_email_position_preview",
    )
    email_position_map_url = fields.Char(
        string="Map Link",
        compute="_compute_email_position_preview",
    )
    email_position_notice = fields.Char(
        string="Position Notice",
        compute="_compute_email_position_preview",
    )

    def _get_email_update_position_data(self):
        """Return structured position dictionary.

        Base implementation falls back to the latest confirmed milestone arrival/departure,
        or the trip's planned origin. Overridden in fleet_navirec when live GPS is available.
        """
        self.ensure_one()
        # Find latest actual milestone event
        milestones = self.milestone_ids.filtered(
            lambda m: m.state not in ("skipped", "pending") and (m.actual_departure_at or m.actual_arrival_at)
        )
        if milestones:
            # Sort by actual departure or arrival time descending
            latest = milestones.sorted(
                key=lambda m: (m.actual_departure_at or m.actual_arrival_at, m.sequence, m.id),
                reverse=True,
            )[0]
            if latest.actual_departure_at:
                event_type = _("Departed")
                event_time = latest.actual_departure_at
            else:
                event_type = _("Arrived at")
                event_time = latest.actual_arrival_at
            return {
                "label": f"{event_type} {latest.name}",
                "source": "milestone",
                "updated_at": event_time,
                "latitude": latest.latitude,
                "longitude": latest.longitude,
                "map_url": (
                    "https://www.google.com/maps?q=%s,%s" % (latest.latitude, latest.longitude)
                    # Optional Float fields use zero for missing coordinates.
                    # Suppress incomplete/ambiguous historical coordinate pairs.
                    if (latest.latitude and latest.longitude
                        and -90 <= latest.latitude <= 90 and -180 <= latest.longitude <= 180) else False
                ),
                "is_stale": False,
                "notice": _("Last recorded checkpoint (not a live GPS fix)"),
            }

        # Planned origin fallback
        origin = (self.origin_location or "").strip()
        return {
            "label": origin if origin else _("Origin location not specified"),
            "source": "planned",
            "updated_at": False,
            "latitude": False,
            "longitude": False,
            "map_url": False,
            "is_stale": False,
            "notice": _("Planned location, not a confirmed checkpoint or live position"),
        }

    def _compute_email_position_preview(self):
        for trip in self:
            pos = trip._get_email_update_position_data()
            trip.email_position_preview = pos.get("label") or ""
            trip.email_position_source = (pos.get("source") or "").title()
            trip.email_position_updated_at = pos.get("updated_at") or False
            trip.email_position_map_url = pos.get("map_url") or ""
            trip.email_position_notice = pos.get("notice") or ""

    def _customer_report_trips(self, customer=None):
        domain = [
            ("state", "=", "in_progress"),
            ("company_id", "in", self.env.companies.ids),
            ("commercial_partner_id", "!=", False),
        ]
        if customer:
            domain.append(("commercial_partner_id", "=", customer.id))
        return self.search(domain, order="company_id, id")

    def _prepare_customer_trip_report(self, timezone):
        tz = pytz.timezone(timezone)

        def local_time(value):
            return pytz.utc.localize(value).astimezone(tz) if value else None

        def format_time(value):
            return value.strftime("%Y-%m-%d %H:%M %Z") if value else ""

        trip_rows = []
        checkpoint_rows = []
        comparison_data = []
        checkpoint_names = {}
        checkpoint_status = dict(
            self.env["taj.transport.milestone"]._fields["state"]._description_selection(self.env)
        )
        trip_status = dict(self._fields["state"]._description_selection(self.env))
        for trip in self:
            position = trip._get_email_update_position_data()
            truck = trip.current_vehicle_id or trip.last_vehicle_id
            truck_name = truck.license_plate or truck.name or ""
            trip_rows.append([
                trip.name, trip_status[trip.state], trip.origin_location or "", trip.destination_location or "",
                trip.container_num or "", trip.cargo_type_id.name or "", trip.cargo_description or "",
                truck_name, position.get("label") or "", format_time(local_time(position.get("updated_at"))),
                position.get("notice") or "", position.get("map_url") or "",
            ])
            arrivals = {}
            for checkpoint in trip.milestone_ids.sorted("sequence"):
                planned_at = checkpoint.planned_arrival_at
                actual_at = checkpoint.actual_arrival_at
                planned = local_time(planned_at)
                actual = local_time(actual_at)
                checkpoint_rows.append([
                    trip.name, checkpoint.name, checkpoint_status[checkpoint.state],
                    format_time(planned), format_time(actual),
                    format_time(local_time(checkpoint.actual_departure_at)),
                ])
                key = checkpoint.name.strip().casefold()
                checkpoint_names.setdefault(key, checkpoint.name)
                arrivals[key] = (
                    planned.replace(tzinfo=None) if planned else None,
                    actual.replace(tzinfo=None) if actual else None,
                    (actual_at - planned_at).total_seconds() / 3600 if planned_at and actual_at else None,
                )
            comparison_data.append((truck_name, trip.name, arrivals))

        comparison_rows = []
        for truck, trip_name, arrivals in comparison_data:
            row = [truck, trip_name]
            for key in checkpoint_names:
                row.extend(arrivals.get(key, (None, None, None)))
            comparison_rows.append(row)

        return {
            "trips": {
                "headers": [
                    _("Trip Reference"), _("Status"), _("Origin"), _("Destination"),
                    _("Container Number"), _("Cargo Type"), _("Cargo Description"),
                    _("Assigned Truck"), _("Current / Last-Known Position"),
                    _("Recorded At"), _("Position Note"), _("Map"),
                ],
                "rows": trip_rows,
            },
            "checkpoints": {
                "headers": [
                    _("Trip Reference"), _("Checkpoint"), _("Status"),
                    _("Planned Arrival"), _("Actual Arrival"), _("Actual Departure"),
                ],
                "rows": checkpoint_rows,
            },
            "comparison": {
                "headers": [_("Truck"), _("Trip Reference")] + [
                    _("Planned Arrival"), _("Actual Arrival"), _("Difference (hours)"),
                ] * len(checkpoint_names),
                "groups": list(checkpoint_names.values()),
                "time_heading": _("Times shown in %s") % timezone,
                "rows": comparison_rows,
            },
        }

    def _render_customer_trip_report(self, timezone):
        return self._render_customer_trip_workbook(self._prepare_customer_trip_report(timezone))

    def _render_customer_trip_workbook(self, report, comparison_only=False):
        output = BytesIO()
        with xlsxwriter.Workbook(output, {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "default_date_format": "yyyy-mm-dd hh:mm",
        }) as workbook:
            header = workbook.add_format({"bold": True})
            if not comparison_only:
                trips = workbook.add_worksheet(_("Trips"))
                trips.set_column(0, 7, 22)
                trips.set_column(8, 11, 36)

                checkpoints = workbook.add_worksheet(_("Checkpoints"))
                checkpoints.set_column(0, 5, 27)

                for sheet, table in ((trips, report["trips"]), (checkpoints, report["checkpoints"])):
                    sheet.write_row(0, 0, table["headers"], header)
                    for row, values in enumerate(table["rows"], start=1):
                        sheet.write_row(row, 0, values)
                    sheet.freeze_panes(1, 0)
                    sheet.autofilter(0, 0, len(table["rows"]), len(table["headers"]) - 1)

            self._write_milestone_comparison(workbook, report["comparison"], header)
        return output.getvalue()

    def _write_milestone_comparison(self, workbook, table, header):
        sheet = workbook.add_worksheet(_("Milestone Comparison"))
        group_header = workbook.add_format({"bold": True, "align": "center"})
        difference_format = workbook.add_format({"num_format": "+0.00;-0.00;0.00"})
        sheet.merge_range(0, 0, 0, 1, table["time_heading"], header)
        sheet.write_row(1, 0, table["headers"], header)
        sheet.set_column(0, 1, 24)
        sheet.freeze_panes(2, 2)

        for index, name in enumerate(table["groups"]):
            column = 2 + index * 3
            sheet.merge_range(0, column, 0, column + 2, name, group_header)
            sheet.set_column(column, column + 1, 22)
            sheet.set_column(column + 2, column + 2, 22, difference_format)
        for row, values in enumerate(table["rows"], start=2):
            sheet.write_row(row, 0, values)

    def _queue_customer_trip_report(self, timezone, dispatch_type):
        customer = self.commercial_partner_id
        primary, cc = customer._customer_trip_report_recipients()
        if not primary:
            raise UserError(_("Set a valid email on the customer record for %s.") % customer.display_name)

        now = fields.Datetime.now()
        attachment = self.env["ir.attachment"].sudo().create({
            "name": CUSTOMER_REPORT_FILENAME.format(now),
            "raw": self._render_customer_trip_report(timezone),
            "mimetype": REPORT_XLSX_MIMETYPE,
        })
        company = self[0].company_id
        mail = self.env["mail.mail"].sudo().with_company(company).create({
            "subject": _("Trip Status Report - %s") % customer.display_name,
            "body_html": Markup("<p>{message}</p>").format(message=_(
                "The attached Excel report contains the current status of your %(count)s trips in progress.",
                count=len(self),
            )),
            "email_from": company.partner_id.email_formatted or self.env.user.partner_id.email_formatted,
            "email_to": primary,
            "email_cc": ", ".join(cc),
            "attachment_ids": [Command.link(attachment.id)],
            "auto_delete": False,
        })
        attachment.write({"res_model": "mail.message", "res_id": mail.mail_message_id.id})
        self.env["taj.transport.trip.email.dispatch"].sudo().create([{
            "trip_id": trip.id,
            "dispatch_type": dispatch_type,
            "queued_at": now,
            "requested_by_id": self.env.user.id if dispatch_type == "manual" else False,
            "recipient_emails": "\n".join([primary, *cc]),
            "mail_ids": [Command.link(mail.id)],
        } for trip in self])
        return mail

    def _customer_report_scope(self):
        self.ensure_one()
        if self.state != "in_progress":
            raise UserError(_("Open an In Progress trip to access its customer status report."))
        customer = self.commercial_partner_id
        if not customer:
            raise UserError(_("This trip needs a customer before a status report can be generated."))
        trips = self._customer_report_trips(customer)
        if self not in trips:
            raise UserError(_("This trip is outside the companies available for this report."))
        return trips, self._customer_report_timezone()

    def _customer_report_timezone(self):
        schedule = json.loads(self.env["ir.config_parameter"].sudo().get_param(
            CUSTOMER_REPORT_SCHEDULE_PARAM, "{}",
        ))
        return schedule.get("timezone", "UTC")

    def action_preview_customer_trip_report(self):
        self.check_access("read")
        trips, timezone = self._customer_report_scope()
        return trips._open_trip_report_preview(timezone)

    def action_preview_milestone_report(self):
        self.ensure_one()
        self.check_access("read")
        if self.state not in ("in_progress", "completed"):
            raise UserError(_("Open an In Progress or Completed trip to preview its milestone report."))
        return self._open_trip_report_preview(self._customer_report_timezone(), comparison_only=True)

    def _open_trip_report_preview(self, timezone, comparison_only=False):
        preview = self.env["taj.transport.customer.report.preview"]._create_preview(self, timezone, comparison_only)
        return {
            "type": "ir.actions.act_window",
            "name": _("Milestone Report Preview") if comparison_only else _("Customer Trip Report Preview"),
            "res_model": preview._name,
            "res_id": preview.id,
            "views": [(self.env.ref("taj_transport_operations.view_customer_trip_report_preview_form").id, "form")],
            "target": "new",
            "context": {"dialog_size": "extra-large"},
        }

    def action_send_email_update_manual(self):
        self.check_access("write")
        trips, timezone = self._customer_report_scope()
        trips._queue_customer_trip_report(timezone, "manual")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Customer Trip Report Queued"),
                "message": _("The Excel report for %s is queued.") % self.commercial_partner_id.display_name,
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def _cron_send_due_trip_email_updates(self):
        params = self.env["ir.config_parameter"].sudo()
        # Claim the schedule row for this transaction, so the slot and its
        # queued mail are committed together and a parallel run skips it.
        record = params.search([("key", "=", CUSTOMER_REPORT_SCHEDULE_PARAM)]).try_lock_for_update()
        if not record:
            return
        record.invalidate_recordset(["value"])
        schedule = json.loads(record.value)
        now = fields.Datetime.now()
        due = fields.Datetime.to_datetime(schedule.get("next_send_at"))
        if not due or due > now:
            return

        trip_model = self.with_context(allowed_company_ids=self.env.user.company_ids.ids)
        for customer, trips in trip_model._customer_report_trips().grouped("commercial_partner_id").items():
            try:
                with self.env.cr.savepoint():
                    trips._queue_customer_trip_report(schedule["timezone"], "automatic")
            except UserError as exc:
                _logger.warning("Customer trip report skipped for partner %s: %s", customer.id, exc)
            except Exception:
                _logger.exception("Could not queue customer trip report for partner %s", customer.id)

        schedule["next_send_at"] = next_customer_report_at(schedule["times"], schedule["timezone"], now)
        record.value = json.dumps(schedule)
