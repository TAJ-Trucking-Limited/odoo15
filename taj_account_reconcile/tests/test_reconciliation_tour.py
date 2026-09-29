from odoo.addons.account_accountant.tests.common import TestBankRecWidgetCommon
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestTajAccountReconcileTour(TestBankRecWidgetCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.company_currency = cls.company_data['currency']
        cls.foreign_currency = cls.other_currency
        cls.third_currency = cls.other_currency_2
        cls.bank_journal_foreign = cls.company_data['default_journal_bank'].copy({
            'name': 'TAJ FX Browser Test',
            'currency_id': cls.foreign_currency.id,
        })

        cls.eligible_line = cls._create_st_line(
            100.0,
            journal_id=cls.bank_journal_foreign.id,
            payment_ref='TAJ eligible FX',
        )
        cls.ineligible_line = cls._create_st_line(
            80.0,
            journal_id=cls.bank_journal_foreign.id,
            payment_ref='TAJ ineligible third currency',
            foreign_currency_id=cls.third_currency.id,
            amount_currency=50.0,
        )
        cls.partial_line = cls._create_st_line(
            200.0,
            journal_id=cls.bank_journal_foreign.id,
            payment_ref='TAJ partial allocation',
        )
        invoice_line = cls._create_invoice_line(
            'out_invoice',
            partner_id=cls.partner_a.id,
            currency_id=cls.foreign_currency.id,
            invoice_date='2019-01-01',
            invoice_line_ids=[{'price_unit': 120.0}],
        )
        cls.partial_line.set_line_bank_statement_line(invoice_line.ids)

        # Odoo 19's Not Matched filter contains reviewed (checked=True) but
        # unreconciled transactions.  Keep the browser fixture in that real
        # state so the tour catches visibility regressions on migrated data.
        for statement_line in cls.eligible_line | cls.ineligible_line | cls.partial_line:
            statement_line.move_id.checked = True
            statement_line.invalidate_recordset()

    def _get_invoice_allocation_line(self):
        _liquidity, _suspense, other_lines = self.partial_line._seek_for_lines()
        allocation_line = other_lines.filtered(
            lambda line: line.reconciled_lines_excluding_exchange_diff_ids)[:1]
        self.assertTrue(allocation_line)
        return allocation_line

    def test_invoice_allocation_sign_guard_rejects_balance_flip(self):
        allocation_line = self._get_invoice_allocation_line()
        self.assertFalse(self.company_currency.is_zero(allocation_line.balance))
        bad_balance = -allocation_line.balance / 2
        with self.assertRaisesRegex(UserError, 'same accounting sign'):
            self.partial_line.edit_reconcile_line(
                allocation_line.id, {'balance': bad_balance})

    def test_invoice_allocation_sign_guard_allows_same_sign(self):
        allocation_line = self._get_invoice_allocation_line()
        good_balance = allocation_line.balance / 2
        # Check the TAJ guard directly so this test stays focused on sign policy;
        # Odoo's native edit_reconcile_line behavior is exercised by the browser tour.
        self.partial_line._check_reconcile_line_amount_sign(
            allocation_line, {'balance': good_balance})

    def test_reconciliation_browser_workflow(self):
        self.assertTrue(self.eligible_line.checked)
        self.assertFalse(self.eligible_line.is_reconciled)
        self.assertTrue(self.eligible_line.taj_can_edit_company_equivalent)
        self.assertFalse(self.ineligible_line.taj_can_edit_company_equivalent)
        self.assertFalse(self.partial_line.taj_can_edit_company_equivalent)
        self.assertTrue(self.partial_line._seek_for_lines()[2])
        self.start_tour(
            f'/odoo/accounting/{self.bank_journal_foreign.id}/reconciliation',
            'taj_account_reconcile_browser',
            login=self.env.user.login,
        )
