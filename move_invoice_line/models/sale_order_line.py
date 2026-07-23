# -*- coding: utf-8 -*-

from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    _description = "Sales Order Line"

    container_num = fields.Char(required=False, string='CONTAINER NUMBER')
    file_name = fields.Char(required=False, string='File Name')
    analytic_account_id = fields.Many2one('account.analytic.account', 'Analytic Account')
    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle', required=False, index=True)
    srn = fields.Char(required=False, string='SHIPMENT REFERENCE NUMBER')
    consignee = fields.Char(required=False, string='CONSIGNEE')
    size = fields.Char(required=False, string='SIZE')
    weight = fields.Char(required=False, string='WEIGHT')

    def _prepare_invoice_line(self, **optional_values):
        values = super()._prepare_invoice_line(**optional_values)
        if values.get('display_type') != 'product':
            return values
        values.update({
            'vehicle_id': self.vehicle_id.id,
            'container_num': self.container_num,
            'file_name': self.file_name,
            'consignee': self.consignee,
            'weight': self.weight,
            'size': self.size,
            'srn': self.srn,
            'order_id': self.order_id.id,
            'route_id': self.id,
        })
        return values
