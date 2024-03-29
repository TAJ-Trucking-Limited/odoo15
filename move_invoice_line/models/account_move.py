from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle', index=True)
    order_id = fields.Many2one('sale.order', string='Sale Order', index=True)
    route_id = fields.Many2one('sale.order.line', string='Route', index=True, domain="[('order_id', '=', order_id)]")
    container_num = fields.Char(require=False, string='CONTAINER NUMBER')
    file_name = fields.Char(require=False, string='File Name')
    analytic_account_id = fields.Many2one('account.analytic.account', 'Analytic Account')
    size = fields.Char(require=False, string='SIZE')
    weight = fields.Char(require=False, string='WEIGHT')
    consignee = fields.Char(require=False, string='CONSIGNEE')
    srn = fields.Char(require=False, string='SHIPMENT REFERENCE NUMBER')

    @api.onchange('route_id')
    def _onchange_route_id(self):
        self.container_num = self.route_id.container_num
        self.file_name = self.route_id.file_name
        self.consignee = self.route_id.consignee
        self.weight = self.route_id.weight
        self.size = self.route_id.size
        self.srn = self.route_id.srn
        self.vehicle_id = self.route_id.vehicle_id
