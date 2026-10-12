"""Explicit one-time resource assignment for a Draft transport trip."""
from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportInitialAssignmentWizard(models.TransientModel):
    _name = "taj.transport.initial.assignment.wizard"
    _description = "Assign Initial Trip Resources"

    trip_id = fields.Many2one(
        "taj.transport.trip", string="Trip", required=True,
        readonly=True,
    )
    vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Truck",
        domain=lambda self: "[('category_id', '=', %s), ('active', '=', True), '|', "
                            "('company_id', '=', False), ('company_id', '=', company_id)]" %
                            self.env.ref("taj_transport_operations.fleet_category_truck").id,
    )
    trailer_id = fields.Many2one(
        "fleet.vehicle", string="Trailer",
        domain=lambda self: "[('category_id', '=', %s), ('active', '=', True), '|', "
                            "('company_id', '=', False), ('company_id', '=', company_id)]" %
                            self.env.ref("taj_transport_operations.fleet_category_trailer").id,
    )
    company_id = fields.Many2one("res.company", related="trip_id.company_id", readonly=True)
    driver_id = fields.Many2one(
        "res.partner", string="Driver",
    )
    co_driver_id = fields.Many2one(
        "res.partner", string="Co-driver",
    )
    effective_from = fields.Datetime(
        string="Effective From", default=fields.Datetime.now,
        required=True,
    )
    effective_location = fields.Char(string="Effective Location")
    reason = fields.Char(
        string="Reason", default="Initial assignment", required=True,
    )
    notes = fields.Text(string="Notes")

    def action_assign_initial_resources(self):
        self.ensure_one()
        trip = self.trip_id
        if not trip.exists() or trip.state != "draft":
            raise UserError(_("Choose an existing Draft trip."))
        if trip.current_assignment_id:
            raise UserError(_(
                "This trip is already assigned. Replacement is handled "
                "separately to preserve the history."
            ))
        self.env["taj.transport.assignment"].create({
            "trip_id": trip.id,
            "vehicle_id": self.vehicle_id.id or False,
            "trailer_id": self.trailer_id.id or False,
            "driver_id": self.driver_id.id or False,
            "co_driver_id": self.co_driver_id.id or False,
            "effective_from": self.effective_from,
            "effective_location": self.effective_location,
            "reason": self.reason,
            "notes": self.notes,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
