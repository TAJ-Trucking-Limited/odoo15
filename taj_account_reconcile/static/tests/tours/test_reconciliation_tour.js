import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("taj_account_reconcile_browser", {
    steps: () => [
        {
            trigger: "div.o_bank_reconciliation_kanban_renderer",
        },
        {
            content: "Open actions for the eligible FX transaction",
            trigger: ".o_statement_line:has(.o_payment_ref:contains('TAJ eligible FX')) button.btn-secondary:has(i.oi-ellipsis-v)",
            run: "click",
        },
        {
            content: "The company-equivalent action is visible only when server eligibility is true",
            trigger: ".dropdown-menu .taj-edit-company-equivalent:contains('Edit Company Equivalent')",
            run: "click",
        },
        {
            content: "The company-equivalent wizard opens",
            trigger: "div.modal-dialog div[name='company_amount'] input",
        },
        {
            content: "The original transaction amount is shown read-only",
            trigger: "div.modal-dialog div[name='source_amount']",
        },
        {
            content: "Close the company-equivalent wizard",
            trigger: "div.modal-dialog button.btn-secondary:contains('Cancel')",
            run: "click",
        },
        {
            content: "Open actions for the unsupported third-currency transaction",
            trigger: ".o_statement_line:has(.o_payment_ref:contains('TAJ ineligible third currency')) button.btn-secondary:has(i.oi-ellipsis-v)",
            run: "click",
        },
        {
            content: "Unsupported transactions do not expose the company-equivalent action",
            trigger: "body:has(.dropdown-item:contains('Delete Transaction')):not(:has(.taj-edit-company-equivalent))",
        },
        {
            content: "Close the unsupported transaction menu",
            trigger: ".o_statement_line:has(.o_payment_ref:contains('TAJ ineligible third currency')) button.btn-secondary:has(i.oi-ellipsis-v)",
            run: "click",
        },
        {
            content: "Native invoice-allocation editing is exposed as Set Amount",
            trigger: ".o_statement_line:has(.o_payment_ref:contains('TAJ partial allocation')) button.taj-set-amount:has(span:contains('Set Amount'))",
            run: "click",
        },
        {
            content: "The native reconciliation edit dialog opens",
            trigger: "div.modal-dialog div[name='balance'] input",
        },
        {
            content: "The TAJ help explains partial invoice allocation",
            trigger: "div.modal-dialog .alert-info:contains('exact amount applied to the invoice')",
        },
        {
            content: "Close the native amount editor",
            trigger: "div.modal-dialog button.o_form_button_cancel:contains('Discard')",
            run: "click",
        },
    ],
});
