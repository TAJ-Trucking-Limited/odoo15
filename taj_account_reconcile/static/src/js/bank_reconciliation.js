/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { BankRecStatementLine } from "@account_accountant/components/bank_reconciliation/statement_line/statement_line";

patch(BankRecStatementLine.prototype, {
    /**
     * Only accountants may open the wizard, only before the transaction is
     * reviewed/reconciled and only while no counterpart line exists.
     *
     * @returns {boolean}
     */
    get canEditCurrencyAmount() {
        if (!this.userCanReview) {
            return false;
        }
        if (this.recordData.checked || this.recordData.is_reconciled) {
            return false;
        }
        if (this.linesToReconcile.length) {
            return false;
        }
        // Do not depend on company_id.currency_id being loaded in the kanban
        // record. Odoo 19 does not expose that nested value consistently in
        // the reconciliation card. The server action re-checks the currency
        // setup and rejects company-currency-only / unsupported transactions.
        return true;
    },

    /**
     * Open the guarded modal wizard and reload the transaction when it closes
     * so both displayed amounts reflect the saved value.
     */
    async actionEditCurrencyAmount() {
        const action = await this.orm.call(
            "account.bank.statement.line",
            "action_open_taj_currency_amount_wizard",
            [this.recordData.id]
        );
        await this.action.doAction(action, {
            onClose: () => this.record.load(),
        });
    },
});
