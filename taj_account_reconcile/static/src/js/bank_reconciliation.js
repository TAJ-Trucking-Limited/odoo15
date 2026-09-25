/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { BankRecButtonList } from "@account_accountant/components/bank_reconciliation/button_list/button_list";

patch(BankRecButtonList.prototype, {
    /**
     * Keep the client-side condition intentionally small. The server action is
     * the authoritative safety boundary and re-checks permissions, review /
     * reconciliation state, counterpart lines, and the supported FX setup.
     *
     * @returns {boolean}
     */
    get canEditCurrencyAmount() {
        return !this.statementLineData.checked && !this.statementLineData.is_reconciled;
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
