from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ChequeWizard(models.TransientModel):
    _name = 'account.cheque.wizard'
    _description = 'Cheque Printing Wizard'

    payment_id = fields.Many2one('account.payment')
    cheque_number = fields.Char(required=True, help="Unique cheque number.")
    payee_id = fields.Many2one(
        'res.partner',
        string='Payee',
        required=True,
        help="Partner receiving the cheque."
    )
    amount = fields.Monetary(required=True, help="Amount written on the cheque.")
    currency_id = fields.Many2one(
        'res.currency',
        required=True,
        help="Currency of the cheque amount.",
    )
    reason_note = fields.Text(required=True, help="A mandatory note explaining the reason for issuing the check.")

    amount_in_words = fields.Char(
        string="Amount in Words",
        compute='_compute_amount_in_words',
        store=True,
        help="Amount written in words.",
    )

    preview_date = fields.Date(string='Cheque Date', default=fields.Date.context_today)

    @api.depends('amount', 'currency_id')
    def _compute_amount_in_words(self):
        """Convert numeric amount into words automatically"""
        for rec in self:
            if rec.amount and rec.currency_id:
                rec.amount_in_words = rec._convert_amount_to_words(rec.amount, rec.currency_id)
            else:
                rec.amount_in_words = ''

    @api.model
    def default_get(self, fields_list):
        """Prefill wizard data from payment or existing cheque."""
        res = super().default_get(fields_list)
        payment = self.env['account.payment'].browse(self.env.context.get('active_id'))
        if not payment:
            return res

        # If cheque already exists → load its data
        if payment.cheque_id:
            cheque = payment.cheque_id
            res.update({
                'payment_id': payment.id,
                'cheque_number': cheque.cheque_number,
                'payee_id': cheque.payee_id.id if cheque.payee_id else False,
                'amount': cheque.amount,
                'currency_id': cheque.currency_id.id,
                'reason_note': cheque.reason_note or '',
                'preview_date': cheque.print_date or fields.Date.context_today(self),
            })
        else:
            # Otherwise prefill from payment
            res.update({
                'payment_id': payment.id,
                'cheque_number': payment.cheque_number or '',
                'payee_id': payment.partner_id.id,
                'amount': payment.amount,
                'currency_id': payment.currency_id.id,
                'reason_note': payment.cheque_reason_note or '',
                'preview_date': fields.Date.context_today(self),
            })
        return res

    def action_print_cheque_and_voucher(self):
        """Create / reprint cheque and return report action."""
        self.ensure_one()
        payment = self.payment_id
        Cheque = self.env['account.cheque']

        # Ensure cheque number is globally unique
        domain = [('cheque_number', '=', self.cheque_number)]
        if payment:
            domain.append(('id', '!=', payment.cheque_id.id if payment.cheque_id else 0))
        existing = Cheque.search(domain, limit=1)
        if existing:
            raise UserError(_("Cheque number already exists. It must be unique."))


        if not self.amount_in_words:
            self.amount_in_words = self._convert_amount_to_words(self.amount, self.currency_id)

        if payment:
            # First time printing
            if not payment.cheque_id:
                payee_note = False
                if self.payee_id != payment.partner_id:
                    payee_note = (
                        f"Payee name was changed from "
                        f"'{payment.partner_id.name}' to '{self.payee_id.name}'."
                    )
                cheque = Cheque.create({
                    'cheque_number': self.cheque_number,
                    'payment_id': payment.id,
                    'journal_id': payment.journal_id.id,
                    'payee_id': self.payee_id.id,
                    'amount': self.amount,
                    'currency_id': self.currency_id.id,
                    'reason_note': self.reason_note,
                    'amount_in_words': self.amount_in_words,
                    'state': 'printed',
                    'print_date': fields.Datetime.now(),
                    'print_count': 1,
                    'payee_change_note': payee_note,
                    'cheque_type': 'payment_cheque',
                })
                payment.write({
                    'cheque_id': cheque.id,
                    'cheque_number': self.cheque_number,
                    'cheque_reason_note': self.reason_note,
                    'cheque_printed': True,
                    'cheque_print_date': fields.Datetime.now(),
                    'cheque_print_count': 1,
                    'cheque_amount': self.amount,
                    'cheque_payee_name': self.payee_id.name,
                })
            else:
                # Reprint or modified cheque
                cheque = payment.cheque_id
                number_changed = cheque.cheque_number != self.cheque_number
                data_changed = (
                    cheque.payee_id != self.payee_id or
                    cheque.reason_note != self.reason_note or
                    cheque.amount != self.amount
                )
                # Prevent modifying cheque data without changing number
                if data_changed and not number_changed:
                    raise UserError(_(
                        "You modified cheque data. "
                        "You must use a new cheque number."
                    ))
                if number_changed or data_changed:
                    # Create new cheque version
                    payee_note = False
                    if self.payee_id != payment.partner_id:
                        payee_note = (
                            f"Payee name was changed from "
                            f"'{payment.partner_id.name}' to '{self.payee_id.name}'."
                        )
                    cheque = Cheque.create({
                        'cheque_number': self.cheque_number,
                        'payment_id': payment.id,
                        'journal_id': payment.journal_id.id,
                        'payee_id': self.payee_id.id,
                        'amount': self.amount,
                        'currency_id': self.currency_id.id,
                        'reason_note': self.reason_note,
                        'amount_in_words': self.amount_in_words,
                        'state': 'printed',
                        'print_date': fields.Datetime.now(),
                        'print_count': 1,
                        'payee_change_note': payee_note,
                        'cheque_type': 'payment_cheque',
                    })
                    payment.write({
                        'cheque_id': cheque.id,
                        'cheque_number': self.cheque_number,
                        'cheque_reason_note': self.reason_note,
                        'cheque_printed': True,
                        'cheque_print_date': fields.Datetime.now(),
                        'cheque_print_count': 1,
                        'cheque_amount': self.amount,
                        'cheque_payee_name': self.payee_id.name,
                    })
                else:
                    # Simple reprint → increment counter
                    cheque.write({
                        'print_count': cheque.print_count + 1,
                        'print_date': fields.Datetime.now(),
                        'amount_in_words': self.amount_in_words,
                    })
                    payment.write({
                        'cheque_print_count': payment.cheque_print_count + 1,
                        'cheque_print_date': fields.Datetime.now(),
                    })
        else:
            # Manual cheque (not linked to payment)

            cheque = Cheque.create({
                'cheque_number': self.cheque_number,
                'payee_id': self.payee_id.id,
                'amount': self.amount,
                'currency_id': self.currency_id.id,
                'reason_note': self.reason_note,
                'amount_in_words': self.amount_in_words,
                'state': 'printed',
                'print_date': fields.Datetime.now(),
                'print_count': 1,
                'cheque_type': 'manual_cheque',
            })
        return {
            'type': 'ir.actions.client',
            'tag': 'print_cheque_action',
            'params': {
                'cheque_id': cheque.id,
                'cheque_number': cheque.cheque_number,
            }
        }

    def _convert_amount_to_words(self, amount, currency):
        """Convert numeric amount into English words."""
        if not amount or not currency:
            return ''
        text = currency.with_context(lang='en_US').amount_to_text(amount)
        text = text.replace(currency.symbol or '', '').strip()
        return f"{text} Only"

    @api.onchange('amount', 'currency_id')
    def _onchange_amount_in_words(self):
        """Auto-update amount in words when amount or currency changes."""
        for rec in self:
            if rec.amount and rec.currency_id:
                rec.amount_in_words = rec._convert_amount_to_words(
                    rec.amount,
                    rec.currency_id
                )
            else:
                rec.amount_in_words = ''

    def action_preview(self):
        """Open cheque preview in a new browser tab."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/cheque/preview/{self.id}',
            'target': 'new',
        }
