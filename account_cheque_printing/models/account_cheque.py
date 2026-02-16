from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AccountCheque(models.Model):
    _name = 'account.cheque'
    _description = 'Cheque'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        string='Cheque Reference',
        readonly=True,
        copy=False,
        help="System-generated internal reference for the cheque."
    )

    cheque_number = fields.Char(
        string='Cheque Number',
        required=True,
        tracking=True,
        copy=False,
        help="Official cheque number. Must be unique across all cheques."
    )

    payment_id = fields.Many2one(
        'account.payment',
        string='Payment',
        ondelete='restrict',
        help="Payment linked to this cheque, if applicable."
    )

    journal_id = fields.Many2one(
        'account.journal',
        string='Bank Journal',
        help="Bank journal from which the cheque is issued."
    )

    payee_id = fields.Many2one(
        'res.partner',
        string='Payee',
        required=True,
        tracking=True,
        help="Partner who will receive the cheque."

    )

    amount = fields.Monetary(
        string='Cheque Amount',
        required=True,
        help="Total amount written on the cheque."
    )

    currency_id = fields.Many2one(
        'res.currency',
        required=True,
        help="Currency used for the cheque amount."
    )

    reason_note = fields.Text(
        string='Reason / Note',
        required=True,
        help="A mandatory note explaining the reason for issuing the check."
    )

    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('printed', 'Printed'),
        ],
        default='draft',
        tracking=True
    )

    print_date = fields.Datetime(
        string='Print Date',
        tracking=True,
        help="Date and time when the cheque was printed."
    )

    print_count = fields.Integer(
        string='Print Count',
        default=0,
        tracking=True,
        help="Number of times this cheque has been printed."
    )
    amount_in_words = fields.Char(string="Amount in Words", help="Cheque amount written in words.")

    payee_change_note = fields.Text(
        string='Payee Change Note',
        tracking=True
    )
    partner_comparison_note = fields.Text(
        compute='_compute_partner_note',
        store=True
    )
    cheque_type = fields.Selection([
        ('payment_cheque', 'Payment Cheque'),
        ('manual_cheque', 'Manual Cheque')
    ], string='Cheque Type', default='manual_cheque', required=True)

    @api.model_create_multi
    def create(self, vals):
        # Override create to generate internal reference
        cheque = super().create(vals)
        cheque.name = f"CH/{cheque.cheque_number}"
        return cheque

    @api.depends('payment_id.partner_id', 'payee_id')
    def _compute_partner_note(self):
        # Generate comparison note if payment partner differs from cheque payee
        for rec in self:
            if rec.payment_id and rec.payee_id and rec.payee_id != rec.payment_id.partner_id:
                rec.partner_comparison_note = (
                    f"Payment partner: {rec.payment_id.partner_id.name} | "
                    f"Cheque payee: {rec.payee_id.name}"
                )
            else:
                rec.partner_comparison_note = False

    @api.constrains('cheque_number')
    def _check_unique_cheque_number_global(self):
        # Ensure cheque number is globally unique
        for rec in self:
            if not rec.cheque_number:
                continue

            exists = self.search([
                ('cheque_number', '=', rec.cheque_number),
                ('id', '!=', rec.id),
            ], limit=1)

            if exists:
                raise ValidationError(
                    _("Cheque number '%s' is already used and must be unique.")
                    % rec.cheque_number
                )