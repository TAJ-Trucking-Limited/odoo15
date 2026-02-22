/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Client action to:
 * 1) Open the cheque PDF inside an HTML page with a fixed browser tab title
 * 2) Download the related voucher PDF using a custom filename
 *
 * This approach avoids the browser overriding the tab title,
 * which normally happens when opening PDF files directly.
 */
registry.category("actions").add("print_cheque_action", async (env, action) => {

    // Validate required action parameters
    if (!action?.params?.cheque_id) {
        console.error("Cheque ID not found in action params!", action);
        return;
    }

    // Extract cheque data from action parameters
    const chequeId = action.params.cheque_id;
    const chequeNumber = action.params.cheque_number;

    // Build report URLs
    const chequeUrl = `/report/pdf/account_cheque_printing.report_cheque_pdf/${chequeId}?filename=Cheque-${chequeNumber}.pdf`;
    const voucherUrl = `/report/pdf/account_cheque_printing.report_cheque_voucher/${chequeId}?download=true`;

    try {
        /* =====================================================
           Open a new HTML page with a fixed and custom title
           The PDF is embedded in an iframe to prevent the browser
           from overriding the tab title with PDF metadata.
        ===================================================== */
        const win = window.open("", "_blank");

        // Write a minimal HTML document to control the tab title
        win.document.write(`
            <html>
                <head>
                    <!-- Set a custom and stable browser tab title -->
                    <title>Cheque-${chequeNumber}</title>

                    <!-- Ensure the PDF iframe fills the entire page -->
                    <style>
                        html, body {
                            margin: 0;
                            height: 100%;
                        }
                        iframe {
                            width: 100%;
                            height: 100%;
                            border: none;
                        }
                    </style>
                </head>
                <body>
                    <!-- Embed the cheque PDF inside an iframe -->
                    <iframe src="${chequeUrl}"></iframe>
                </body>
            </html>
        `);

        // Finalize the document writing
        win.document.close();

        /* =====================================================
           Download the voucher PDF with a custom filename
           The filename is based on the cheque number.
        ===================================================== */
        const response = await fetch(voucherUrl);
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);

        // Create a temporary anchor to trigger the download
        const link = document.createElement("a");
        link.href = url;
        link.download = `Voucher-${chequeNumber}.pdf`;
        document.body.appendChild(link);
        link.click();

        // Clean up temporary elements and object URL
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);

    } catch (error) {
        // Handle any unexpected errors during the process
        console.error("Error while executing print cheque action:", error);
    }
});

