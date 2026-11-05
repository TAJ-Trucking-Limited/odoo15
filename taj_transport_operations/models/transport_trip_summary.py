"""Read-only shipment details and operational indicators for dispatch reporting."""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class TransportTripSummary(models.Model):
    _inherit = "taj.transport.trip"

    transport_file_id = fields.Many2one(related="sale_line_id.transport_file_id", string="File Number")
    file_type_id = fields.Many2one(related="sale_line_id.file_type_id")
    clearing_service = fields.Selection(related="sale_line_id.clearing_service")
    product_id = fields.Many2one(related="sale_line_id.product_id", string="Product")
    eta = fields.Datetime(related="sale_line_id.eta", string="ETA (Shipment Arrival)")
    etol = fields.Date(related="sale_line_id.etol", string="ETOL (Estimated Loading Date)")
    allocation_date = fields.Datetime(related="sale_line_id.allocation_date")
    last_milestone_id = fields.Many2one("taj.transport.milestone", compute="_compute_last_milestone")
    last_milestone_activity = fields.Char(string="Last Milestone", compute="_compute_last_milestone")
    last_milestone_at = fields.Datetime(string="Last Milestone Date", compute="_compute_last_milestone")
    activity_status = fields.Selection(
        [("not_started", "Not Started"), ("recent", "Recent Activity"),
         ("overdue", "Activity Overdue"), ("closed", "Closed")],
        string="Activity Status", compute="_compute_activity_status",
    )

    @api.depends("milestone_ids.name", "milestone_ids.state", "milestone_ids.actual_arrival_at", "milestone_ids.actual_departure_at")
    def _compute_last_milestone(self):
        for trip in self:
            recorded = trip.milestone_ids.filtered(
                lambda checkpoint: checkpoint.state not in ("pending", "skipped")
                and (checkpoint.actual_departure_at or checkpoint.actual_arrival_at)
            ).sorted(key=lambda checkpoint: (
                checkpoint.actual_departure_at or checkpoint.actual_arrival_at, checkpoint.sequence, checkpoint.id,
            ))
            latest = recorded[-1:]
            trip.last_milestone_id = latest
            trip.last_milestone_at = latest.actual_departure_at or latest.actual_arrival_at
            trip.last_milestone_activity = (
                (_("Departed %s") if latest.actual_departure_at else _("Arrived at %s")) % latest.name
                if latest else False
            )

    @api.depends("state", "actual_departure_at", "last_activity_at", "company_id.transport_inactivity_hours")
    def _compute_activity_status(self):
        now = fields.Datetime.now()
        for trip in self:
            if trip.state in ("completed", "cancelled"):
                trip.activity_status = "closed"
            elif trip.state != "in_progress":
                trip.activity_status = "not_started"
            else:
                baseline = max(filter(None, [trip.actual_departure_at or trip.create_date, trip.last_activity_at]))
                threshold = now - timedelta(hours=trip.company_id.transport_inactivity_hours or 24)
                trip.activity_status = "overdue" if baseline <= threshold else "recent"

    def write(self, vals):
        if "actual_departure_at" not in vals:
            return super().write(vals)
        # Corrections use the same serialization and timeline as checkpoint actions.
        for trip in self.sorted("id"):
            trip._lock_for_transition()
        departure = fields.Datetime.to_datetime(vals["actual_departure_at"]) if vals["actual_departure_at"] else False
        for trip in self:
            if trip.state == "in_progress":
                if not departure or departure > fields.Datetime.now():
                    raise ValidationError(_("An active trip needs an actual start that is not in the future."))
                start_assignment = trip.assignment_ids.filtered(
                    lambda assignment: assignment.effective_from <= trip.actual_departure_at
                    and (not assignment.effective_to or assignment.effective_to > trip.actual_departure_at)
                ).sorted(key=lambda assignment: (assignment.effective_from, assignment.id), reverse=True)[:1]
                if start_assignment and (
                    departure < start_assignment.effective_from
                    or (start_assignment.effective_to and departure >= start_assignment.effective_to)
                ):
                    raise ValidationError(_("Actual start must remain within the resource assignment covering the original start."))
        result = super().write(vals)
        for trip in self:
            trip._validate_milestone_timeline()
        return result
