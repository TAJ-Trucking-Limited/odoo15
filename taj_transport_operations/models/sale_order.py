from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    transport_customer_email = fields.Char(
        string="Customer Email", related="partner_id.email",
        store=True, readonly=True,
        help="Email taken from the customer record. The trips of this order "
             "show the same address.",
    )

    transport_trip_ids = fields.One2many(
        "taj.transport.trip", "sale_order_id",
        string="Transport Trips", readonly=True,
    )
    transport_trip_count = fields.Integer(
        compute="_compute_transport_trip_count",
        string="Transport Trips",
    )
    trip_file_ids = fields.One2many(
        "taj.transport.trip.file", "sale_order_id", string="Trip Files",
        readonly=True, copy=False,
        groups="sales_team.group_sale_salesman,taj_transport_operations.group_transport_user",
    )
    trip_file_count = fields.Integer(
        string="Trip Files", compute="_compute_trip_file_count",
        groups="sales_team.group_sale_salesman,taj_transport_operations.group_transport_user",
    )

    @api.depends("transport_trip_ids")
    def _compute_transport_trip_count(self):
        for order in self:
            order.transport_trip_count = len(order.transport_trip_ids)

    @api.depends("name", "partner_id")
    @api.depends_context("transport_order_label")
    def _compute_display_name(self):
        if not self.env.context.get("transport_order_label"):
            return super()._compute_display_name()
        for order in self:
            order.display_name = order.name

    @api.depends("trip_file_ids")
    def _compute_trip_file_count(self):
        for order in self:
            order.trip_file_count = len(order.trip_file_ids)

    def _action_confirm(self):
        result = super()._action_confirm()
        self.env["taj.transport.trip.file"]._ensure_for_sale_lines(self.order_line)
        return result

    def action_view_trip_files(self):
        self.ensure_one()
        self.check_access("read")
        action = self.env.ref(
            "taj_transport_operations.action_transport_trip_file"
        ).read()[0]
        action["domain"] = [("sale_order_id", "=", self.id)]
        return action

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
