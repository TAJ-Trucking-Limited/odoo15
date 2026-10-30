"""Append-only operational updates and company-scoped inactivity reminders."""
from datetime import timedelta
import math

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize


class ResCompany(models.Model):
    _inherit = "res.company"

    transport_inactivity_enabled = fields.Boolean(string="Enable Trip Inactivity Alerts")
    transport_inactivity_hours = fields.Float(string="Inactivity Hours", default=24, required=True)
    transport_inactivity_recipient = fields.Char(string="Inactivity Alert Email")

    @api.constrains("transport_inactivity_enabled", "transport_inactivity_hours", "transport_inactivity_recipient")
    def _check_transport_inactivity_settings(self):
        for company in self:
            hours = company.transport_inactivity_hours
            if not math.isfinite(hours) or hours <= 0:
                raise ValidationError(_("Inactivity hours must be a positive number."))
            try:
                fields.Datetime.now() - timedelta(hours=hours)
            except OverflowError:
                raise ValidationError(_("The inactivity interval is too large.")) from None
            recipient = (company.transport_inactivity_recipient or "").strip()
            if recipient and not email_normalize(recipient):
                raise ValidationError(_("Enter one valid inactivity alert email address."))
            if company.transport_inactivity_enabled and not recipient:
                raise ValidationError(_("Set the recipient before enabling inactivity alerts."))


class TripActivity(models.Model):
    _name = "taj.transport.trip.activity"
    _description = "Trip Activity History"
    _order = "recorded_at desc, id desc"
    _check_company_auto = True

    _unique_source_event = models.Constraint(
        "UNIQUE(source_event_id)", "This milestone event is already recorded as an activity.",
    )
    trip_id = fields.Many2one("taj.transport.trip", required=True, ondelete="restrict", index=True, check_company=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True, index=True)
    activity_type = fields.Selection([
        ("manual", "Manual Update"), ("arrival", "Milestone Arrival"),
        ("departure", "Milestone Departure"),
    ], required=True)
    description = fields.Text(required=True)
    recorded_at = fields.Datetime(required=True, index=True)
    user_id = fields.Many2one("res.users", string="Recorded By", required=True, ondelete="restrict")
    milestone_id = fields.Many2one("taj.transport.milestone", ondelete="restrict", check_company=True)
    source_event_id = fields.Many2one("taj.transport.milestone.event", ondelete="restrict", check_company=True)
    occurred_at = fields.Datetime(string="Milestone Event Time")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Use Record Activity or a milestone action to record trip activity."))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Trip activity history cannot be edited."))

    def unlink(self):
        raise AccessError(_("Trip activity history cannot be deleted."))


