"""Audited atomic replacement on a Draft, Ready or In Progress trip."""
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError


class TajTransportReplaceAssignmentWizard(models.TransientModel):
    _name = "taj.transport.replace.assignment.wizard"
    _description = "Replace Trip Resources"

    trip_id = fields.Many2one(
        "taj.transport.trip", string="Trip", required=True, readonly=True,
    )
    expected_assignment_id = fields.Many2one(
        "taj.transport.assignment", string="Assignment Being Replaced",
        required=True, readonly=True,
        help="Reject outdated replacement dialogs so no newer assignment is lost.",
    )
    company_id = fields.Many2one(
        "res.company", related="trip_id.company_id", readonly=True,
    )
    vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Truck",
        domain=lambda self: "[('active', '=', True), '|', ('category_id', '=', False), ('category_id', '=', %s), '|', "
                            "('company_id', '=', False), ('company_id', '=', company_id)]" %
                            self.env.ref("taj_transport_operations.fleet_category_truck").id,
    )
    trailer_id = fields.Many2one(
        "fleet.vehicle", string="Trailer",
        domain=lambda self: "[('category_id', '=', %s), ('active', '=', True), '|', "
                            "('company_id', '=', False), ('company_id', '=', company_id)]" %
                            self.env.ref("taj_transport_operations.fleet_category_trailer").id,
    )
    driver_id = fields.Many2one("res.partner", string="Driver")
    co_driver_id = fields.Many2one("res.partner", string="Co-driver")
    effective_from = fields.Datetime(
        string="Effective From", required=True, default=fields.Datetime.now,
    )
    effective_location = fields.Char(string="Location of Replacement")
    reason = fields.Char(string="Replacement Reason", required=True)
    notes = fields.Text(string="Replacement Notes")

    def action_replace_resources(self):
        self.ensure_one()
        trip = self.trip_id.exists()
        if not trip:
            raise UserError(_("The trip no longer exists."))
        trip.check_access("write")

        # Lock the trip, not just the currently open row: replacements on the
        # same trip serialize even when the open assignment changes. The
        # transaction is rolled back in full if any step below fails.
        self.env["taj.transport.assignment"].flush_model([
            "trip_id", "effective_from", "effective_to",
        ])
        self.env.cr.execute(
            "SELECT id FROM taj_transport_trip WHERE id = %s FOR UPDATE",
            (trip.id,),
        )
        trip.invalidate_recordset(["state", "current_assignment_id"])
        if trip.state not in ("draft", "ready", "in_progress"):
            raise UserError(_(
                "Resources cannot be replaced on completed or cancelled trips."
            ))

        # Query the database *after* obtaining the lock; the cached assignment
        # in an older wizard is not a reliable concurrency check.
        self.env.cr.execute(
            "SELECT id FROM taj_transport_assignment "
            "WHERE trip_id = %s AND effective_to IS NULL FOR UPDATE",
            (trip.id,),
        )
        current_ids = [row[0] for row in self.env.cr.fetchall()]
        if len(current_ids) != 1:
            raise UserError(_(
                "Expected one active resource assignment for this trip. "
                "Refresh the trip and review its assignment history."
            ))
        current = self.env["taj.transport.assignment"].browse(current_ids[0])
        if current.id != self.expected_assignment_id.id:
            raise UserError(_(
                "The resources changed after this dialog was opened. "
                "Close it and open Replace Resources again."
            ))
        current.check_access("read")
        current.invalidate_recordset([
            "effective_from", "effective_to", "vehicle_id", "trailer_id",
            "driver_id", "co_driver_id",
        ])
        effective_from = fields.Datetime.to_datetime(self.effective_from)
        if not effective_from:
            raise ValidationError(_("Enter the effective replacement time."))
        if effective_from > fields.Datetime.now():
            raise ValidationError(_(
                "Future resource replacements cannot be made active immediately."
            ))
        if effective_from <= current.effective_from:
            raise ValidationError(_(
                "The replacement must start after the active assignment began."
            ))
        if not (self.reason or "").strip():
            raise ValidationError(_("A reason is required for each replacement."))

        new_resources = {
            "vehicle_id": self.vehicle_id.id or False,
            "trailer_id": self.trailer_id.id or False,
            "driver_id": self.driver_id.id or False,
            "co_driver_id": self.co_driver_id.id or False,
        }
        if not any(new_resources.values()):
            raise ValidationError(_(
                "At least one truck, trailer or driver must remain assigned."
            ))
        if trip.state in ("ready", "in_progress") and (
            not new_resources["vehicle_id"] or not new_resources["driver_id"]
        ):
            raise ValidationError(_(
                "Ready and In Progress trips require a truck and primary driver."
            ))
        if (
            trip.state == "in_progress"
            and trip.actual_departure_at
            and effective_from < trip.actual_departure_at
        ):
            raise ValidationError(_(
                "An in-progress resource replacement cannot precede "
                "the actual departure."
            ))
        if all(
            new_resources[field] == (getattr(current, field).id or False)
            for field in new_resources
        ):
            raise ValidationError(_(
                "Change at least one resource before saving a replacement."
            ))

        # Close only the old effective period, then create a fresh immutable
        # row. The private model methods preserve ACLs, company/category
        # checks, chronology and original Sales/Invoice data.
        current._close_for_replacement(effective_from)
        new_assignment = self.env[
            "taj.transport.assignment"
        ]._create_from_replacement({
            "trip_id": trip.id,
            **new_resources,
            "effective_from": effective_from,
            "effective_location": self.effective_location,
            "reason": self.reason.strip(),
            "notes": self.notes,
        })
        trip.message_post(
            body=Markup("<p>%s</p><p>%s: %s</p><p>%s: %s</p>") % (
                _("Trip resources replaced. Assignment history is preserved."),
                _("Effective from"),
                fields.Datetime.context_timestamp(
                    self, effective_from
                ).strftime("%Y-%m-%d %H:%M"),
                _("Reason"), new_assignment.reason,
            ),
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
