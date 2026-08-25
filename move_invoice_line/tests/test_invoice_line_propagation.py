from xml.etree import ElementTree

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestSaleInvoiceLinePropagation(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        brand = cls.env['fleet.vehicle.model.brand'].sudo().create({
            'name': 'Test Brand',
        })
        vehicle_model = cls.env['fleet.vehicle.model'].sudo().create({
            'name': 'Test Model',
            'brand_id': brand.id,
        })
        cls.vehicle = cls.env['fleet.vehicle'].sudo().create({
            'model_id': vehicle_model.id,
            'license_plate': 'TEST-1',
        })
        cls.sale_order = cls.env['sale.order'].sudo().create({
            'partner_id': cls.partner_a.id,
        })
        cls.sale_line = cls.env['sale.order.line'].sudo().create({
            'order_id': cls.sale_order.id,
            'product_id': cls.product_a.id,
            'product_uom_qty': 1,
            'price_unit': 100,
            'vehicle_id': cls.vehicle.id,
            'container_num': 'CONT-001',
            'file_name': 'FILE-001',
            'consignee': 'Test Consignee',
            'weight': '20T',
            'size': '40FT',
            'srn': 'SRN-001',
        })

    def _combined_view_root(self, xml_id):
        view = self.env.ref(xml_id)
        return ElementTree.fromstring(view.get_combined_arch())

    def test_product_line_propagates_all_custom_values(self):
        values = self.sale_line._prepare_invoice_line()

        self.assertEqual(values['vehicle_id'], self.vehicle.id)
        self.assertEqual(values['container_num'], 'CONT-001')
        self.assertEqual(values['file_name'], 'FILE-001')
        self.assertEqual(values['consignee'], 'Test Consignee')
        self.assertEqual(values['weight'], '20T')
        self.assertEqual(values['size'], '40FT')
        self.assertEqual(values['srn'], 'SRN-001')
        self.assertEqual(values['order_id'], self.sale_order.id)
        self.assertEqual(values['route_id'], self.sale_line.id)

    def test_section_does_not_receive_cargo_values(self):
        section = self.env['sale.order.line'].sudo().create({
            'order_id': self.sale_order.id,
            'display_type': 'line_section',
            'name': 'Section',
        })

        values = section._prepare_invoice_line()

        self.assertNotIn('route_id', values)
        self.assertNotIn('container_num', values)

    def test_single_route_is_auto_selected_from_sale_order(self):
        self.env['sale.order.line'].sudo().create({
            'order_id': self.sale_order.id,
            'display_type': 'line_section',
            'name': 'Section',
        })
        line = self.env['account.move.line'].new({
            'order_id': self.sale_order.id,
        })

        line._onchange_order_id()

        self.assertEqual(line.route_id, self.sale_line)
        self.assertEqual(line.container_num, 'CONT-001')
        self.assertEqual(line.file_name, 'FILE-001')
        self.assertEqual(line.consignee, 'Test Consignee')
        self.assertEqual(line.weight, '20T')
        self.assertEqual(line.size, '40FT')
        self.assertEqual(line.srn, 'SRN-001')
        self.assertEqual(line.vehicle_id, self.vehicle)

    def test_multiple_routes_are_not_auto_selected(self):
        self.env['sale.order.line'].sudo().create({
            'order_id': self.sale_order.id,
            'product_id': self.product_a.id,
            'product_uom_qty': 1,
            'price_unit': 100,
            'container_num': 'CONT-002',
        })
        line = self.env['account.move.line'].new({
            'order_id': self.sale_order.id,
        })

        line._onchange_order_id()

        self.assertFalse(line.route_id)
        self.assertFalse(line.container_num)

    def test_move_form_exposes_cargo_fields_on_both_line_lists(self):
        root = self._combined_view_root(
            'move_invoice_line.view_move_form_madfox_17'
        )
        expected_fields = {
            'order_id',
            'route_id',
            'vehicle_id',
            'analytic_account_id',
            'file_name',
            'container_num',
            'consignee',
            'size',
            'weight',
            'srn',
        }

        targets = {
            'invoice_line_ids': (
                ".//page[@name='invoice_tab']"
                "/field[@name='invoice_line_ids']/list"
            ),
            'line_ids': (
                ".//page[@name='aml_tab']"
                "/field[@name='line_ids']/list"
            ),
        }

        for line_field, xpath in targets.items():
            line_lists = root.findall(xpath)
            self.assertEqual(
                len(line_lists),
                1,
                f'Expected one embedded list for {line_field}',
            )
            field_names = {
                node.get('name')
                for node in line_lists[0].findall('./field')
            }
            self.assertTrue(
                expected_fields.issubset(field_names),
                f'Missing cargo fields from {line_field}: '
                f'{sorted(expected_fields - field_names)}',
            )

    def test_other_custom_forms_keep_their_field_placements(self):
        sale_form = self._combined_view_root(
            'move_invoice_line.view_sale_form_madfox'
        )
        sale_lists = sale_form.findall(
            ".//field[@name='order_line']/list"
        )
        self.assertEqual(len(sale_lists), 1)
        sale_fields = {
            field.get('name')
            for field in sale_lists[0].findall('./field')
        }
        self.assertTrue({
            'vehicle_id',
            'analytic_account_id',
            'file_name',
            'container_num',
            'srn',
            'consignee',
            'size',
            'weight',
        }.issubset(sale_fields))

        purchase_form = self._combined_view_root(
            'move_invoice_line.view_purchase_order_form'
        )
        self.assertTrue(
            purchase_form.findall(".//field[@name='sale_order_id']")
        )
        purchase_lists = purchase_form.findall(
            ".//field[@name='order_line']/list"
        )
        self.assertEqual(len(purchase_lists), 1)
        purchase_fields = {
            field.get('name')
            for field in purchase_lists[0].findall('./field')
        }
        self.assertTrue({
            'truck_number',
            'cargo_type',
            'rout',
        }.issubset(purchase_fields))

        partner_form = self._combined_view_root(
            'move_invoice_line.view_partner_form'
        )
        self.assertTrue(partner_form.findall(".//field[@name='vrn']"))

        asset_form = self._combined_view_root(
            'move_invoice_line.view_account_asset_inherit_form'
        )
        base_asset_form = ElementTree.fromstring(
            self.env.ref(
                'account_asset.view_account_asset_form'
            ).with_context(lang=None).arch_db
        )
        asset_field_paths = {
            'asset model': (
                ".//group[@invisible=\"state != 'model'\"]"
                "//field[@name='account_depreciation_expense_id']"
            ),
            'asset': (
                ".//notebook[@invisible=\"state == 'model'\"]"
                "//field[@name='account_depreciation_expense_id']"
            ),
        }
        for branch, xpath in asset_field_paths.items():
            expected_fields = base_asset_form.findall(xpath)
            expense_fields = asset_form.findall(xpath)
            with self.subTest(asset_branch=branch):
                self.assertEqual(len(expected_fields), 1)
                self.assertEqual(len(expense_fields), 1)
                self.assertEqual(expense_fields[0].get('required'), '1')
                self.assertEqual(
                    expense_fields[0].get('readonly'),
                    "state == 'close'",
                )
                for attribute in ('domain', 'context'):
                    self.assertEqual(
                        expense_fields[0].get(attribute),
                        expected_fields[0].get(attribute),
                    )

        journal_items = self._combined_view_root(
            'move_invoice_line.view_move_line_tree_fleet_madfox'
        )
        journal_item_fields = {
            field.get('name')
            for field in journal_items.findall('.//field')
        }
        self.assertTrue({
            'vehicle_id',
            'order_id',
            'route_id',
        }.issubset(journal_item_fields))

    def test_invoice_report_renders_custom_cargo_content(self):
        invoice = self.init_invoice(
            'out_invoice',
            products=self.product_a,
        )
        product_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product'
        )
        product_line.write({
            'vehicle_id': self.vehicle.id,
            'container_num': 'REPORT-CONT-001',
            'consignee': 'Report Consignee',
        })

        content, report_type = self.env['ir.actions.report']._render_qweb_html(
            'account.account_invoices',
            res_ids=invoice.ids,
        )
        html = content.decode() if isinstance(content, bytes) else content

        self.assertEqual(report_type, 'html')
        for expected in (
            self.vehicle.license_plate,
            'REPORT-CONT-001',
            'Report Consignee',
            'Exchange Rate:',
            'Total TSH',
            '05233990011',
        ):
            self.assertIn(expected, html)
