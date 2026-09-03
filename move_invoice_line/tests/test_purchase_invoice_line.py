from xml.etree import ElementTree

from lxml import html as lxml_html

from odoo.exceptions import UserError
from odoo.tests import Form, tagged

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

    def _render_purchase_html(self, purchase_order):
        content, report_type = self.env['ir.actions.report']._render_qweb_html(
            'purchase.action_report_purchase_order',
            res_ids=purchase_order.ids,
        )
        self.assertEqual(report_type, 'html')
        return content.decode() if isinstance(content, bytes) else content

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

    def test_inline_edit_preserves_manual_cargo_fields_with_analytics(self):
        purchase_line = self._purchase_line()
        plan = self.env['account.analytic.plan'].sudo().create({
            'name': 'Test Manual Cargo Plan',
        })
        analytic_account = self.env['account.analytic.account'].sudo().create({
            'name': 'Truck Cargo DAR AUTO VALUE',
            'plan_id': plan.id,
        })
        purchase_line.analytic_distribution = {
            str(analytic_account.id): 100,
        }

        with Form(purchase_line.order_id) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.truck_number = 'MANUAL-TRUCK'
                line_form.cargo_type = 'MANUAL-CARGO'
                line_form.rout = 'MANUAL-ROUTE'

        purchase_line.invalidate_recordset([
            'truck_number', 'cargo_type', 'rout',
        ])
        self.assertEqual(purchase_line.truck_number, 'MANUAL-TRUCK')
        self.assertEqual(purchase_line.cargo_type, 'MANUAL-CARGO')
        self.assertEqual(purchase_line.rout, 'MANUAL-ROUTE')

    def test_purchase_report_renders_custom_cargo_content(self):
        sale_order = self._sale_order()
        purchase_line = self._purchase_line(sale_order=sale_order)
        purchase_line.order_id.partner_id.write({
            'street': 'SUPPLIER STREET 172',
            'city': 'Dar es Salaam',
            'phone': '+255 700 001 172',
            'vat': 'SUPPLIER-VAT-172',
        })
        purchase_line.write({
            'truck_number': 'REPORT-TRUCK-001',
            'rout': 'REPORT-ROUTE-001',
            'cargo_type': 'REPORT-CARGO-001',
        })

        html = self._render_purchase_html(purchase_line.order_id)

        for expected in (
            'Truck Number',
            'Container',
            'Route',
            'Cargo Type',
            'Date Req.',
            'REPORT-TRUCK-001',
            'CONT-1',
            'REPORT-ROUTE-001',
            'REPORT-CARGO-001',
            'Test Vendor',
            'SUPPLIER STREET 172',
            '+255 700 001 172',
            'SUPPLIER-VAT-172',
        ):
            self.assertIn(expected, html)

    def test_purchase_report_keeps_supplier_left_and_hides_shipping_address(self):
        purchase_line = self._purchase_line()
        shipping_partner = self.env['res.partner'].sudo().create({
            'name': 'San Francisco',
            'street': 'PO Box 8648',
            'city': 'Dar es Salaam',
        })
        purchase_line.order_id.dest_address_id = shipping_partner

        root = ElementTree.fromstring(
            self.env.ref(
                'move_invoice_line.report_purchaseorder_document'
            ).get_combined_arch()
        )
        layout_options = root.findall(
            ".//t[@t-call='web.external_layout']"
            "/t[@t-set='custom_layout_address']"
        )
        self.assertEqual(len(layout_options), 1)
        self.assertEqual(layout_options[0].get('t-value'), 'True')
        hide_options = root.findall(
            ".//t[@t-call='web.external_layout']"
            "/t[@t-set='taj_hide_information_block']"
        )
        self.assertEqual(len(hide_options), 1)
        self.assertEqual(hide_options[0].get('t-value'), 'True')
        document = lxml_html.fromstring(
            self._render_purchase_html(purchase_line.order_id)
        )
        address_rows = document.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' address ')]"
        )
        self.assertEqual(len(address_rows), 1)
        blocks = address_rows[0].xpath(
            "./div[@name='address' or @name='information_block']"
        )
        self.assertEqual(
            [block.get('name') for block in blocks],
            ['address', 'information_block'],
        )
        self.assertIn('top: -17mm', address_rows[0].get('style', ''))
        self.assertIn(
            'margin-bottom: -17mm',
            address_rows[0].get('style', ''),
        )
        self.assertIn(self.vendor.name, blocks[0].text_content())
        self.assertIn(shipping_partner.name, blocks[1].text_content())
        self.assertIn('display: none', blocks[1].get('style', ''))
