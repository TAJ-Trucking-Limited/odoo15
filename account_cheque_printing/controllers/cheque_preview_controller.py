from odoo import http
from odoo.http import request

class ChequePreviewController(http.Controller):
    """
    Controller responsible for rendering the cheque preview page.
    It retrieves data from the cheque wizard and passes it to a QWeb template.
    """

    @http.route('/cheque/preview/<int:wizard_id>', type='http', auth='user', website=False)
    def cheque_preview(self, wizard_id, **kwargs):
        """
        Render cheque preview based on the provided wizard record.

        :param wizard_id: ID of the account.cheque.wizard record
        :return: Rendered QWeb template with cheque preview data
        """

        # Fetch the cheque wizard record using the provided ID
        wizard = request.env['account.cheque.wizard'].browse(wizard_id)
        # Validate that the wizard record exists
        if not wizard.exists():
            return request.not_found("Cheque wizard not found!")
        # Prepare the document dictionary to be passed to the QWeb template
        # This structure mimics the standard 'docs' pattern used in reports
        doc = {
            'payee_name': wizard.payee_id.name if wizard.payee_id else '',
            'amount': wizard.amount,
            'amount_in_words': wizard.amount_in_words,
            'date': wizard.preview_date,
            'currency_code': wizard.currency_id.name if wizard.currency_id else '',
        }
        # Render the preview template and pass the prepared data
        return request.render('account_cheque_printing.report_cheque_preview', {
            'docs': [doc]
        })
