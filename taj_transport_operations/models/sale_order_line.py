from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    transport_planned_departure_at = fields.Datetime(
        string="Planned Departure", copy=False,
        help="Copied to a new trip; later edits do not update existing trips.",
    )
    transport_planned_arrival_at = fields.Datetime(
        string="Planned Arrival", copy=False,
        help="Copied to a new trip; later edits do not update existing trips.",
    )

    @api.constrains("transport_planned_departure_at", "transport_planned_arrival_at")
    def _check_transport_planned_schedule(self):
        for line in self:
            if (line.transport_planned_departure_at and line.transport_planned_arrival_at
                    and line.transport_planned_arrival_at < line.transport_planned_departure_at):
                raise ValidationError(_("Transport planned arrival cannot be earlier than the planned departure."))

    transport_trip_ids = fields.One2many(
        "taj.transport.trip", "sale_line_id",
        string="Transport Trip", readonly=True,
    )
    transport_trip_count = fields.Integer(
        string="Transport Trips", compute="_compute_transport_trip_count",
    )

    @api.depends("transport_trip_ids")
    def _compute_transport_trip_count(self):
        for line in self:
            line.transport_trip_count = len(line.transport_trip_ids)

    @api.depends("product_id", "order_id", "order_partner_id", "name", "vehicle_id", "vehicle_id.license_plate", "container_num")
    @api.depends_context("transport_line_label")
    def _compute_display_name(self):
        if not self.env.context.get("transport_line_label"):
            return super()._compute_display_name()
        for line in self:
            label = "%s — %s" % (
                line.product_id.display_name or line.name or "",
                line.vehicle_id.license_plate or _("No truck"),
            )
            if line.container_num:
                label += " — " + line.container_num
            # Distinguish repeated product/truck/container combinations.
            line.display_name = "%s [#%s]" % (label, line.id)

    def action_prepare_transport_trip(self):
        self.ensure_one()
        self.check_access("read")
        existing = self.transport_trip_ids[:1]
        if existing:
            return existing.get_formview_action()
        self.env["taj.transport.trip"]._validate_sale_line(self)
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Transport Trip"),
            "res_model": "taj.transport.trip",
            "view_mode": "form", "target": "current",
            "views": [(self.env.ref("taj_transport_operations.view_transport_trip_form").id, "form")],
            "context": {
                "default_sale_order_id": self.order_id.id,
                "default_sale_line_id": self.id,
                "transport_trip_entry": True,
            },
        }

    cargo_type_id = fields.Many2one(
        "taj.transport.cargo.type", string="Cargo Type",
        help="Optional cargo classification copied onto the new trip for "
        "this sales line. Later line edits do not update existing trips.",
    )
    cargo_description = fields.Text(
        string="Cargo Description",
        help="Optional goods description copied onto the new trip for "
        "this sales line. Later line edits do not update existing trips.",
    )
    special_handling_instructions = fields.Text(
        string="Special Handling Instructions",
        help="Optional handling notes copied onto the new trip for "
        "this sales line. Later line edits do not update existing trips.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        confirmed = lines.filtered(lambda line: line.order_id.state in ("sale", "done"))
        self.env["taj.transport.trip.file"]._ensure_for_sale_lines(confirmed)
        return lines

    def write(self, vals):
        if "order_id" in vals and any(
            line.order_id.id != vals["order_id"]
            and self.env["taj.transport.trip"].sudo().search_count([("sale_line_id", "=", line.id)])
            for line in self
        ):
            raise UserError(_("A sales line linked to a trip cannot move to another Sales Order."))
        return super().write(vals)

    def action_open_or_create_transport_trip(self):
        self.ensure_one()
        trip = self.transport_trip_ids[:1]
        if trip:
            return {
                "type": "ir.actions.act_window",
                "name": _("Transport Trip"),
                "res_model": "taj.transport.trip",
                "res_id": trip.id,
                "view_mode": "form",
                "target": "current",
            }
        Trip = self.env["taj.transport.trip"]
        Trip._validate_sale_line(self)
        routes = self.env["taj.transport.route"]._matching_for_sale_line(self)
        if not routes:
            raise UserError(_(
                "No active route template is linked to product %(product)s "
                "for company %(company)s. Ask a Transport Manager to link a "
                "route template to this product, then try again."
            ) % {
                "product": self.product_id.display_name,
                "company": (
                    self.company_id or self.order_id.company_id
                ).display_name,
            })
        if len(routes) > 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Choose Route Template"),
                "res_model": "taj.transport.trip.route.chooser.wizard",
                "view_mode": "form",
                "target": "new",
                "context": {"default_sale_line_id": self.id},
            }
        trip = Trip.create({
            "sale_line_id": self.id,
            "route_id": routes.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
