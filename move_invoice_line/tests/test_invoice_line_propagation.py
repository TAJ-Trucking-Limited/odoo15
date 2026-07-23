from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestSaleInvoiceLinePropagation(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        brand = cls.env['fleet.vehicle.model.brand'].create({'name': 'Test Brand'})
        vehicle_model = cls.env['fleet.vehicle.model'].create({
            'name': 'Test Model',
            'brand_id': brand.id,
        })
        cls.vehicle = cls.env['fleet.vehicle'].create({
            'model_id': vehicle_model.id,
            'license_plate': 'TEST-1',
        })
        cls.sale_order = cls.env['sale.order'].create({
            'partner_id': cls.partner_a.id,
        })
        cls.sale_line = cls.env['sale.order.line'].create({
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
        section = self.env['sale.order.line'].create({
            'order_id': self.sale_order.id,
            'display_type': 'line_section',
            'name': 'Section',
        })

        values = section._prepare_invoice_line()

        self.assertNotIn('route_id', values)
        self.assertNotIn('container_num', values)
