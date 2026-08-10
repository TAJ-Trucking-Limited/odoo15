from xml.etree import ElementTree

from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

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

        with (
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
            mute_logger('odoo.sql_db'),
        ):
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

    def test_manual_cheque_menu_keeps_account_user_restriction(self):
        menu = self.env.ref(
            'account_cheque_printing.menu_account_cheque_wizard'
        )

        self.assertEqual(
            menu.group_ids,
            self.env.ref('account.group_account_user'),
        )

    def test_payment_form_keeps_only_custom_cheque_button(self):
        payment_form = self.env.ref(
            'account.view_account_payment_form'
        )
        root = ElementTree.fromstring(payment_form.get_combined_arch())
        buttons = root.findall('.//button')
        action_id = str(self.env.ref(
            'account_cheque_printing.action_open_cheque_wizard'
        ).id)

        custom_buttons = [
            button
            for button in buttons
            if button.get('name') == action_id
            and button.get('string') == 'Print Cheque'
        ]
        standard_buttons = [
            button
            for button in buttons
            if button.get('name') == 'print_checks'
        ]

        self.assertEqual(len(custom_buttons), 1)
        self.assertFalse(standard_buttons)
