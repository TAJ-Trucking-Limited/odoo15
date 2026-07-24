from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestPurchaseInvoiceLinePropagation(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].sudo().create({
            'name': 'Test Vendor',
            'supplier_rank': 1,
        })

    def _sale_order(self, line_count=1):
        order = self.env['sale.order'].sudo().create({
            'partner_id': self.partner_a.id,
        })
        for index in range(line_count):
            self.env['sale.order.line'].sudo().create({
                'order_id': order.id,
                'product_id': self.product_a.id,
                'product_uom_qty': 1,
                'price_unit': 100,
                'container_num': f'CONT-{index + 1}',
                'file_name': f'FILE-{index + 1}',
                'consignee': f'Consignee {index + 1}',
                'weight': f'{index + 1}0T',
                'size': '40FT',
                'srn': f'SRN-{index + 1}',
            })
        return order

    def _purchase_line(self, sale_order=None, sale_line=None):
        order = self.env['purchase.order'].sudo().create({
            'partner_id': self.vendor.id,
            'sale_order_id': sale_order.id if sale_order else False,
        })
        return self.env['purchase.order.line'].sudo().create({
            'order_id': order.id,
            'product_id': self.product_a.id,
            'product_qty': 1,
            'price_unit': 100,
            'sale_line_id': sale_line.id if sale_line else False,
        })

    def test_standard_sale_line_link_has_priority(self):
        first_order = self._sale_order()
        linked_order = self._sale_order()
        linked_line = linked_order.order_line.filtered(
            lambda line: not line.display_type
        )
        purchase_line = self._purchase_line(
            sale_order=first_order,
            sale_line=linked_line,
        )

        values = purchase_line._prepare_account_move_line()

        self.assertEqual(values['route_id'], linked_line.id)
        self.assertEqual(values['order_id'], linked_order.id)
        self.assertEqual(values['container_num'], 'CONT-1')

    def test_header_link_uses_sole_product_line(self):
        sale_order = self._sale_order()
        sale_line = sale_order.order_line.filtered(lambda line: not line.display_type)
        purchase_line = self._purchase_line(sale_order=sale_order)

        values = purchase_line._prepare_account_move_line()

        self.assertEqual(values['route_id'], sale_line.id)
        self.assertEqual(values['file_name'], 'FILE-1')
        self.assertEqual(purchase_line.source_sale_line_id, sale_line)

    def test_ambiguous_header_link_blocks_bill_values(self):
        sale_order = self._sale_order(line_count=2)
        purchase_line = self._purchase_line(sale_order=sale_order)

        with self.assertRaisesRegex(UserError, 'multiple product lines'):
            purchase_line._prepare_account_move_line()

    def test_unlinked_purchase_line_remains_unmapped(self):
        values = self._purchase_line()._prepare_account_move_line()

        self.assertNotIn('route_id', values)
        self.assertNotIn('container_num', values)
