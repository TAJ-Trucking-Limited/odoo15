from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.sale.tests.common import TestSaleCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestOperatingIncome(TestSaleCommon):
    def _order(self):
        return self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({
                'product_id': self.product_a.id,
                'product_uom_qty': 1,
                'price_unit': 100,
                'tax_ids': [Command.clear()],
            })],
        })

    def _invoice(self, order, amount, move_type='out_invoice', currency=None, post=True):
        move = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': order.partner_id.id,
            'invoice_date': fields.Date.today(),
            'currency_id': (currency or order.company_id.currency_id).id,
            'invoice_line_ids': [Command.create({
                'product_id': self.product_a.id,
                'account_id': self.company_data['default_account_revenue'].id,
                'quantity': 1,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
                'sale_line_ids': [Command.set(order.order_line.ids)],
            })],
        })
        if post:
            move.action_post()
        return move

    def _income(self, order):
        return self.env['report.send.mail']._get_order_operating_income(order)

    def test_original_invoice_and_native_partial_credit_note(self):
        order = self._order()
        invoice = self._invoice(order, 3698.40)
        refund = invoice._reverse_moves(default_values_list=[{
            'date': fields.Date.today(),
            'invoice_date': fields.Date.today(),
        }])
        refund.invoice_line_ids.price_unit = 18.40
        refund.action_post()
        self.assertEqual(set(order.invoice_ids.ids), {invoice.id, refund.id})
        self.assertAlmostEqual(self._income(order), 3680.0, places=2)
        report = self.env['report.send.mail'].create({})
        with patch.object(type(report), '_get_sale_orders', return_value=order):
            [(order_line, row)] = report._prepare_profit_rows()
        self.assertAlmostEqual(row['operating_income'], 3680.0, places=2)
        self.assertAlmostEqual(row['cross_profit'], 3680.0, places=2)
        self.assertAlmostEqual(report._trip_profit_values(order_line, row)['operating_income'], 3680.0, places=2)

    def test_multiple_posted_documents_and_exclude_draft_cancelled(self):
        order = self._order()
        other_order = self._order()
        invoice = self._invoice(order, 60, post=False)
        invoice.write({'invoice_line_ids': [Command.create({
            'product_id': self.product_a.id,
            'account_id': self.company_data['default_account_revenue'].id,
            'quantity': 1,
            'price_unit': 40,
            'tax_ids': [Command.clear()],
            'sale_line_ids': [Command.set(order.order_line.ids)],
        })]})
        invoice.action_post()
        self._invoice(order, 50)
        self._invoice(order, 10, 'out_refund')
        self._invoice(order, 15, 'out_refund')
        self._invoice(order, 999, post=False)
        self._invoice(order, 888, 'out_refund', post=False).button_cancel()
        self._invoice(other_order, 777)
        self.assertAlmostEqual(self._income(order), 125.0, places=2)
        self.assertAlmostEqual(self._income(other_order), 777.0, places=2)

    def test_no_invoice_and_full_refund(self):
        order = self._order()
        self.assertEqual(self._income(order), 0.0)
        invoice = self._invoice(order, 100)
        self.assertAlmostEqual(self._income(order), 100.0, places=2)
        refund = invoice._reverse_moves(default_values_list=[{
            'date': fields.Date.today(),
            'invoice_date': fields.Date.today(),
        }])
        refund.action_post()
        self.assertAlmostEqual(self._income(order), 0.0, places=2)

    def test_foreign_currency_uses_company_signed_amounts(self):
        order = self._order()
        company = order.company_id
        currency = self.env.ref('base.USD')
        if currency == company.currency_id:
            currency = self.env.ref('base.EUR')
        currency.active = True
        self.env['res.currency.rate'].create({
            'currency_id': currency.id,
            'company_id': company.id,
            'name': fields.Date.today(),
            'rate': 0.5,
        })
        invoice = self._invoice(order, 100, currency=currency)
        refund = self._invoice(order, 10, 'out_refund', currency=currency)
        expected = invoice.amount_total_signed + refund.amount_total_signed
        self.assertNotAlmostEqual(expected, 90.0, places=2)
        self.assertAlmostEqual(self._income(order), expected, places=2)
