from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_amount


class BankStatementCurrencyAmountWizard(models.TransientModel):
    _name = 'taj.bank.statement.currency.amount.wizard'
    _description = 'Edit Bank Transaction Company-Currency Equivalent'

    statement_line_id = fields.Many2one(
        comodel_name='account.bank.statement.line',
        string='Bank Transaction',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    company_id = fields.Many2one(
        related='statement_line_id.company_id',
    )
    source_amount = fields.Monetary(
        string='Transaction Amount',
        currency_field='source_currency_id',
        readonly=True,
    )
    source_currency_id = fields.Many2one(
        comodel_name='res.currency',
        string='Transaction Currency',
        required=True,
        readonly=True,
    )
    company_amount = fields.Monetary(
        string='Company Equivalent',
        currency_field='company_currency_id',
        required=True,
    )
    company_currency_id = fields.Many2one(
        comodel_name='res.currency',
        string='Company Currency',
        required=True,
        readonly=True,
    )
    company_amount_field = fields.Selection(
        selection=[
            ('amount', "Amount"),
            ('amount_currency', "Amount in Currency"),
        ],
        string='Company Equivalent Field',
        required=True,
        readonly=True,
        help="Technical field telling which standard bank transaction field "
             "stores the company-currency equivalent.",
    )
    effective_rate = fields.Float(
        string='Effective Rate',
        compute='_compute_effective_rate',
        digits=(16, 6),
        readonly=True,
        help="Company-currency amount for one unit of the transaction currency.",
    )

    @api.depends('source_amount', 'company_amount', 'source_currency_id')
    def _compute_effective_rate(self):
        for wizard in self:
            if wizard.source_currency_id \
                    and not wizard.source_currency_id.is_zero(wizard.source_amount):
                wizard.effective_rate = \
                    abs(wizard.company_amount) / abs(wizard.source_amount)
            else:
                wizard.effective_rate = 0.0

    def action_apply(self):
        self.ensure_one()
        statement_line = self.statement_line_id
        statement_line._check_currency_amount_edit_allowed()
        statement_line._check_currency_amount_edit_state()
        info = statement_line._get_currency_amount_edit_info()
        if info['company_amount_field'] != self.company_amount_field:
            raise UserError(_(
                "The currency setup of this bank transaction changed. "
                "Close this dialog and open it again."
            ))
        company_currency = statement_line.company_id.currency_id
        statement_line._check_currency_amount_edit_values(
            info['source_amount'], info['source_currency'],
            self.company_amount, company_currency,
        )
        old_company_amount = info['company_amount']
        if info['company_amount_field'] == 'amount_currency':
            statement_line.write({
                'foreign_currency_id': company_currency.id,
                'amount_currency': self.company_amount,
            })
        else:
            statement_line.write({
                'amount': self.company_amount,
            })
        if not company_currency.is_zero(self.company_amount - old_company_amount):
            statement_line.move_id.message_post(body=self._build_audit_message(
                old_company_amount, self.company_amount, company_currency))
        return {'type': 'ir.actions.act_window_close'}

    def _build_audit_message(self, old_amount, new_amount, currency):
        message = _(
            "Company-currency equivalent changed from %(old)s to %(new)s.",
            old=format_amount(self.env, old_amount, currency),
            new=format_amount(self.env, new_amount, currency),
        )
        # Markup escapes the substituted message while keeping the paragraph.
        return Markup("<p>%s</p>") % message
