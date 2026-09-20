/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { BankRecStatementLine } from "@account_accountant/components/bank_reconciliation/statement_line/statement_line";

patch(BankRecStatementLine.prototype, {
    /**
     * Tell whether the transaction carries a company-currency equivalent that
     * an accountant can edit.
     *
     * @returns {boolean}
     */
    get hasEditableCompanyCurrency() {
        const companyCurrencyId = this.recordData.company_id?.currency_id?.id;
        const journalCurrencyId = this.recordData.currency_id?.id;
        const foreignCurrencyId = this.recordData.foreign_currency_id?.id;
        if (!companyCurrencyId || !journalCurrencyId) {
            return false;
        }
        if (journalCurrencyId !== companyCurrencyId) {
            // Foreign-currency journal: the company currency must be the only
            // possible other currency of the transaction.
            return !foreignCurrencyId || foreignCurrencyId === companyCurrencyId;
        }
        // Company-currency journal: a foreign-currency transaction is required.
        return !!foreignCurrencyId && foreignCurrencyId !== companyCurrencyId;
    },

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
        return this.hasEditableCompanyCurrency;
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
