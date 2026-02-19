/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Client action to print a cheque and download its voucher.
 * Expects action.params: { cheque_id: int, cheque_number: string }
 */
registry.category("actions").add("print_cheque_action", async (env, action) => {

    // Validate required parameters
    if (!action?.params?.cheque_id) {
        console.error("Cheque ID not found in action params!", action);
        return;
    }

    const chequeId = action.params.cheque_id;
    const chequeNumber = action.params.cheque_number;   // Used for naming the downloaded file

    // Report URLs
    const chequeUrl = `/report/pdf/account_cheque_printing.report_cheque_pdf/${chequeId}`;
    const voucherUrl = `/report/pdf/account_cheque_printing.report_cheque_voucher/${chequeId}?download=true`;

    try {
        // Open cheque PDF in a new browser tab
        window.open(chequeUrl, "_blank");

        // Fetch voucher PDF and trigger download with a custom filename
        const response = await fetch(voucherUrl);
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${chequeNumber}.pdf`;   // Filename based on cheque number
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
    } catch (error) {
        console.error("Error while executing print action:", error);
    }
});

