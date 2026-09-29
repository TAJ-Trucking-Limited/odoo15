import ast
from pathlib import Path

from odoo.modules.module import get_module_path
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestReconciliationViews(TransactionCase):

    def setUp(self):
        super().setUp()
        self.module_path = Path(get_module_path('taj_account_reconcile'))

    def _read_asset(self, relative_path):
        return (self.module_path / relative_path).read_text(encoding='utf-8')

    def _read_manifest(self):
        return ast.literal_eval(
            (self.module_path / '__manifest__.py').read_text(encoding='utf-8'))

    # -------------------------------------------------------------------------
    # ASSETS AND TEMPLATE EXTENSIONS
    # -------------------------------------------------------------------------

    def test_manifest_registers_backend_assets(self):
        assets = self._read_manifest()['assets']['web.assets_backend']
        self.assertIn('taj_account_reconcile/static/src/js/bank_reconciliation.js', assets)
        self.assertIn('taj_account_reconcile/static/src/xml/bank_reconciliation.xml', assets)
        self.assertIn('taj_account_reconcile/static/src/scss/bank_reconciliation.scss', assets)
        test_assets = self._read_manifest()['assets']['web.assets_tests']
        self.assertIn('taj_account_reconcile/static/tests/tours/**/*', test_assets)

    def test_button_list_dropdown_template_extension(self):
        content = self._read_asset('static/src/xml/bank_reconciliation.xml')
        self.assertIn('t-inherit="account_accountant.BankRecButtonListDropdown"', content)
        self.assertIn('t-inherit-mode="extension"', content)
        self.assertIn('BankRecFileUploader', content)
        self.assertIn('DropdownItem', content)
        self.assertIn('Edit Company Equivalent', content)
        self.assertIn('canEditCurrencyAmount', content)
        self.assertIn('onSelected.bind="actionEditCurrencyAmount"', content)
        self.assertNotIn('position="replace"', content)

    def test_line_to_reconcile_extension_labels_native_edit_button(self):
        content = self._read_asset('static/src/xml/bank_reconciliation.xml')
        self.assertIn('t-inherit="account_accountant.BankRecLineToReconcile"', content)
        self.assertIn('Set Amount', content)
        self.assertIn('taj-set-amount', content)
        self.assertIn('btn-outline-secondary', content)
        self.assertIn('text-nowrap', content)
        self.assertIn('name="data-tooltip"', content)
        # The native toggleEditLine behaviour must only be decorated, not replaced.
        self.assertNotIn('position="replace"', content)

    def test_set_amount_layout_css_keeps_action_on_one_line(self):
        content = self._read_asset('static/src/scss/bank_reconciliation.scss')
        self.assertIn('grid-template-columns', content)
        self.assertIn('128px', content)
        self.assertIn('.taj-set-amount', content)
        self.assertIn('white-space: nowrap', content)

    def test_button_list_js_patch(self):
        content = self._read_asset('static/src/js/bank_reconciliation.js')
        self.assertIn(
            '@account_accountant/components/bank_reconciliation/button_list/button_list',
            content)
        self.assertIn('@web/core/utils/patch', content)
        self.assertIn('BankRecButtonList.prototype', content)
        self.assertIn('action_open_taj_currency_amount_wizard', content)
        self.assertIn('onClose', content)
        self.assertIn('this.props.statementLine.load()', content)
        self.assertIn('this.statementLineData.taj_can_edit_company_equivalent', content)
        self.assertIn('non-stored server field', content)

    # -------------------------------------------------------------------------
    # SERVER VIEWS
    # -------------------------------------------------------------------------

    def test_bank_rec_kanban_loads_company_equivalent_eligibility(self):
        view = self.env.ref(
            'taj_account_reconcile.view_bank_statement_line_kanban_taj_company_equivalent')
        self.assertEqual(
            view.inherit_id,
            self.env.ref('account_accountant.view_bank_statement_line_kanban_bank_rec_widget'))
        self.assertEqual(view.model, 'account.bank.statement.line')
        self.assertIn('taj_can_edit_company_equivalent', view.arch_db)
        self.assertIn("foreign_currency_id", view.arch_db)

    def test_browser_tour_is_registered_in_test_assets(self):
        content = self._read_asset('static/tests/tours/test_reconciliation_tour.js')
        self.assertIn('taj_account_reconcile_browser', content)
        self.assertIn('taj-edit-company-equivalent', content)
        self.assertIn('Edit Company Equivalent', content)
        self.assertIn('Set Amount', content)
        self.assertIn("div[name='company_amount'] input", content)
        self.assertIn("div[name='balance'] input", content)

    def test_native_edit_line_view_keeps_editable_amounts(self):
        native_view = self.env.ref('account_accountant.view_bank_rec_edit_line')
        self.assertEqual(native_view.type, 'form')
        arch = native_view.arch_db
        self.assertIn('name="balance"', arch)
        self.assertIn('name="amount_currency"', arch)

    def test_edit_line_help_view_is_installed(self):
        help_view = self.env.ref('taj_account_reconcile.view_bank_rec_edit_line_taj_help')
        self.assertEqual(
            help_view.inherit_id, self.env.ref('account_accountant.view_bank_rec_edit_line'))
        self.assertEqual(help_view.model, 'account.move.line')
        self.assertIn('//form/sheet', help_view.arch_db)
        self.assertIn("//label[@for='balance']", help_view.arch_db)
        self.assertIn('Amount to Apply', help_view.arch_db)
        self.assertIn('role="status"', help_view.arch_db)
        self.assertIn('same accounting sign', help_view.arch_db)
        self.assertIn('-3,000 USD', help_view.arch_db)

    def test_wizard_view_configuration(self):
        wizard_view = self.env.ref(
            'taj_account_reconcile.view_bank_statement_currency_amount_wizard_form')
        self.assertEqual(wizard_view.model, 'taj.bank.statement.currency.amount.wizard')
        arch = wizard_view.arch_db
        for field_name in (
            'source_amount', 'source_currency_id',
            'company_amount', 'company_currency_id', 'effective_rate',
        ):
            self.assertIn(field_name, arch)
        self.assertIn('action_apply', arch)
        self.assertIn('special="cancel"', arch)
        invisible_field_reasons = {
            'statement_line_id': 'revalidate the bank transaction at save time',
            'company_amount_field': 'detect currency setup changes after opening',
        }
        for field_name, reason in invisible_field_reasons.items():
            self.assertRegex(
                arch,
                rf'<field name="{field_name}" invisible="1"\s*/>\s*'
                rf'<!-- Required by action_apply to {reason}\. -->',
            )

    def test_wizard_access_is_restricted_to_accountants(self):
        access = self.env.ref(
            'taj_account_reconcile.access_bank_statement_currency_amount_wizard_user')
        self.assertEqual(
            access.group_id, self.env.ref('account.group_account_user'))
        self.assertTrue(access.perm_read)
        self.assertTrue(access.perm_write)
        self.assertTrue(access.perm_create)
