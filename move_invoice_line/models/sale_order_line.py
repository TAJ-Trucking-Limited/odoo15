# -*- coding: utf-8 -*-

from odoo import Command, api, fields, models

# Context key used to avoid re-entering the VAT (%) synchronization when the
# synchronization itself writes on the lines.
VAT_SYNC_CONTEXT_KEY = 'move_invoice_line_skip_vat_percentage_sync'


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

    # ------------------------------------------------------------------
    # CRUD: keep the order VAT (%) applied to every product line
    # ------------------------------------------------------------------

    @api.depends('product_id', 'company_id')
    def _compute_tax_ids(self):
        vat_lines = self.filtered(
            lambda line: (
                not line.display_type
                and line.order_id.vat_percentage_active
            ),
        )
        super(SaleOrderLine, self - vat_lines)._compute_tax_ids()
        taxes_by_order = {}
        for line in vat_lines:
            order = line.order_id
            if order not in taxes_by_order:
                taxes_by_order[order] = order._get_vat_percentage_tax()
            line.tax_ids = taxes_by_order[order]

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get(VAT_SYNC_CONTEXT_KEY):
            lines._sync_vat_percentage_taxes()
        return lines

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get(VAT_SYNC_CONTEXT_KEY):
            self._sync_vat_percentage_taxes()
        return result

    def _sync_vat_percentage_taxes(self):
        """Apply the order VAT tax to the lines of VAT-enabled orders."""
        lines = self.filtered(
            lambda line: (
                not line.display_type
                and line.order_id.vat_percentage_active
            ),
        )
        for order, order_lines in lines.grouped('order_id').items():
            order_lines._set_vat_percentage_tax(
                order._get_vat_percentage_tax(),
            )

    def _set_vat_percentage_tax(self, tax):
        """Set the given tax on these lines (empty tax clears them)."""
        command = [Command.set(tax.ids)]
        for line in self:
            if set(line.tax_ids.ids) == set(tax.ids):
                continue
            line.with_context(**{VAT_SYNC_CONTEXT_KEY: True}).write({
                'tax_ids': command,
            })

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
