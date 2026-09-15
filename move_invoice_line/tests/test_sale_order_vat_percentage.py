# -*- coding: utf-8 -*-

from xml.etree import ElementTree

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestSaleOrderVatPercentage(AccountTestInvoicingCommon):
    """Behavior contract for the Sales Order VAT (%) feature."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        # The generic chart provides two 15% sale taxes (tax_sale_a and its
        # copy tax_sale_b), so ambiguous rates are avoided on purpose.
        cls.sale_tax_7_5 = cls.env['account.tax'].create({
            'name': 'Sale VAT 7.5%',
            'amount': 7.5,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'price_include_override': 'tax_excluded',
            'company_id': cls.company.id,
        })
        cls.sale_tax_100 = cls.env['account.tax'].create({
            'name': 'Sale VAT 100%',
            'amount': 100.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'price_include_override': 'tax_excluded',
            'company_id': cls.company.id,
        })
        cls.product_vat = cls._create_product(
            name='VAT percentage product',
            invoice_policy='order',
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_order(self, rate=None, qty=1.0, price_unit=100.0):
        vals = {
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({
                'product_id': self.product_vat.id,
                'product_uom_qty': qty,
                'price_unit': price_unit,
            })],
        }
        if rate is not None:
            vals['vat_percentage'] = rate
        return self.env['sale.order'].create(vals)

    def _render_invoice_html(self, invoice):
        content, report_type = self.env['ir.actions.report']._render_qweb_html(
            'account.account_invoices',
            res_ids=invoice.ids,
        )
        self.assertEqual(report_type, 'html')
        return content.decode() if isinstance(content, bytes) else content

    # ------------------------------------------------------------------
    # Rate boundaries and line taxes
    # ------------------------------------------------------------------

    def test_zero_rate_clears_line_taxes_on_create(self):
        order = self._create_order(rate=0.0)

        self.assertTrue(order.vat_percentage_active)
        self.assertFalse(order.order_line.tax_ids)

    def test_zero_rate_clears_line_taxes_on_write(self):
        order = self._create_order()
        self.assertEqual(order.order_line.tax_ids, self.tax_sale_a)

        order.write({'vat_percentage': 0.0})

        self.assertTrue(order.vat_percentage_active)
        self.assertFalse(order.order_line.tax_ids)

    def test_decimal_rate_replaces_taxes_on_existing_and_new_lines(self):
        order = self._create_order(rate=7.5)
        line = order.order_line

        self.assertEqual(line.tax_ids, self.sale_tax_7_5)
        self.assertNotIn(self.tax_sale_a, order.order_line.tax_ids)

        new_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product_vat.id,
            'product_uom_qty': 1.0,
            'price_unit': 50.0,
        })
        self.assertEqual(new_line.tax_ids, self.sale_tax_7_5)

        order.write({'order_line': [Command.create({
            'product_id': self.product_vat.id,
            'product_uom_qty': 1.0,
            'price_unit': 25.0,
        })]})
        added_line = order.order_line - line - new_line
        self.assertEqual(added_line.tax_ids, self.sale_tax_7_5)

    def test_changing_rate_updates_existing_lines(self):
        order = self._create_order(rate=7.5)

        order.write({'vat_percentage': 100.0})
        self.assertEqual(order.order_line.tax_ids, self.sale_tax_100)

        order.write({'vat_percentage': 0.0})
        self.assertFalse(order.order_line.tax_ids)

    def test_selecting_vat_tax_updates_rate_and_line_taxes(self):
        order = self._create_order()

        order.write({'vat_tax_id': self.sale_tax_7_5.id})

        self.assertTrue(order.vat_percentage_active)
        self.assertEqual(order.vat_tax_id, self.sale_tax_7_5)
        self.assertEqual(order.vat_percentage, 7.5)
        self.assertEqual(order.order_line.tax_ids, self.sale_tax_7_5)

    def test_clearing_vat_tax_removes_line_taxes(self):
        order = self._create_order()
        order.write({'vat_tax_id': self.sale_tax_7_5.id})

        order.write({'vat_tax_id': False})

        self.assertTrue(order.vat_percentage_active)
        self.assertFalse(order.vat_tax_id)
        self.assertEqual(order.vat_percentage, 0.0)
        self.assertFalse(order.order_line.tax_ids)

    def test_boundaries_are_accepted_and_out_of_range_rejected(self):
        zero_order = self._create_order(rate=0.0)
        self.assertFalse(zero_order.order_line.tax_ids)

        full_order = self._create_order(rate=100.0)
        self.assertEqual(full_order.order_line.tax_ids, self.sale_tax_100)

        with self.assertRaises(ValidationError):
            zero_order.write({'vat_percentage': 100.5})
        with self.assertRaises(ValidationError):
            zero_order.write({'vat_percentage': -0.5})

    # ------------------------------------------------------------------
    # Tax resolution
    # ------------------------------------------------------------------

    def test_missing_matching_tax_is_rejected(self):
        order = self._create_order()

        with self.assertRaises(ValidationError):
            order.write({'vat_percentage': 12.25})
        self.assertFalse(order.vat_percentage_active)
        self.assertEqual(order.order_line.tax_ids, self.tax_sale_a)

        with self.assertRaises(ValidationError):
            self._create_order(rate=12.25)

    def test_duplicate_matching_taxes_are_rejected(self):
        self.sale_tax_7_5.copy({'name': 'Sale VAT 7.5% (duplicate)'})
        order = self._create_order()

        with self.assertRaises(ValidationError):
            order.write({'vat_percentage': 7.5})
        self.assertFalse(order.vat_percentage_active)

    def test_tax_country_is_respected(self):
        country = self.env['res.country'].create({
            'name': 'Vat Testland',
            'code': 'VX',
        })
        foreign_tax = self.env['account.tax'].create({
            'name': 'Sale VAT 7.5% (Vat Testland)',
            'amount': 7.5,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'price_include_override': 'tax_excluded',
            'company_id': self.company.id,
            'country_id': country.id,
        })
        fiscal_position = self.env['account.fiscal.position'].create({
            'name': 'Vat Testland foreign VAT',
            'country_id': country.id,
            'foreign_vat': 'VX123456',
        })
        order = self._create_order()
        order.fiscal_position_id = fiscal_position
        self.assertEqual(order.tax_country_id, country)

        order.write({'vat_percentage': 7.5})

        self.assertEqual(order.order_line.tax_ids, foreign_tax)

    def test_tax_from_another_country_is_not_selected(self):
        country = self.env['res.country'].create({
            'name': 'Other Testland',
            'code': 'VY',
        })
        self.env['account.tax'].create({
            'name': 'Sale VAT 12.25% (Other Testland)',
            'amount': 12.25,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'price_include_override': 'tax_excluded',
            'company_id': self.company.id,
            'country_id': country.id,
        })
        order = self._create_order()

        with self.assertRaises(ValidationError):
            order.write({'vat_percentage': 12.25})
        self.assertFalse(order.vat_percentage_active)

    # ------------------------------------------------------------------
    # Invoice propagation
    # ------------------------------------------------------------------

    def test_invoice_propagates_rate_marker_and_real_amounts(self):
        order = self._create_order(rate=7.5, qty=2.0)
        order.action_confirm()

        invoices = order._create_invoices()

        self.assertEqual(len(invoices), 1)
        invoice = invoices
        self.assertTrue(invoice.is_vat_percentage_invoice)
        self.assertEqual(invoice.vat_percentage, 7.5)
        self.assertEqual(invoice.invoice_line_ids.tax_ids, self.sale_tax_7_5)
        self.assertEqual(invoice.amount_untaxed, 200.0)
        self.assertEqual(invoice.amount_tax, 15.0)
        self.assertEqual(invoice.amount_total, 215.0)

    def test_different_rates_are_not_grouped_in_one_invoice(self):
        order_7_5 = self._create_order(rate=7.5)
        order_100 = self._create_order(rate=100.0)
        orders = order_7_5 | order_100
        orders.action_confirm()

        invoices = orders._create_invoices()

        self.assertEqual(len(invoices), 2)
        self.assertEqual(
            sorted(invoices.mapped('vat_percentage')), [7.5, 100.0],
        )
        for order in orders:
            self.assertEqual(len(order.invoice_ids), 1)
            self.assertEqual(order.invoice_ids.vat_percentage, order.vat_percentage)

    def test_vat_cannot_change_after_invoice_creation(self):
        order = self._create_order(rate=7.5)
        order.action_confirm()
        order._create_invoices()
        self.assertTrue(order.invoice_ids)

        with self.assertRaises(UserError):
            order.write({'vat_percentage': 100.0})

        self.assertEqual(order.vat_percentage, 7.5)
        self.assertEqual(order.order_line.tax_ids, self.sale_tax_7_5)

    # ------------------------------------------------------------------
    # Report behavior
    # ------------------------------------------------------------------

    def test_feature_invoice_renders_requested_labels(self):
        order = self._create_order(rate=7.5, qty=2.0)
        order.action_confirm()
        invoice = order._create_invoices()

        html = self._render_invoice_html(invoice)

        self.assertIn('Total before Tax', html)
        self.assertIn('Total Tax Amount (7.5%)', html)
        self.assertIn('Total Amount', html)
        self.assertIn('Total TSH', html)
        self.assertNotIn('Untaxed Amount', html)

    def test_feature_invoice_rate_label_has_no_trailing_zeros(self):
        order = self._create_order(rate=100.0)
        order.action_confirm()
        invoice = order._create_invoices()

        html = self._render_invoice_html(invoice)

        self.assertIn('Total Tax Amount (100%)', html)
        self.assertNotIn('Total Tax Amount (100.0%)', html)

    def test_invoice_tax_label_does_not_format_a_literal_percent(self):
        view = self.env.ref('move_invoice_line.report_invoice_document_inherit')
        root = ElementTree.fromstring(view.arch)
        labels = root.findall(".//span[@t-out]")

        tax_label = next(
            (
                label for label in labels
                if 'Total Tax Amount' in (label.get('t-out') or '')
            ),
        )
        self.assertEqual(
            tax_label.get('t-out'),
            "'Total Tax Amount (' + ('%g' % o.vat_percentage) + '%)'",
        )

    def test_manual_invoice_keeps_standard_tax_totals(self):
        invoice = self.init_invoice('out_invoice', products=self.product_vat)
        self.assertFalse(invoice.is_vat_percentage_invoice)

        html = self._render_invoice_html(invoice)

        self.assertIn('Untaxed Amount', html)
        self.assertNotIn('Total before Tax', html)
        self.assertNotIn('Total Tax Amount', html)

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def test_sale_order_form_shows_vat_tax_dropdown(self):
        view = self.env.ref('move_invoice_line.view_sale_form_madfox')
        root = ElementTree.fromstring(view.get_combined_arch())

        nodes = root.findall(".//field[@name='vat_tax_id']")

        self.assertEqual(len(nodes), 1)
        self.assertNotEqual(nodes[0].get('invisible'), '1')
        self.assertIn("('type_tax_use', '=', 'sale')", nodes[0].get('domain'))
        self.assertEqual(
            nodes[0].get('options'),
            "{'no_create': True, 'no_open': True}",
        )

    def test_invoice_form_shows_rate_readonly_without_marker(self):
        view = self.env.ref('move_invoice_line.view_move_form_madfox_17')
        root = ElementTree.fromstring(view.get_combined_arch())

        rate_nodes = root.findall(".//field[@name='vat_percentage']")
        self.assertEqual(len(rate_nodes), 1)
        self.assertEqual(rate_nodes[0].get('readonly'), '1')

        marker_nodes = root.findall(
            ".//field[@name='is_vat_percentage_invoice']"
        )
        for node in marker_nodes:
            self.assertEqual(node.get('invisible'), '1')
