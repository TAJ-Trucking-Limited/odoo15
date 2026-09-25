from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestBankStatementCurrencyAmount(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.company_currency = cls.company_data['currency']
        cls.foreign_currency = cls.setup_other_currency('EUR')
        cls.third_currency = cls.setup_other_currency('GBP')
        cls.bank_journal_company = cls.company_data['default_journal_bank']
        # A journal expressed in a currency that differs from the company one.
        cls.bank_journal_foreign = cls.bank_journal_company.copy({
            'currency_id': cls.foreign_currency.id,
            'name': 'TAJ EUR Bank',
        })

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _create_statement_line(self, journal, amount, foreign_currency=None,
                               amount_currency=None, **kwargs):
        vals = {
            'journal_id': journal.id,
            'partner_id': self.partner_a.id,
            'payment_ref': 'TAJ test transaction',
            'date': fields.Date.from_string('2024-01-01'),
            'amount': amount,
            **kwargs,
        }
        if foreign_currency:
            vals['foreign_currency_id'] = foreign_currency.id
            vals['amount_currency'] = amount_currency
        statement_line = self.env['account.bank.statement.line'].create(vals)
        # Bank transactions are not reviewed until the accountant validates them.
        self._uncheck(statement_line)
        return statement_line

    def _uncheck(self, statement_line):
        if statement_line.checked:
            statement_line.move_id.checked = False
            statement_line.invalidate_recordset(['checked'])
        self.assertFalse(statement_line.checked)

    def _open_wizard(self, statement_line):
        action = statement_line.action_open_taj_currency_amount_wizard()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['res_model'], 'taj.bank.statement.currency.amount.wizard')
        wizard = self.env[action['res_model']].browse(action['res_id'])
        self.assertTrue(wizard.exists())
        return wizard

    def _make_reconciled(self, statement_line):
        """Fully reconcile the suspense line of the statement line."""
        _liquidity_line, suspense_line, _other_lines = statement_line._seek_for_lines()
        self.assertTrue(suspense_line)
        # The default bank suspense account is not necessarily reconcilable in
        # a fresh Odoo.sh test database. Enable reconciliation explicitly so
        # this fixture can exercise the already-reconciled transaction guard.
        suspense_line.account_id.reconcile = True
        counterpart_move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': statement_line.date,
            'line_ids': [
                Command.create({
                    'name': 'TAJ reconciliation counterpart',
                    'account_id': suspense_line.account_id.id,
                    'currency_id': suspense_line.currency_id.id,
                    'amount_currency': -suspense_line.amount_currency,
                    'balance': -suspense_line.balance,
                }),
                Command.create({
                    'name': 'TAJ reconciliation counterpart',
                    'account_id': self.company_data['default_account_revenue'].id,
                    'currency_id': self.company_currency.id,
                    'amount_currency': suspense_line.balance,
                    'balance': suspense_line.balance,
                }),
            ],
        })
        counterpart_move.action_post()
        counterpart_line = counterpart_move.line_ids.filtered(
            lambda line: line.account_id == suspense_line.account_id)
        (suspense_line + counterpart_line).reconcile()
        # In Odoo 19, a statement line is only considered reconciled after it
        # has been reviewed.  Keep the fixture aligned with that real state.
        statement_line.move_id.checked = True
        statement_line.invalidate_recordset()

    def _make_partially_reconciled(self, statement_line, partial_ratio=0.5):
        """Partially reconcile the suspense line of the statement line."""
        _liquidity_line, suspense_line, _other_lines = \
            statement_line._seek_for_lines()
        self.assertTrue(suspense_line)
        suspense_line.account_id.reconcile = True
        part_amount_curr = suspense_line.currency_id.round(
            -suspense_line.amount_currency * partial_ratio)
        part_balance = self.company_currency.round(
            -suspense_line.balance * partial_ratio)
        counterpart_move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id,
            'date': statement_line.date,
            'line_ids': [
                Command.create({
                    'name': 'TAJ partial counterpart',
                    'account_id': suspense_line.account_id.id,
                    'currency_id': suspense_line.currency_id.id,
                    'amount_currency': part_amount_curr,
                    'balance': part_balance,
                }),
                Command.create({
                    'name': 'TAJ partial counterpart',
                    'account_id': self.company_data['default_account_revenue'].id,
                    'currency_id': self.company_currency.id,
                    'amount_currency': -part_balance,
                    'balance': -part_balance,
                }),
            ],
        })
        counterpart_move.action_post()
        counterpart_line = counterpart_move.line_ids.filtered(
            lambda line: line.account_id == suspense_line.account_id)
        (suspense_line + counterpart_line).reconcile()
        statement_line.invalidate_recordset()

    def _add_other_line(self, statement_line):
        """Add a balanced counterpart line, like a split or a write-off."""
        # Write through the statement line: its Odoo 19 write override safely
        # handles changes to the already-posted linked journal entry.
        statement_line.write({
            'line_ids': [
                Command.create({
                    'name': 'TAJ split',
                    'account_id': self.company_data['default_account_expense'].id,
                    'balance': 5.0,
                }),
                Command.create({
                    'name': 'TAJ split',
                    'account_id': self.company_data['default_account_revenue'].id,
                    'balance': -5.0,
                }),
            ],
        })
        statement_line.invalidate_recordset()

    def _get_currency_rates(self):
        return self.env['res.currency.rate'].search_read(
            [], ['currency_id', 'name', 'rate', 'company_id'])

    # -------------------------------------------------------------------------
    # WIZARD OPENING
    # -------------------------------------------------------------------------

    def test_open_wizard_foreign_currency_journal(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        wizard = self._open_wizard(statement_line)
        expected_company_amount = self.foreign_currency._convert(
            100.0, self.company_currency, self.company, statement_line.date)
        self.assertEqual(wizard.statement_line_id, statement_line)
        self.assertEqual(wizard.source_amount, 100.0)
        self.assertEqual(wizard.source_currency_id, self.foreign_currency)
        self.assertEqual(wizard.company_currency_id, self.company_currency)
        self.assertEqual(wizard.company_amount_field, 'amount_currency')
        self.assertAlmostEqual(wizard.company_amount, expected_company_amount, places=2)

    def test_open_wizard_company_currency_journal(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        wizard = self._open_wizard(statement_line)
        self.assertEqual(wizard.source_amount, 400.0)
        self.assertEqual(wizard.source_currency_id, self.foreign_currency)
        self.assertEqual(wizard.company_amount, 200.0)
        self.assertEqual(wizard.company_currency_id, self.company_currency)
        self.assertEqual(wizard.company_amount_field, 'amount')

    def test_wizard_field_configuration(self):
        wizard_model = self.env['taj.bank.statement.currency.amount.wizard']
        self.assertTrue(wizard_model._fields['source_amount'].readonly)
        self.assertTrue(wizard_model._fields['source_currency_id'].readonly)
        self.assertTrue(wizard_model._fields['source_currency_id'].required)
        self.assertTrue(wizard_model._fields['company_currency_id'].readonly)
        self.assertTrue(wizard_model._fields['company_currency_id'].required)
        self.assertTrue(wizard_model._fields['company_amount_field'].readonly)
        self.assertTrue(wizard_model._fields['company_amount_field'].required)
        self.assertTrue(wizard_model._fields['effective_rate'].readonly)
        self.assertFalse(wizard_model._fields['company_amount'].readonly)
        self.assertTrue(wizard_model._fields['company_amount'].required)

    def test_effective_rate_is_computed(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        wizard = self._open_wizard(statement_line)
        wizard.company_amount = 210.0
        self.assertAlmostEqual(wizard.effective_rate, 0.525, places=6)

    # -------------------------------------------------------------------------
    # UI ELIGIBILITY FLAG
    # -------------------------------------------------------------------------

    def test_company_equivalent_eligibility_foreign_currency_journal(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        self.assertTrue(statement_line.taj_can_edit_company_equivalent)

    def test_company_equivalent_eligibility_company_currency_journal(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        self.assertTrue(statement_line.taj_can_edit_company_equivalent)

    def test_company_equivalent_eligibility_rejects_company_currency_only(self):
        statement_line = self._create_statement_line(self.bank_journal_company, 100.0)
        self.assertFalse(statement_line.taj_can_edit_company_equivalent)

    def test_company_equivalent_eligibility_rejects_third_currency(self):
        statement_line = self._create_statement_line(
            self.bank_journal_foreign, 100.0,
            foreign_currency=self.third_currency, amount_currency=50.0)
        self.assertFalse(statement_line.taj_can_edit_company_equivalent)

    def test_company_equivalent_eligibility_rejects_checked_transaction(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        statement_line.move_id.checked = True
        statement_line.invalidate_recordset()
        self.assertFalse(statement_line.taj_can_edit_company_equivalent)

    def test_company_equivalent_eligibility_rejects_partial_reconciliation(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        self._make_partially_reconciled(statement_line)
        self.assertFalse(statement_line.taj_can_edit_company_equivalent)

    def test_company_equivalent_eligibility_rejects_counterpart_lines(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        self._add_other_line(statement_line)
        self.assertFalse(statement_line.taj_can_edit_company_equivalent)

    def test_company_equivalent_eligibility_rejects_non_accountant(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        non_accountant = new_test_user(
            self.env, login='taj_eligibility_non_accountant',
            groups='base.group_user,account.group_account_readonly')
        self.assertFalse(
            statement_line.with_user(non_accountant).taj_can_edit_company_equivalent)

    # -------------------------------------------------------------------------
    # APPLYING BOTH DIRECTIONS
    # -------------------------------------------------------------------------

    def test_apply_foreign_currency_journal_sets_company_equivalent(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        wizard = self._open_wizard(statement_line)
        wizard.company_amount = 60.0
        result = wizard.action_apply()
        self.assertEqual(result, {'type': 'ir.actions.act_window_close'})

        statement_line.invalidate_recordset()
        # The transaction amount and its currency are preserved.
        self.assertEqual(statement_line.amount, 100.0)
        self.assertEqual(statement_line.currency_id, self.foreign_currency)
        # The company equivalent is stored on the standard other-currency fields.
        self.assertEqual(statement_line.foreign_currency_id, self.company_currency)
        self.assertEqual(statement_line.amount_currency, 60.0)

        liquidity_lines, suspense_lines, other_lines = statement_line._seek_for_lines()
        self.assertFalse(other_lines)
        self.assertEqual(len(liquidity_lines), 1)
        self.assertEqual(len(suspense_lines), 1)
        self.assertRecordValues(liquidity_lines, [{
            'currency_id': self.foreign_currency.id,
            'amount_currency': 100.0,
            'debit': 60.0,
            'credit': 0.0,
        }])
        self.assertRecordValues(suspense_lines, [{
            'currency_id': self.company_currency.id,
            'amount_currency': -60.0,
            'debit': 0.0,
            'credit': 60.0,
        }])
        self.assertAlmostEqual(sum(statement_line.move_id.line_ids.mapped('balance')), 0.0, places=2)

    def test_apply_company_currency_journal_updates_amount_only(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        wizard = self._open_wizard(statement_line)
        wizard.company_amount = 210.0
        result = wizard.action_apply()
        self.assertEqual(result, {'type': 'ir.actions.act_window_close'})

        statement_line.invalidate_recordset()
        # The foreign amount is preserved, only the company amount changes.
        self.assertEqual(statement_line.amount, 210.0)
        self.assertEqual(statement_line.amount_currency, 400.0)
        self.assertEqual(statement_line.foreign_currency_id, self.foreign_currency)
        self.assertEqual(statement_line.currency_id, self.company_currency)

        liquidity_lines, suspense_lines, other_lines = statement_line._seek_for_lines()
        self.assertFalse(other_lines)
        self.assertEqual(len(liquidity_lines), 1)
        self.assertEqual(len(suspense_lines), 1)
        self.assertRecordValues(liquidity_lines, [{
            'currency_id': self.company_currency.id,
            'amount_currency': 210.0,
            'debit': 210.0,
            'credit': 0.0,
        }])
        self.assertRecordValues(suspense_lines, [{
            'currency_id': self.foreign_currency.id,
            'amount_currency': -400.0,
            'debit': 0.0,
            'credit': 210.0,
        }])
        self.assertAlmostEqual(sum(statement_line.move_id.line_ids.mapped('balance')), 0.0, places=2)

    def test_apply_negative_outflow_foreign_currency_journal(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, -100.0)
        wizard = self._open_wizard(statement_line)
        self.assertEqual(wizard.source_amount, -100.0)
        self.assertEqual(wizard.source_currency_id, self.foreign_currency)
        self.assertEqual(wizard.company_currency_id, self.company_currency)
        self.assertEqual(wizard.company_amount_field, 'amount_currency')

        wizard.company_amount = -60.0
        result = wizard.action_apply()
        self.assertEqual(result, {'type': 'ir.actions.act_window_close'})

        statement_line.invalidate_recordset()
        self.assertEqual(statement_line.amount, -100.0)
        self.assertEqual(statement_line.currency_id, self.foreign_currency)
        self.assertEqual(statement_line.foreign_currency_id, self.company_currency)
        self.assertEqual(statement_line.amount_currency, -60.0)

        liquidity_lines, suspense_lines, other_lines = statement_line._seek_for_lines()
        self.assertFalse(other_lines)
        self.assertEqual(len(liquidity_lines), 1)
        self.assertEqual(len(suspense_lines), 1)
        self.assertRecordValues(liquidity_lines, [{
            'currency_id': self.foreign_currency.id,
            'amount_currency': -100.0,
            'debit': 0.0,
            'credit': 60.0,
        }])
        self.assertRecordValues(suspense_lines, [{
            'currency_id': self.company_currency.id,
            'amount_currency': 60.0,
            'debit': 60.0,
            'credit': 0.0,
        }])
        balance_sum = sum(statement_line.move_id.line_ids.mapped('balance'))
        self.assertTrue(self.company_currency.is_zero(balance_sum))

    def test_apply_negative_outflow_company_currency_journal(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, -200.0,
            foreign_currency=self.foreign_currency, amount_currency=-400.0)
        wizard = self._open_wizard(statement_line)
        self.assertEqual(wizard.source_amount, -400.0)
        self.assertEqual(wizard.source_currency_id, self.foreign_currency)
        self.assertEqual(wizard.company_amount, -200.0)
        self.assertEqual(wizard.company_currency_id, self.company_currency)
        self.assertEqual(wizard.company_amount_field, 'amount')

        wizard.company_amount = -210.0
        result = wizard.action_apply()
        self.assertEqual(result, {'type': 'ir.actions.act_window_close'})

        statement_line.invalidate_recordset()
        self.assertEqual(statement_line.amount, -210.0)
        self.assertEqual(statement_line.amount_currency, -400.0)
        self.assertEqual(statement_line.foreign_currency_id, self.foreign_currency)
        self.assertEqual(statement_line.currency_id, self.company_currency)

        liquidity_lines, suspense_lines, other_lines = statement_line._seek_for_lines()
        self.assertFalse(other_lines)
        self.assertEqual(len(liquidity_lines), 1)
        self.assertEqual(len(suspense_lines), 1)
        self.assertRecordValues(liquidity_lines, [{
            'currency_id': self.company_currency.id,
            'amount_currency': -210.0,
            'debit': 0.0,
            'credit': 210.0,
        }])
        self.assertRecordValues(suspense_lines, [{
            'currency_id': self.foreign_currency.id,
            'amount_currency': 400.0,
            'debit': 210.0,
            'credit': 0.0,
        }])
        balance_sum = sum(statement_line.move_id.line_ids.mapped('balance'))
        self.assertTrue(self.company_currency.is_zero(balance_sum))

    def test_apply_posts_audit_message(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        wizard = self._open_wizard(statement_line)
        wizard.company_amount = 210.0
        wizard.action_apply()
        bodies = statement_line.move_id.message_ids.mapped('body')
        self.assertTrue(
            any('200.00' in body and '210.00' in body for body in bodies),
            "The move must receive an audit message with the old and new equivalents.",
        )

    def test_apply_does_not_touch_currency_rates(self):
        rates_before = self._get_currency_rates()
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        wizard = self._open_wizard(statement_line)
        wizard.company_amount = 60.0
        wizard.action_apply()
        self.assertEqual(self._get_currency_rates(), rates_before)

    # -------------------------------------------------------------------------
    # SAFETY CHECKS
    # -------------------------------------------------------------------------

    def test_rejects_zero_company_equivalent(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        wizard = self._open_wizard(statement_line)
        wizard.company_amount = 0.0
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertFalse(statement_line.foreign_currency_id)

    def test_rejects_zero_source_amount(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        with self.assertRaises(UserError):
            statement_line._check_currency_amount_edit_values(
                0.0, self.foreign_currency, 50.0, self.company_currency)

    def test_rejects_opposite_sign_company_equivalent(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, -200.0,
            foreign_currency=self.foreign_currency, amount_currency=-400.0)
        wizard = self._open_wizard(statement_line)
        wizard.company_amount = 210.0
        with self.assertRaises(UserError):
            wizard.action_apply()
        statement_line.invalidate_recordset()
        self.assertEqual(statement_line.amount, -200.0)
        self.assertEqual(statement_line.amount_currency, -400.0)

    def test_rejects_checked_transaction(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        statement_line.move_id.checked = True
        statement_line.invalidate_recordset(['checked'])
        self.assertTrue(statement_line.checked)
        with self.assertRaises(UserError):
            statement_line.action_open_taj_currency_amount_wizard()

    def test_rejects_checked_reconciled_transaction(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        self._make_reconciled(statement_line)
        self.assertTrue(statement_line.is_reconciled)
        self.assertTrue(statement_line.checked)
        with self.assertRaises(UserError):
            statement_line.action_open_taj_currency_amount_wizard()

    def test_rejects_partially_reconciled_transaction(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        self._make_partially_reconciled(statement_line)
        self.assertFalse(statement_line.is_reconciled)
        self.assertFalse(statement_line.checked)
        _liquidity, suspense_lines, _other = statement_line._seek_for_lines()
        partial_records = \
            suspense_lines.matched_debit_ids + suspense_lines.matched_credit_ids
        self.assertTrue(partial_records.exists())
        self.assertTrue(any(
            not line.currency_id.is_zero(line.amount_residual)
            for line in suspense_lines
        ))
        with self.assertRaises(UserError):
            statement_line.action_open_taj_currency_amount_wizard()

    def test_rejects_transaction_with_other_lines(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        self._add_other_line(statement_line)
        self.assertTrue(statement_line._seek_for_lines()[2])
        self.assertFalse(statement_line.is_reconciled)
        with self.assertRaises(UserError):
            statement_line.action_open_taj_currency_amount_wizard()

    def test_rejects_company_currency_journal_without_foreign_currency(self):
        statement_line = self._create_statement_line(self.bank_journal_company, 100.0)
        with self.assertRaises(UserError):
            statement_line.action_open_taj_currency_amount_wizard()

    def test_rejects_third_currency_on_foreign_currency_journal(self):
        statement_line = self._create_statement_line(
            self.bank_journal_foreign, 100.0,
            foreign_currency=self.third_currency, amount_currency=50.0)
        with self.assertRaises(UserError):
            statement_line.action_open_taj_currency_amount_wizard()

    def test_rejects_non_accountant_user(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        non_accountant = new_test_user(
            self.env, login='taj_non_accounting_user',
            groups='base.group_user')
        with self.assertRaises(AccessError):
            statement_line.with_user(non_accountant).action_open_taj_currency_amount_wizard()

    def test_rejects_user_without_access_to_company(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        other_company = self.env['res.company'].create({'name': 'TAJ other company'})
        restricted_user = new_test_user(
            self.env, login='taj_restricted_accountant',
            groups='base.group_user,account.group_account_user',
            company_id=other_company.id,
            company_ids=[Command.set(other_company.ids)])
        with self.assertRaises(AccessError):
            statement_line.with_user(restricted_user).action_open_taj_currency_amount_wizard()

    def test_apply_rechecks_safety(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        wizard = self._open_wizard(statement_line)
        # The transaction is reviewed between opening and applying the wizard.
        statement_line.move_id.checked = True
        statement_line.invalidate_recordset(['checked'])
        wizard.company_amount = 210.0
        with self.assertRaises(UserError):
            wizard.action_apply()
        statement_line.invalidate_recordset()
        self.assertEqual(statement_line.amount, 200.0)

    def test_apply_rejects_partially_reconciled_transaction(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        wizard = self._open_wizard(statement_line)
        # Partially reconcile the suspense line while the wizard is open
        self._make_partially_reconciled(statement_line)
        _liquidity, suspense_lines, _other = statement_line._seek_for_lines()
        partial_records = \
            suspense_lines.matched_debit_ids + suspense_lines.matched_credit_ids
        self.assertTrue(partial_records.exists())
        self.assertFalse(statement_line.is_reconciled)
        self.assertTrue(any(
            not line.currency_id.is_zero(line.amount_residual)
            for line in suspense_lines
        ))
        matched_records_before = [
            (line.matched_debit_ids.ids, line.matched_credit_ids.ids)
            for line in suspense_lines
        ]
        wizard.company_amount = 210.0
        with self.assertRaises(UserError):
            wizard.action_apply()
        # Verify partial reconciliation records are never mutated
        statement_line.invalidate_recordset()
        self.assertEqual(statement_line.amount, 200.0)
        _liquidity, suspense_lines, _other = statement_line._seek_for_lines()
        matched_records_after = [
            (line.matched_debit_ids.ids, line.matched_credit_ids.ids)
            for line in suspense_lines
        ]
        self.assertEqual(matched_records_after, matched_records_before)

    def test_apply_rejects_concurrency_amount_drift(self):
        statement_line = self._create_statement_line(
            self.bank_journal_company, 200.0,
            foreign_currency=self.foreign_currency, amount_currency=400.0)
        wizard = self._open_wizard(statement_line)
        self.assertEqual(wizard.source_amount, 400.0)
        # Modify the foreign amount on the statement line while the wizard is open
        statement_line.write({'amount_currency': 500.0})
        statement_line.invalidate_recordset()
        wizard.company_amount = 210.0
        with self.assertRaises(UserError):
            wizard.action_apply()
        statement_line.invalidate_recordset()
        self.assertEqual(statement_line.amount, 200.0)
        self.assertEqual(statement_line.amount_currency, 500.0)

    def test_apply_rejects_concurrency_amount_drift_foreign_journal(self):
        statement_line = self._create_statement_line(self.bank_journal_foreign, 100.0)
        wizard = self._open_wizard(statement_line)
        self.assertEqual(wizard.source_amount, 100.0)
        # Modify the journal amount on the statement line while the wizard is open
        statement_line.write({'amount': 150.0})
        statement_line.invalidate_recordset()
        wizard.company_amount = 60.0
        with self.assertRaises(UserError):
            wizard.action_apply()
        statement_line.invalidate_recordset()
        self.assertEqual(statement_line.amount, 150.0)
        self.assertFalse(statement_line.foreign_currency_id)
