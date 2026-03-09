from odoo import models, fields


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    cheque_id = fields.Many2one(
        'account.cheque',
        string='Cheque',
        readonly=True,
        copy=False,
        tracking=True
    )

    cheque_number = fields.Char(
        string='Cheque Number',
        copy=False,
        tracking=True
    )

    cheque_reason_note = fields.Text(
        string='Cheque Reason / Note',
        tracking=True
    )

    cheque_printed = fields.Boolean(
        string='Cheque Printed',
        default=False,
        copy=False,
        tracking=True
    )

    cheque_print_date = fields.Datetime(
        string='Cheque Print Date',
        readonly=True,
        copy=False,
        tracking=True
    )

    cheque_print_count = fields.Integer(
        string='Cheque Print Count',
        default=0,
        copy=False,
        tracking=True
    )

    cheque_amount = fields.Monetary(
        string='Cheque Amount',
        currency_field='currency_id',
        readonly=True,
        copy=False,
        tracking=True
    )
    cheque_payee_name = fields.Char(
        string='Cheque Payee Name',
        tracking=True,
        copy=False
    )
