from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestAccountCheque(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.partner_a
        cls.currency = cls.company_data['currency']

    def _cheque_values(self, number, **overrides):
        values = {
            'cheque_number': number,
            'payee_id': self.partner.id,
            'amount': 100,
            'currency_id': self.currency.id,
            'reason_note': 'Test cheque',
        }
        values.update(overrides)
        return values

    def _payment(self):
        return self.env['account.payment'].create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner.id,
            'amount': 100,
            'currency_id': self.currency.id,
            'journal_id': self.company_data['default_journal_bank'].id,
        })

    def test_batch_create_assigns_references(self):
        cheques = self.env['account.cheque'].create([
            self._cheque_values('BATCH-001'),
            self._cheque_values('BATCH-002'),
        ])

        self.assertEqual(cheques.mapped('name'), ['CH/BATCH-001', 'CH/BATCH-002'])

    def test_duplicate_cheque_number_is_rejected(self):
        self.env['account.cheque'].create(self._cheque_values('UNIQUE-001'))

        with self.assertRaises(ValidationError):
            self.env['account.cheque'].create(self._cheque_values('UNIQUE-001'))

    def test_manual_cheque_wizard(self):
        wizard = self.env['account.cheque.wizard'].create({
            'cheque_number': 'MANUAL-001',
            'payee_id': self.partner.id,
            'amount': 100,
            'currency_id': self.currency.id,
            'reason_note': 'Manual test cheque',
        })

        action = wizard.action_print_cheque_and_voucher()
        cheque = self.env['account.cheque'].browse(action['params']['cheque_id'])

        self.assertEqual(cheque.cheque_type, 'manual_cheque')
        self.assertEqual(cheque.print_count, 1)
        self.assertEqual(action['tag'], 'print_cheque_action')

    def test_payment_cheque_reprint_increments_counters(self):
        payment = self._payment()
        wizard_values = {
            'payment_id': payment.id,
            'cheque_number': 'PAYMENT-001',
            'payee_id': self.partner.id,
            'amount': payment.amount,
            'currency_id': payment.currency_id.id,
            'reason_note': 'Supplier payment',
        }

        self.env['account.cheque.wizard'].create(
            wizard_values
        ).action_print_cheque_and_voucher()
        self.env['account.cheque.wizard'].create(
            wizard_values
        ).action_print_cheque_and_voucher()

        self.assertEqual(payment.cheque_id.print_count, 2)
        self.assertEqual(payment.cheque_print_count, 2)

    def test_changed_data_requires_new_number(self):
        payment = self._payment()
        values = {
            'payment_id': payment.id,
            'cheque_number': 'PAYMENT-002',
            'payee_id': self.partner.id,
            'amount': payment.amount,
            'currency_id': payment.currency_id.id,
            'reason_note': 'Supplier payment',
        }
        self.env['account.cheque.wizard'].create(
            values
        ).action_print_cheque_and_voucher()
        values['amount'] = 200

        with self.assertRaises(UserError):
            self.env['account.cheque.wizard'].create(
                values
            ).action_print_cheque_and_voucher()
