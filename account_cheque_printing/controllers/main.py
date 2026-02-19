from odoo import http
from odoo.http import request

class ChequePrintController(http.Controller):

    @http.route(
        '/cheque/print_and_download/<int:cheque_id>',
        type='http',
        auth='user'
    )
    def print_and_download(self, cheque_id, **kwargs):
        """
        Controller method to handle printing and downloading a cheque along with its voucher.

        Args:
            cheque_id (int): The ID of the cheque to be printed and downloaded.
            **kwargs: Additional keyword arguments (not used here).
        """

        # Construct the URL for the cheque PDF report (will be opened in a new tab)
        cheque_pdf_url = (
            f"/report/pdf/"
            f"account_cheque_printing.report_cheque_pdf/"
            f"{cheque_id}"
        )

        # Construct the URL for the voucher PDF report (will be downloaded automatically)
        voucher_pdf_url = (
            f"/report/pdf/"
            f"account_cheque_printing.report_cheque_voucher/"
            f"{cheque_id}?download=true"
        )

        # Return a small HTML page with JavaScript to:
        # 1. Automatically download the voucher PDF
        # 2. Open the cheque PDF in a new browser tab
        # 3. Close the current (temporary) page after actions are triggered
        return f"""
        <html>
            <body>
                <script>
                    // Create a temporary link element to download the voucher PDF
                    var link = document.createElement('a');
                    link.href = '{voucher_pdf_url}';
                    link.download = '';  // Trigger download
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);

                    // Open the cheque PDF in a new browser tab
                    window.open('{cheque_pdf_url}', '_blank');

                    // Close the temporary page
                    window.close();
                </script>
            </body>
        </html>
        """