class TransportTrip(models.Model):
    _inherit = "taj.transport.trip"

    trip_activity_ids = fields.One2many("taj.transport.trip.activity", "trip_id", string="Activity History", readonly=True, copy=False)
    last_activity_at = fields.Datetime(string="Last Activity Recorded", readonly=True, copy=False, index=True)
    inactivity_reference_at = fields.Datetime(compute="_compute_inactivity_reference_at", store=True, index=True)
    last_inactivity_alert_at = fields.Datetime(string="Last Inactivity Alert Queued", readonly=True, copy=False)

    @api.depends("started_at", "create_date", "last_activity_at")
    def _compute_inactivity_reference_at(self):
        for trip in self:
            baseline = trip.started_at or trip.create_date
            trip.inactivity_reference_at = max(
                filter(None, [baseline, trip.last_activity_at]),
                default=False,
            )

    def action_open_record_activity(self):
        self.ensure_one()
        self.check_access("write")
        if self.state not in ("draft", "ready", "in_progress"):
            raise UserError(_("Activities can only be recorded on open trips."))
        return {
            "type": "ir.actions.act_window", "name": _("Record Activity"),
            "res_model": "taj.transport.trip.activity.wizard",
            "view_mode": "form", "target": "new",
            "context": {"default_trip_id": self.id},
        }

    def _record_trip_activity(self, description, source_event=None):
        self.ensure_one()
        self.check_access("write")
        self._lock_for_transition()
        if self.state not in ("draft", "ready", "in_progress"):
            raise UserError(_("Activities can only be recorded on open trips."))
        description = (description or "").strip()
        if not description:
            raise ValidationError(_("Enter an activity description."))
        now = fields.Datetime.now()
        vals = {
            "trip_id": self.id, "activity_type": "manual",
            "description": description, "recorded_at": now, "user_id": self.env.user.id,
        }
        if source_event:
            if source_event.trip_id != self or source_event.event_type not in ("arrival", "departure"):
                raise ValidationError(_("The activity must reference an arrival or departure on this trip."))
            vals.update({
                "activity_type": source_event.event_type,
                "milestone_id": source_event.milestone_id.id,
                "source_event_id": source_event.id,
                "occurred_at": source_event.occurred_at,
            })
        activity = self.env["taj.transport.trip.activity"].sudo().create(vals)
        super(TransportTrip, self).write({"last_activity_at": now})
        return activity

    @api.model_create_multi
    def create(self, vals_list):
        self._check_activity_clock_values(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._check_activity_clock_values([vals])
        return super().write(vals)

    @api.model
    def _check_activity_clock_values(self, vals_list):
        if any({"last_activity_at", "last_inactivity_alert_at", "inactivity_reference_at", "trip_activity_ids"}.intersection(vals) for vals in vals_list):
            raise UserError(_("Activity history and alert timestamps are system-managed."))

    def _inactivity_email_body(self, company, now, hours):
        rows = []
        for trip in self:
            last = trip.inactivity_reference_at
            rows.append(Markup("<tr><td><a href='{url}'>{trip}</a></td><td>{order}</td><td>{customer}</td><td>{truck}</td><td>{last} UTC</td><td>{idle:.1f}</td></tr>").format(
                url="%s/odoo/taj.transport.trip/%s" % (trip.get_base_url(), trip.id),
                trip=trip.name, order=trip.sale_order_id.name,
                customer=trip.partner_id.display_name,
                truck=trip.current_vehicle_id.license_plate or _("Unassigned"),
                last=fields.Datetime.to_string(last), idle=(now-last).total_seconds()/3600,
            ))
        return Markup("<p>{company}: In Progress trips with no activity for more than {hours:g} hours.</p><table border='1' cellpadding='5'><thead><tr><th>Trip</th><th>Sales Order</th><th>Customer</th><th>Truck</th><th>Last Activity / Trip Start</th><th>Inactive Hours</th></tr></thead><tbody>{rows}</tbody></table><p>Activity means a submitted manual update or a confirmed milestone arrival/departure.</p>").format(
            company=company.name, hours=hours, rows=Markup("").join(rows),
        )

    @api.model
    def _cron_queue_inactivity_alerts(self):
        """One digest per company; advance reminder clocks only after queuing."""
        now = fields.Datetime.now()
        companies = self.env["res.company"].sudo().search([("transport_inactivity_enabled", "=", True)])
        for company in companies:
            hours = company.transport_inactivity_hours
            recipient = email_normalize(company.transport_inactivity_recipient or "")
            if not recipient or not math.isfinite(hours) or hours <= 0:
                continue
            try:
                cutoff = now - timedelta(hours=hours)
            except OverflowError:
                continue
            Trip = self.sudo().with_company(company).with_context(allowed_company_ids=company.ids)
            candidates = Trip.search([
                ("company_id", "=", company.id), ("state", "=", "in_progress"),
                ("inactivity_reference_at", "<", cutoff),
                "|", ("last_inactivity_alert_at", "=", False),
                ("last_inactivity_alert_at", "<=", cutoff),
            ], order="id")
            due = Trip.browse()
            for trip in candidates:
                trip._lock_for_transition()
                trip.invalidate_recordset(["last_activity_at", "started_at", "inactivity_reference_at", "last_inactivity_alert_at"])
                if (trip.state == "in_progress" and trip.inactivity_reference_at
                        and trip.inactivity_reference_at < cutoff
                        and (not trip.last_inactivity_alert_at or trip.last_inactivity_alert_at <= cutoff)):
                    due |= trip
            if not due:
                continue
            self.env["mail.mail"].sudo().with_company(company).create({
                "subject": _("Trip inactivity alert — %s") % company.name,
                "email_to": recipient,
                "email_from": company.partner_id.email_formatted or self.env.user.partner_id.email_formatted,
                "body_html": due._inactivity_email_body(company, now, hours),
                "auto_delete": False,
            })
            super(TransportTrip, due).write({"last_inactivity_alert_at": now})


class TransportMilestone(models.Model):
    _inherit = "taj.transport.milestone"

    def _log_event(self, event_type, **kwargs):
        event = super()._log_event(event_type, **kwargs)
        if event_type in ("arrival", "departure"):
            description = _("%(event)s: %(checkpoint)s") % {
                "event": _("Arrival") if event_type == "arrival" else _("Departure"),
                "checkpoint": self.name,
            }
            if event.note:
                description += "\n" + event.note
            self.trip_id._record_trip_activity(description, source_event=event)
        return event
