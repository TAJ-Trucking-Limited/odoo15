"""Explicit one-time resource assignment for a Draft transport trip."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TajTransportInitialAssignmentWizard(models.TransientModel):
    _name = "taj.transport.initial.assignment.wizard"
    _description = "Assign Initial Trip Resources"

    trip_id = fields.Many2one(
        "taj.transport.trip", string="Trip", required=True,
    )
    available_trip_ids = fields.Many2many(
        "taj.transport.trip", compute="_compute_available_trip_ids",
    )
    sale_order_id = fields.Many2one(related="trip_id.sale_order_id", readonly=True)

    @api.depends("vehicle_id")
    def _compute_available_trip_ids(self):
        for wizard in self:
            domain = [("state", "=", "draft"), ("assignment_ids", "=", False)]
            if wizard.vehicle_id.company_id:
                domain.append(("company_id", "=", wizard.vehicle_id.company_id.id))
            reservations = self.env["taj.transport.trip"].sudo().search([
                ("state", "=", "draft"), ("assignment_ids", "=", False),
                ("initial_vehicle_id", "=", wizard.vehicle_id.id),
            ]) if wizard.vehicle_id else self.env["taj.transport.trip"]
            if reservations:
                domain.append(("id", "in", reservations.ids))
            wizard.available_trip_ids = self.env["taj.transport.trip"].search(domain)
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
        compute="_compute_driver_id", store=True, readonly=False, precompute=True,
        help="Defaults to the selected truck's driver in Fleet. You can choose "
             "another driver for this trip.",
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

    @api.depends("vehicle_id")
    def _compute_driver_id(self):
        for wizard in self:
            wizard.driver_id = wizard.vehicle_id.driver_id

    def action_assign_initial_resources(self):
        self.ensure_one()
        trip = self.trip_id
        trip.check_access("write")
        trip._lock_for_transition()
        trip.invalidate_recordset(["state", "assignment_ids", "current_assignment_id"])
        if not trip.exists() or trip.state != "draft":
            raise UserError(_("Choose an existing Draft trip."))
        if trip.current_assignment_id:
            raise UserError(_(
                "This trip is already assigned. Replacement is handled "
                "separately to preserve the history."
            ))
        if self.env.context.get("select_trip_for_vehicle"):
            vehicle = self.vehicle_id
            vehicle.check_access("read")
            vehicle._lock_for_transport_assignment()
            if (not vehicle or not vehicle.active
                    or vehicle.category_id != self.env.ref("taj_transport_operations.fleet_category_truck")):
                raise UserError(_("Select an active Truck."))
            # Check all companies without exposing another company's trip.
            occupied = self.env["taj.transport.assignment"].sudo().search_count([
                ("effective_to", "=", False),
                ("trip_id.state", "in", ["draft", "ready", "in_progress"]),
                "|", ("vehicle_id", "=", vehicle.id), ("trailer_id", "=", vehicle.id),
            ])
            reserved = self.env["taj.transport.trip"].sudo().search_count([
                ("state", "=", "draft"), ("assignment_ids", "=", False),
                ("initial_vehicle_id", "=", vehicle.id), ("id", "!=", trip.id),
            ])
            if occupied or reserved:
                raise UserError(_("This truck is assigned or reserved for another trip. Refresh Vehicle Availability."))
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
