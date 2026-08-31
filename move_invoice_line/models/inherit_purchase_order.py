from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrderInherit(models.Model):
    _inherit = 'purchase.order'

    sale_order_id = fields.Many2one('sale.order', 'Sale Order')


class PurchaseOrderLineInherit(models.Model):
    _inherit = 'purchase.order.line'

    hide_analytic_account = fields.Many2many('account.analytic.account', 'analytic_purchase_line')
    truck_number = fields.Char('Truck Number')
    cargo_type = fields.Char('Cargo Type')
    rout = fields.Char('Rout')
    source_sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Source Sale Order Line',
        compute='_compute_source_sale_line_id',
    )

    @api.depends(
        'sale_line_id',
        'order_id.sale_order_id',
        'order_id.sale_order_id.order_line.display_type',
    )
    def _compute_source_sale_line_id(self):
        for line in self:
            candidates = line.order_id.sale_order_id.order_line.filtered(
                lambda sale_line: not sale_line.display_type
            )
            line.source_sale_line_id = (
                line.sale_line_id
                or (candidates if len(candidates) == 1 else False)
            )

    def _get_invoice_sale_line(self):
        self.ensure_one()
        if self.sale_line_id:
            return self.sale_line_id
        if not self.order_id.sale_order_id:
            return self.env['sale.order.line']

        candidates = self.order_id.sale_order_id.order_line.filtered(
            lambda line: not line.display_type
        )
        if len(candidates) > 1:
            raise UserError(_(
                "Purchase order %(purchase_order)s is linked to sale order "
                "%(sale_order)s, which has multiple product lines. Link each "
                "purchase line to its sale line before creating the bill.",
                purchase_order=self.order_id.display_name,
                sale_order=self.order_id.sale_order_id.display_name,
            ))
        return candidates

    def _prepare_account_move_line(self, move=False):
        values = super()._prepare_account_move_line(move=move)
        if values.get('display_type') != 'product':
            return values

        sale_line = self._get_invoice_sale_line()
        if sale_line:
            values.update({
                'vehicle_id': sale_line.vehicle_id.id,
                'container_num': sale_line.container_num,
                'file_name': sale_line.file_name,
                'consignee': sale_line.consignee,
                'weight': sale_line.weight,
                'size': sale_line.size,
                'srn': sale_line.srn,
                'order_id': sale_line.order_id.id,
                'route_id': sale_line.id,
            })
        return values
