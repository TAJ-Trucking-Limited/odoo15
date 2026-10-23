"""Transport categories on the existing Fleet source of truth."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..hooks import ensure_transport_categories


class FleetVehicleModelCategory(models.Model):
    _inherit = "fleet.vehicle.model.category"

    @api.model
    def _ensure_transport_categories(self):
        ensure_transport_categories(self.env)


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    is_transport_trailer = fields.Boolean(compute="_compute_is_transport_trailer")
    transport_truck_assignment_ids = fields.One2many(
        "taj.transport.assignment", "vehicle_id", readonly=True,
        domain=[("effective_to", "=", False)],
        groups="taj_transport_operations.group_transport_user",
    )
    transport_trailer_assignment_ids = fields.One2many(
        "taj.transport.assignment", "trailer_id", readonly=True,
        domain=[("effective_to", "=", False)],
        groups="taj_transport_operations.group_transport_user",
    )
    transport_initial_trip_ids = fields.One2many(
        "taj.transport.trip", "initial_vehicle_id", readonly=True,
        groups="taj_transport_operations.group_transport_user",
    )
    transport_availability_state = fields.Selection(
        [("assigned", "Assigned"), ("busy", "Busy"), ("standby", "Standby")],
        string="Availability", compute="_compute_transport_availability_state",
        store=True, index=True, compute_sudo=True,
        groups="taj_transport_operations.group_transport_user",
        help="Draft trips reserve their original truck until resources are "
             "assigned. Current Draft/Ready assignments are Assigned; "
             "In Progress assignments are Busy. Busy takes priority.",
    )
    transport_trip_ids = fields.Many2many(
        "taj.transport.trip", string="Active Trips",
        compute="_compute_transport_trip_ids", compute_sudo=False,
        groups="taj_transport_operations.group_transport_user",
        help="Active trips you can access in your selected companies. "
             "Availability also accounts for assignments in other companies "
             "when a vehicle is shared; their trip details remain private.",
    )

    @api.depends(
        "transport_truck_assignment_ids.effective_to",
        "transport_truck_assignment_ids.trip_id.state",
        "transport_trailer_assignment_ids.effective_to",
        "transport_trailer_assignment_ids.trip_id.state",
        "transport_initial_trip_ids.state",
        "transport_initial_trip_ids.assignment_ids",
    )
    def _compute_transport_availability_state(self):
        # Compute the status across companies so a shared vehicle cannot
        # appear free merely because its trip is hidden from the reader.
        by_vehicle = {vehicle.id: set() for vehicle in self}
        assignments = self.env["taj.transport.assignment"].sudo().search([
            ("effective_to", "=", False),
            ("trip_id.state", "in", ("draft", "ready", "in_progress")),
            "|", ("vehicle_id", "in", self.ids), ("trailer_id", "in", self.ids),
        ])
        for assignment in assignments:
            for vehicle_id in (assignment.vehicle_id.id, assignment.trailer_id.id):
                if vehicle_id in by_vehicle:
                    by_vehicle[vehicle_id].add(assignment.trip_id.state)
        reservations = self.env["taj.transport.trip"].sudo().search([
            ("state", "=", "draft"),
            ("initial_vehicle_id", "in", self.ids),
            ("assignment_ids", "=", False),
        ])
        for trip in reservations:
            by_vehicle[trip.initial_vehicle_id.id].add("draft")
        for vehicle in self:
            states = by_vehicle[vehicle.id]
            if "in_progress" in states:
                vehicle.transport_availability_state = "busy"
            elif states:
                vehicle.transport_availability_state = "assigned"
            else:
                vehicle.transport_availability_state = "standby"

    @api.depends(
        "transport_truck_assignment_ids.effective_to",
        "transport_truck_assignment_ids.trip_id.state",
        "transport_trailer_assignment_ids.effective_to",
        "transport_trailer_assignment_ids.trip_id.state",
        "transport_initial_trip_ids.state",
        "transport_initial_trip_ids.assignment_ids",
    )
    @api.depends_context("uid", "company", "allowed_company_ids")
    def _compute_transport_trip_ids(self):
        Trip = self.env["taj.transport.trip"]
        trips = Trip.search([
            ("state", "in", ("draft", "ready", "in_progress")),
            "|", "|",
            ("current_assignment_id.vehicle_id", "in", self.ids),
            ("current_assignment_id.trailer_id", "in", self.ids),
            "&", "&",
            ("state", "=", "draft"),
            ("initial_vehicle_id", "in", self.ids),
            ("assignment_ids", "=", False),
        ])
        by_vehicle = {vehicle.id: [] for vehicle in self}
        for trip in trips:
            assignment = trip.current_assignment_id
            if assignment:
                resource_ids = (assignment.vehicle_id.id, assignment.trailer_id.id)
            else:
                resource_ids = (trip.initial_vehicle_id.id,)
            for vehicle_id in resource_ids:
                if vehicle_id in by_vehicle:
                    by_vehicle[vehicle_id].append(trip.id)
        for vehicle in self:
            vehicle.transport_trip_ids = Trip.browse(by_vehicle[vehicle.id])

    def action_open_transport_trips(self):
        self.ensure_one()
        self.check_access("read")
        action = self.env.ref("taj_transport_operations.action_transport_trip").read()[0]
        action["domain"] = [("id", "in", self.transport_trip_ids.ids)]
        return action

    @api.depends("category_id")
    def _compute_is_transport_trailer(self):
        category = self.env.ref("taj_transport_operations.fleet_category_trailer",
                                raise_if_not_found=False)
        for vehicle in self:
            vehicle.is_transport_trailer = bool(category and vehicle.category_id == category)

    @api.model
    def _transport_resource_domain(self, kind):
        category = self.env.ref("taj_transport_operations.fleet_category_" + kind)
        return [("category_id", "=", category.id), ("active", "=", True)]

    @api.model_create_multi
    def create(self, vals_list):
        # Availability is derived even when a vehicle is created through RPC.
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            vals.pop("transport_availability_state", None)
        return super().create(vals_list)

    def write(self, vals):
        if "transport_availability_state" in vals:
            raise ValidationError(_(
                "Availability is calculated from trips and cannot be edited."
            ))
        if "company_id" in vals:
            self.check_access("write")
            changing = self.filtered(lambda v: v.company_id.id != vals["company_id"])
            if changing and self.env["taj.transport.assignment"].sudo().search_count([
                "|", ("vehicle_id", "in", changing.ids), ("trailer_id", "in", changing.ids),
            ]):
                raise ValidationError(_(
                    "The company cannot change for a truck or trailer with trip assignment history."
                ))
        result = super().write(vals)
        if {"category_id", "model_id"}.intersection(vals):
            assignments = self.env["taj.transport.assignment"].sudo()
            for kind, field in (("truck", "vehicle_id"), ("trailer", "trailer_id")):
                category = self.env.ref("taj_transport_operations.fleet_category_" + kind,
                                        raise_if_not_found=False)
                if category:
                    invalid = self.filtered(lambda v: v.category_id != category)
                    if invalid and assignments.search_count([(field, "in", invalid.ids)]):
                        raise ValidationError(_(
                            "The category/model cannot change the resource type of an assigned vehicle."
                        ))
        return result
