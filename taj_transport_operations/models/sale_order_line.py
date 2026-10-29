from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    transport_trip_ids = fields.One2many(
        "taj.transport.trip", "sale_line_id",
        string="Transport Trip", readonly=True,
    )
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
