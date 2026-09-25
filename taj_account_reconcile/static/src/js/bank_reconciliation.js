/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { BankRecButtonList } from "@account_accountant/components/bank_reconciliation/button_list/button_list";

patch(BankRecButtonList.prototype, {
    /**
     * Visibility is decided by the non-stored server field loaded by the native
     * Odoo 19 bank-reconciliation kanban. The action still revalidates all
     * conditions when clicked and again when the wizard is applied.
     *
     * @returns {boolean}
     */
    get canEditCurrencyAmount() {
        return Boolean(this.statementLineData.taj_can_edit_company_equivalent);
    },

    /**
     * Open the guarded modal wizard and reload the transaction when it closes
     * so both displayed amounts reflect the saved value.
     */
    async actionEditCurrencyAmount() {
        const action = await this.orm.call(
            "account.bank.statement.line",
            "action_open_taj_currency_amount_wizard",
            [this.statementLineData.id]
        );
        await this.action.doAction(action, {
            onClose: () => this.props.statementLine.load(),
        });
    },
});
