"""Copy a shipment line within its order, clearing the selected cargo values."""
from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportDuplicateSaleLineWizard(models.TransientModel):
    _name = "taj.transport.duplicate.sale.line.wizard"
    _description = "Duplicate Sales Order Line"

    sale_order_id = fields.Many2one(
        "sale.order", string="Quotation / Sales Order", required=True, readonly=True,
    )
    source_line_id = fields.Many2one(
        "sale.order.line", string="Line to Duplicate", required=True,
        domain="[('order_id', '=', sale_order_id), ('product_id', '!=', False), "
               "('display_type', 'in', [False, 'product']), ('is_downpayment', '=', False)]",
    )
    clean_mode = fields.Selection(
        [("container", "Clean Container"), ("all", "Clean All Variables")],
        string="Clear on Duplicate", required=True, default="container",
    )

    def action_duplicate(self):
        self.ensure_one()
        self.check_access("write")
        order = self.sale_order_id
        order.check_access("write")
        order._check_confirmed_sale_edit_access()
        if order.locked or order.state not in ("draft", "sent", "sale"):
            raise UserError(_("Lines can only be duplicated on an unlocked quotation or sales order."))
        source = self.source_line_id
        source.check_access("read")
        if (not source or source.order_id != order or not source.product_id
                or source.display_type not in (False, "product") or source.is_downpayment):
            raise UserError(_("Select a product line from this quotation or sales order."))
        values = {
            "order_id": order.id,
            "sequence": max(order.order_line.mapped("sequence"), default=0) + 1,
            "container_num": False,
            "vehicle_id": False,
            # Each duplicated shipment needs fresh analytical allocation,
            # regardless of the selected cargo cleaning mode.
            "analytic_account_id": False,
            "analytic_distribution": False,
            "price_unit": source.price_unit,
            "transport_planned_departure_at": source.transport_planned_departure_at,
            "transport_planned_arrival_at": source.transport_planned_arrival_at,
        }
        if self.clean_mode == "all":
            values.update({
                "cargo_type_id": False, "size": False, "weight": False,
                "cargo_description": False, "special_handling_instructions": False,
                "price_unit": 0.0,
            })
        source.copy(values)
        return {"type": "ir.actions.act_window_close"}
