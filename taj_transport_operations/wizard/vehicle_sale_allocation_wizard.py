"""Allocate a Fleet truck to an unallocated confirmed Sales shipment.

Vehicle Availability is a planning entrypoint. It must not assign an existing
Trip or create a new one: Trip creation stays in the dispatch worklist.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class TajTransportVehicleSaleAllocationWizard(models.TransientModel):
    _name = "taj.transport.vehicle.sale.allocation.wizard"
    _description = "Allocate Vehicle to Sales Shipment"

    vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Truck", required=True, readonly=True,
    )
    planned_sale_line_count = fields.Integer(
        related="vehicle_id.transport_planned_sale_line_count",
        string="Already Planned Sales Lines", readonly=True,
    )
    sale_order_id = fields.Many2one(
        "sale.order", string="Sales Order", required=True,
        domain="[('state', 'in', ['sale', 'done'])]",
    )
    sale_line_id = fields.Many2one(
        "sale.order.line", string="Unallocated Shipment Line", required=True,
        domain="[('order_id', '=', sale_order_id), ('vehicle_id', '=', False), "
               "('product_id', '!=', False), ('is_downpayment', '=', False), "
               "('transport_trip_ids', '=', False)]",
    )
    driver_id = fields.Many2one(
        "res.partner", string="Driver", compute="_compute_driver_id",
        store=True, readonly=False, precompute=True,
        help="Defaults to the Fleet driver. An explicit empty selection is "
             "kept for the Trip created later.",
    )
    trailer_id = fields.Many2one(
        "fleet.vehicle", string="Trailer", compute="_compute_trailer_id",
        store=True, readonly=False, precompute=True,
        domain=lambda self: (
            "[('active', '=', True), ('category_id', '=', %s)]"
            % self.env.ref("taj_transport_operations.fleet_category_trailer").id
        ),
        help="Defaults to the truck's Fleet default trailer. An explicit empty "
             "selection is kept for the Trip created later.",
    )
    etol = fields.Date(
        string="ETOL (Estimated Loading Date)", required=True,
    )

    @api.depends("vehicle_id")
    def _compute_driver_id(self):
        for wizard in self:
            wizard.driver_id = wizard.vehicle_id.driver_id

    @api.depends("vehicle_id")
    def _compute_trailer_id(self):
        for wizard in self:
            wizard.trailer_id = wizard.vehicle_id.transport_default_trailer_id

    @api.onchange("sale_order_id")
    def _onchange_sale_order_id(self):
        self.sale_line_id = False
        self.etol = False

    @api.onchange("sale_line_id")
    def _onchange_sale_line_id(self):
        self.etol = self.sale_line_id.etol or False

    def action_allocate(self):
        self.ensure_one()
        if not (
            self.env.su
            or self.env.user.has_group("taj_transport_operations.group_transport_user")
        ):
            raise AccessError(_("Transport Operations access is required."))

        vehicle = self.vehicle_id.exists()
        order = self.sale_order_id.exists()
        line = self.sale_line_id.exists()
        if not vehicle or not order or not line:
            raise UserError(_("Select a Truck, Sales Order and unallocated shipment line."))
        vehicle.check_access("read")
        line.check_access("read")
        if (
            order.state not in ("sale", "done")
            or line.order_id != order
            or line.vehicle_id
            or line.transport_trip_ids
            or line.display_type not in (False, "product")
            or not line.product_id
            or line.is_downpayment
        ):
            raise UserError(_(
                "Select a confirmed Sales Order line without a Truck or Trip. "
                "Refresh the dialog if the allocation has changed."
            ))
        if not vehicle.transport_can_assign:
            raise UserError(_(
                "This truck is no longer available for planning. "
                "Refresh Vehicle Availability."
            ))
        if vehicle.company_id and vehicle.company_id != order.company_id:
            raise UserError(_("Choose a truck from the Sales Order's company or a shared truck."))

        # Reuse the already-hardened first-allocation workflow: ETOL shipment
        # updates, driver/trailer snapshots, company checks, concurrency locks,
        # Fleet pairing and duplicate-allocation protection stay in one place.
        existing = self.env["taj.transport.assign.truck.wizard"].create({
            "sale_line_id": line.id,
            "vehicle_id": vehicle.id,
            "driver_id": self.driver_id.id or False,
            "trailer_id": self.trailer_id.id or False,
            "etol": self.etol,
        })
        return existing.action_assign_truck()
