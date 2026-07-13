import logging

from odoo import models, api, fields

_logger = logging.getLogger(__name__)


class PurchaseOrderInherit(models.Model):
    _inherit = 'purchase.order'

    sale_order_id = fields.Many2one('sale.order', 'Sale Order')

    def action_create_invoice(self):
        action = super().action_create_invoice()
        for order in self:
            if not order.sale_order_id or not order.invoice_ids:
                continue
            invoice = order.invoice_ids[:1]
            for rec in order.sale_order_id.order_line:
                for rec_line in invoice.line_ids:
                    if rec_line.account_id.account_type != 'liability_payable':
                        rec_line.container_num = rec.container_num
                        rec_line.file_name = rec.file_name
                        rec_line.consignee = rec.consignee
                        rec_line.weight = rec.weight
                        rec_line.size = rec.size
                        rec_line.srn = rec.srn
                        rec_line.order_id = rec.order_id.id
                        rec_line.route_id = rec.id
        return action

    @api.onchange('order_line')
    def set_cargo_rout(self):
        for rec in self:
            for line in rec.order_line:
                _logger.info(f'mammamamm')
                if line.analytic_distribution:
                    ids = []
                    _logger.info(f'line.analytic_distribution{line.analytic_distribution}')
                    _logger.info(f'line.analytic_distribution{line.analytic_distribution}')
                    for key in line.analytic_distribution.keys():
                        _logger.info(f'key::::{key}')
                        truck = self.env['account.analytic.account'].sudo().search([('id', '=', int(key))])
                        if 'Truck' in truck.name:
                            line.truck_number = truck.name
                        if 'Cargo' in truck.name:
                            line.cargo_type = truck.name
                        if 'DAR' in truck.name:
                            line.rout = truck.name
                        # ids.append(truck.id)
                    # line.hide_analytic_account = ids


class PurchaseOrderLineInherit(models.Model):
    _inherit = 'purchase.order.line'

    hide_analytic_account = fields.Many2many('account.analytic.account', 'analytic_purchase_line')
    truck_number = fields.Char('Truck Number')
    cargo_type = fields.Char('Cargo Type')
    rout = fields.Char('Rout')
