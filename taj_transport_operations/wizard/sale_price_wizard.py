"""A narrow, audited price-only change to a locked Sales Order line."""
from markupsafe import Markup
from math import isfinite
from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError
from ..models.sale_order_line import _CONFIRMED_PRICE_WRITE


class SalePriceWizard(models.TransientModel):
    _name = "taj.transport.sale.price.wizard"
    _description = "Edit Confirmed Sales Line Price"

    sale_line_id = fields.Many2one("sale.order.line", required=True, readonly=True)
    currency_id = fields.Many2one(related="sale_line_id.currency_id")
    price_unit = fields.Monetary(string="Unit Price", required=True, currency_field="currency_id")

    def action_update_price(self):
        self.ensure_one()
        line = self.sale_line_id.exists()
        line.ensure_one()
        line.check_access("write")
        if not self.env.user.has_group("taj_transport_operations.group_edit_confirmed_sale_orders"):
            raise AccessError(_("Confirmed Sales Order Price Editing permission is required."))
        if not isfinite(self.price_unit):
            raise UserError(_("Enter a valid unit price."))
        order = line.order_id
        order.check_access("write")
        order.flush_recordset(["state"])
        self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR NO KEY UPDATE", (order.id,))
        order.invalidate_recordset(["state"])
        line.flush_recordset(["price_unit"])
        self.env.cr.execute("SELECT id FROM sale_order_line WHERE id = %s FOR NO KEY UPDATE", (line.id,))
        line.invalidate_recordset(["price_unit", "order_id"])
        if line.order_id.state not in ("sale", "done") or line.display_type or line.is_downpayment:
            raise UserError(_("Choose a confirmed product line."))
        old_price = line.price_unit
        line.sudo().with_context(
            _confirmed_price_write=_CONFIRMED_PRICE_WRITE,
            move_invoice_line_skip_vat_percentage_sync=True,
        ).write({"price_unit": self.price_unit})
        line.order_id.sudo().message_post(
            body=Markup("<p>%s: %s → %s</p>") % (_("Unit price for %s") % line.product_id.display_name, old_price, self.price_unit),
            author_id=self.env.user.partner_id.id,
        )
        return {"type": "ir.actions.act_window_close"}
