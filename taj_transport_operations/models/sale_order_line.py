from odoo import _, fields, models
from odoo.exceptions import UserError


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    transport_trip_ids = fields.One2many(
        "taj.transport.trip", "sale_line_id",
        string="Transport Trip", readonly=True,
    )

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
