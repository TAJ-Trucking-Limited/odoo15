from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    transport_trip_ids = fields.One2many(
        "taj.transport.trip", "sale_order_id",
        string="Transport Trips", readonly=True,
    )
    transport_trip_count = fields.Integer(
        compute="_compute_transport_trip_count",
        string="Transport Trips",
    )

    @api.depends("transport_trip_ids")
    def _compute_transport_trip_count(self):
        for order in self:
            order.transport_trip_count = len(order.transport_trip_ids)

    def action_view_transport_trips(self):
        self.ensure_one()
        action = self.env.ref(
            "taj_transport_operations.action_transport_trip"
        ).read()[0]
        action["domain"] = [("sale_order_id", "=", self.id)]
        return action

    def action_prepare_transport_trips(self):
        self.ensure_one()
        if self.state not in ("sale", "done"):
            raise UserError(_(
                "Confirm the sales order before creating transport trips."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Transport Trips"),
            "res_model": "taj.transport.trip.create.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sale_order_id": self.id},
        }
