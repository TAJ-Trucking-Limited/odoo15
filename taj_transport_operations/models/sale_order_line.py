from odoo import _, fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    transport_trip_ids = fields.One2many(
        "taj.transport.trip", "sale_line_id",
        string="Transport Trip", readonly=True,
    )

    def action_open_or_create_transport_trip(self):
        self.ensure_one()
        trip = self.transport_trip_ids[:1]
        if not trip:
            self.env["taj.transport.trip"]._validate_sale_line(self)
            trip = self.env["taj.transport.trip"].create({
                "sale_line_id": self.id,
            })
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
