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
        # Match assignment's trip -> source line lock order. Recheck after the
        # lock so a stale dialog or a retried request cannot replace a truck.
        trips = line.transport_trip_ids
        for trip in trips.sorted("id"):
            trip._lock_for_transition()
        line._lock_transport_vehicle()
        if line.vehicle_id:
            raise UserError(_("This line already has a truck. Refresh the worklist."))
        if trips:
            trip = trips.ensure_one()
            if trip.state != "draft" or trip.assignment_ids:
                raise UserError(_("Use the trip's resource workflow to change its allocation."))
            self.env["taj.transport.assignment"].create({
                "trip_id": trip.id, "vehicle_id": vehicle.id,
                "driver_id": driver.id, "effective_from": fields.Datetime.now(),
                "reason": _("Assigned from Sales allocation worklist"),
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
