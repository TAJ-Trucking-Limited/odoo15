"""Assign a planned truck to a sales order line without creating a trip."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class TajTransportAssignTruckWizard(models.TransientModel):
    _name = "taj.transport.assign.truck.wizard"
    _description = "Assign Truck to Sales Order Line"

    sale_line_id = fields.Many2one(
        "sale.order.line",
        string="Sales Order Line",
        required=True,
        readonly=True,
    )
    sale_order_id = fields.Many2one(
        related="sale_line_id.order_id",
        string="Sales Order",
        readonly=True,
    )
    product_id = fields.Many2one(
        related="sale_line_id.product_id",
        string="Product",
        readonly=True,
    )
    company_id = fields.Many2one(
        related="sale_line_id.company_id",
        string="Company",
        readonly=True,
    )
    current_vehicle_id = fields.Many2one(
        related="sale_line_id.vehicle_id",
        string="Current Truck",
        readonly=True,
    )
    vehicle_id = fields.Many2one(
        "fleet.vehicle",
        string="Truck",
        required=True,
        domain=lambda self: (
            "[('active', '=', True), '|', ('category_id', '=', False), "
            "('category_id', '=', %s), '|', ('company_id', '=', False), "
            "('company_id', '=', company_id)]"
            % self.env.ref("taj_transport_operations.fleet_category_truck").id
        ),
    )

    driver_id = fields.Many2one(
        "res.partner", string="Driver", compute="_compute_driver_id",
        store=True, readonly=False, precompute=True,
        domain="[('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="Defaults to the Fleet truck driver. You can choose another driver "
             "or leave it empty; your choice is kept when creating the Trip later.",
    )

    @api.model
    def _default_etol(self):
        line_id = self.env.context.get("default_sale_line_id")
        if not line_id:
            return False
        line = self.env["sale.order.line"].browse(line_id).exists()
        return line.etol if line else False

    etol = fields.Date(
        string="ETOL (Estimated Loading Date)", default=_default_etol,
        help="Required for dispatch allocation. Saved back to the Sales "
             "Order shipment line when assigning the truck.",
    )
    sale_order_conflict_note = fields.Char(
        compute="_compute_truck_warnings",
        string="Other Sales Orders",
    )
    in_progress_trip_note = fields.Char(
        compute="_compute_truck_warnings",
        string="In Progress Trips",
    )

    @api.depends("vehicle_id", "sale_line_id")
    def _compute_truck_warnings(self):
        SaleLine = self.env["sale.order.line"].sudo()
        Trip = self.env["taj.transport.trip"].sudo()
        for wizard in self:
            wizard.sale_order_conflict_note = False
            wizard.in_progress_trip_note = False
            truck = wizard.vehicle_id
            source = wizard.sale_line_id
            if not truck or not source:
                continue
            source.check_access("read")
            # Only count within the selected sale's company. Do not expose
            # record identities or another company's confidential shipment.
            others = SaleLine.search([
                ("vehicle_id", "=", truck.id),
                ("id", "!=", source.id),
                ("order_id.company_id", "=", source.company_id.id),
                ("order_id.state", "in", ("draft", "sent", "sale", "done")),
                ("product_id", "!=", False),
            ])
            uncompleted = others.filtered(
                lambda line: not any(
                    trip.state == "completed" for trip in line.transport_trip_ids
                )
            )
            if uncompleted:
                wizard.sale_order_conflict_note = _(
                    "Warning: This truck is already on %(count)s other Sales "
                    "Order line(s) without a completed trip.",
                    count=len(uncompleted),
                )
            # Fleet availability is global for shared trucks too. Show only
            # a generic occupancy warning, never another company's Trip IDs.
            in_progress = Trip.search_count([
                ("state", "=", "in_progress"),
                ("current_assignment_id.vehicle_id", "=", truck.id),
            ])
            if in_progress:
                wizard.in_progress_trip_note = _(
                    "Warning: This truck already has an In Progress trip."
                )

    @api.depends("vehicle_id")
    def _compute_driver_id(self):
        for wizard in self:
            driver = wizard.vehicle_id.driver_id
            company = wizard.company_id or self.env.company
            wizard.driver_id = driver if (
                driver and driver.active
                and (not driver.company_id or driver.company_id == company)
            ) else False

    def action_assign_truck(self):
        self.ensure_one()
        line = self.sale_line_id
        line.check_access("read")
        if not (
            self.env.su
            or self.env.user.has_group("taj_transport_operations.group_transport_user")
        ):
            raise AccessError(
                _("Transport Operations access is required to assign a truck.")
            )
        if (
            not line.exists()
            or line.order_id.state not in ("sale", "done")
            or line.display_type not in (False, "product")
            or not line.product_id
            or line.is_downpayment
        ):
            raise UserError(_("Select a confirmed product line."))
        vehicle = self.vehicle_id
        vehicle.check_access("read")
        category = self.env.ref("taj_transport_operations.fleet_category_truck")
        if (
            not vehicle
            or not vehicle.active
            or (vehicle.category_id and vehicle.category_id != category)
            or (vehicle.company_id and vehicle.company_id != line.order_id.company_id)
        ):
            raise UserError(
                _(
                    "Choose an active Truck from the sales order's company or a shared truck."
                )
            )
        driver = self.driver_id
        if driver:
            driver.check_access("read")
            if (
                not driver.active
                or (driver.company_id and driver.company_id != line.company_id)
            ):
                raise UserError(_(
                    "Choose an active driver from the Sales Order's company "
                    "or a shared contact."
                ))
        if not self.etol:
            raise UserError(_("ETOL is required before assigning a truck."))
        # Match assignment's trip -> source line lock order. Recheck after the
        # lock so a stale dialog or a retried request cannot replace a truck.
        trips = line.transport_trip_ids
        for trip in trips.sorted("id"):
            trip._lock_for_transition()
        line._lock_transport_vehicle()
        if line.vehicle_id:
            raise UserError(_("This line already has a truck. Refresh the worklist."))
        # The Sale Order is locked. Route the ETOL update through the same
        # audited, field-limited shipment editor used by File Numbers.
        if line.etol != self.etol:
            file_row = line.transport_file_line_ids
            if len(file_row) != 1:
                raise UserError(_("The Sales line needs its File Number shipment row before allocation."))
            line._write_file_shipment({"etol": self.etol}, file_row)
        if trips:
            trip = trips.ensure_one()
            if trip.state != "draft" or trip.assignment_ids:
                raise UserError(_("Use the trip's resource workflow to change its allocation."))
            self.env["taj.transport.assignment"].create({
                "trip_id": trip.id, "vehicle_id": vehicle.id,
                "driver_id": driver.id, "effective_from": fields.Datetime.now(),
                "reason": _("Assigned from Sales allocation worklist"),
            })
            # Keep the Sales allocation snapshot consistent with Resources.
            line.sudo().write({
                "transport_allocated_driver_id": driver.id or False,
                "transport_driver_choice_set": True,
            })
        else:
            # Save the driver's explicit choice alongside the first truck.
            # A later Trip uses this snapshot instead of Fleet's possibly
            # changed driver. False is an intentional no-driver selection.
            line.sudo().write({
                "vehicle_id": vehicle.id,
                "transport_allocated_driver_id": driver.id or False,
                "transport_driver_choice_set": True,
            })
        return {"type": "ir.actions.act_window_close"}
