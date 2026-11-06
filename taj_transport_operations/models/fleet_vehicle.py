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

    transport_group_last_activity_at = fields.Datetime(
        string="Last Activity Recorded",
        compute="_compute_transport_group_last_activity_at",
        groups="taj_transport_operations.group_transport_user",
        help="Most recent recorded activity among trips associated with "
             "this truck's visible milestones in your allowed companies.",
    )

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_transport_group_last_activity_at(self):
        # Use milestone vehicle assignments, not the vehicle's *current*
        # driver or the Trip's creation truck, so grouped historical rows
        # agree with the truck under which Odoo displays them. ORM record
        # rules and allowed companies apply; do not sudo this search.
        latest = {vehicle.id: False for vehicle in self}
        if self.ids:
            milestones = self.env["taj.transport.milestone"].search([
                ("vehicle_id", "in", self.ids),
                ("company_id", "in", self.env.companies.ids),
                ("trip_id.last_activity_at", "!=", False),
            ])
            for milestone in milestones:
                timestamp = milestone.trip_id.last_activity_at
                vehicle_id = milestone.vehicle_id.id
                current = latest[vehicle_id]
                if timestamp and (not current or timestamp > current):
                    latest[vehicle_id] = timestamp
        for vehicle in self:
            vehicle.transport_group_last_activity_at = latest[vehicle.id]

    transport_can_assign = fields.Boolean(
        compute="_compute_transport_can_assign",
        groups="taj_transport_operations.group_transport_user",
    )

    @api.depends(
        "active", "category_id", "transport_availability_state",
        "transport_initial_trip_ids.state", "transport_initial_trip_ids.assignment_ids",
    )
    @api.depends_context("uid", "company", "allowed_company_ids")
    def _compute_transport_can_assign(self):
        category = self.env.ref("taj_transport_operations.fleet_category_truck")
        for vehicle in self:
            vehicle.transport_can_assign = bool(
                vehicle.active and (not vehicle.category_id or vehicle.category_id == category)
                and vehicle.transport_availability_state == "standby"
            )

    transport_planned_sale_line_count = fields.Integer(
        string="Planned Sales Lines",
        compute="_compute_transport_planned_sale_line_count",
        groups="taj_transport_operations.group_transport_user",
        help="Confirmed Sales shipment lines already allocated to this truck "
             "but not yet turned into Trips. These are planning allocations, "
             "not active assignments; Availability reflects actual Trips.",
    )

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_transport_planned_sale_line_count(self):
        # Deliberately no sudo: a dispatcher only sees counts for Sales lines
        # permitted by their active companies and record rules.
        counts = {}
        if self.ids:
            grouped = self.env["sale.order.line"]._read_group([
                ("vehicle_id", "in", self.ids),
                ("order_id.state", "in", ("sale", "done")),
                ("transport_trip_ids", "=", False),
                ("is_downpayment", "=", False),
                ("product_id", "!=", False),
            ], ["vehicle_id"], ["__count"])
            counts = {vehicle.id: count for vehicle, count in grouped if vehicle}
        for vehicle in self:
            vehicle.transport_planned_sale_line_count = counts.get(vehicle.id, 0)

    def action_assign_transport_trip(self):
        """Open Sales-line allocation, never Trip resource assignment."""
        self.ensure_one()
        self.check_access("read")
        if not (
            self.env.su
            or self.env.user.has_group("taj_transport_operations.group_transport_user")
        ):
            raise ValidationError(_("Transport Operations access is required."))
        if not self.transport_can_assign:
            raise ValidationError(_("Choose an active standby Truck for Sales allocation."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Assign Truck to Sales Order"),
            "res_model": "taj.transport.vehicle.sale.allocation.wizard",
            "view_mode": "form", "target": "new",
            "context": {"default_vehicle_id": self.id},
        }

    def _lock_for_transport_assignment(self):
        """Serialize truck reservations and assignment dialogs per vehicle."""
        if self.ids:
            self.check_access("read")
            self.env.cr.execute(
                "SELECT id FROM fleet_vehicle WHERE id IN %s ORDER BY id FOR UPDATE",
                (tuple(self.ids),),
            )
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
        if kind == "truck":
            return [("active", "=", True), "|", ("category_id", "=", False), ("category_id", "=", category.id)]
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
                    invalid = self.filtered(
                        lambda v: v.category_id != category and (kind != "truck" or v.category_id)
                    )
                    if invalid and assignments.search_count([(field, "in", invalid.ids)]):
                        raise ValidationError(_(
                            "The category/model cannot change the resource type of an assigned vehicle."
                        ))
        return result
