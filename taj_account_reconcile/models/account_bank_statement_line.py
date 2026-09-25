from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    taj_can_edit_company_equivalent = fields.Boolean(
        string='Can Edit Company Equivalent',
        compute='_compute_taj_can_edit_company_equivalent',
        compute_sudo=False,
        help=(
            "Technical UI flag. It is true only when the current user may edit "
            "the company-currency equivalent and the bank transaction is in a "
            "safe supported FX state."
        ),
    )

    def _compute_taj_can_edit_company_equivalent(self):
        user_is_accountant = self.env.user.has_group('account.group_account_user')
        allowed_companies = self.env.companies
        for statement_line in self:
            statement_line.taj_can_edit_company_equivalent = False
            if not user_is_accountant or statement_line.company_id not in allowed_companies:
                continue
            try:
                statement_line._check_currency_amount_edit_state()
                info = statement_line._get_currency_amount_edit_info()
                statement_line._check_currency_amount_edit_values(
                    info['source_amount'],
                    info['source_currency'],
                    info['company_amount'],
                    statement_line.company_id.currency_id,
                )
            except UserError:
                continue
            statement_line.taj_can_edit_company_equivalent = True

    # -------------------------------------------------------------------------
    # SAFETY HELPERS
    # -------------------------------------------------------------------------

    def _check_currency_amount_edit_allowed(self):
        """Access check shared by the action and by the wizard at apply time."""
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_user'):
            raise AccessError(_(
                "Only accounting users can edit the company-currency equivalent "
                "of a bank transaction."
            ))
        self.check_access('write')
        if self.company_id not in self.env.companies:
            raise AccessError(_(
                "You are not allowed to edit bank transactions of this company."
            ))

    def _check_currency_amount_edit_state(self):
        """Reject reconciled transactions or transactions with counterpart lines.

        In Odoo 19, ``checked=True`` is also the normal state behind the
        ``Not Matched`` filter.  A reviewed-but-unreconciled transaction is
        therefore still editable as long as no reconciliation/counterpart lines
        exist yet.
        """
        self.ensure_one()
        if self.is_reconciled:
            raise UserError(_(
                "This bank transaction is already reconciled. Undo its "
                "reconciliation before editing its company-currency equivalent."
            ))
        _liquidity_lines, suspense_lines, other_lines = self._seek_for_lines()
        if other_lines or any(
            line.matched_debit_ids or line.matched_credit_ids
            for line in suspense_lines
        ):
            raise UserError(_(
                "This bank transaction already has reconciliation lines. "
                "Remove them and edit the company-currency equivalent before "
                "selecting the invoices again."
            ))

    def _get_currency_amount_edit_info(self):
        """Return the source and company amounts and the field receiving the edit.

        Two safe arrangements are supported:

        * a foreign-currency journal: ``amount`` is the transaction amount and
          the company equivalent is written to ``amount_currency`` while
          ``foreign_currency_id`` is set to the company currency;
        * a company-currency journal with a foreign currency: ``amount_currency``
          is the transaction amount and the company equivalent is ``amount``.
        """
        self.ensure_one()
        company_currency = self.company_id.currency_id
        journal_currency = self.currency_id
        foreign_currency = self.foreign_currency_id
        if journal_currency != company_currency:
            # Foreign-currency journal, e.g. a USD journal in a TZS company.
            if foreign_currency and foreign_currency != company_currency:
                raise UserError(_(
                    "This bank transaction uses the third currency %(foreign)s "
                    "next to the journal currency %(journal)s. Only the company "
                    "currency %(company)s can be entered as an equivalent.",
                    foreign=foreign_currency.display_name,
                    journal=journal_currency.display_name,
                    company=company_currency.display_name,
                ))
            if foreign_currency:
                company_amount = self.amount_currency
            else:
                company_amount = journal_currency._convert(
                    self.amount, company_currency, self.company_id, self.date)
            return {
                'source_amount': self.amount,
                'source_currency': journal_currency,
                'company_amount': company_amount,
                'company_amount_field': 'amount_currency',
            }
        # Company-currency journal carrying a foreign-currency transaction.
        if not foreign_currency:
            raise UserError(_(
                "This bank transaction is expressed in the company currency "
                "only. There is no company-currency equivalent to edit."
            ))
        if foreign_currency == company_currency:
            raise UserError(_(
                "The transaction currency and the company currency are the same. "
                "There is no company-currency equivalent to edit."
            ))
        return {
            'source_amount': self.amount_currency,
            'source_currency': foreign_currency,
            'company_amount': self.amount,
            'company_amount_field': 'amount',
        }

    def _check_currency_amount_edit_values(self, source_amount, source_currency,
                                           company_amount, company_currency):
        """Reject zero or opposite-sign amounts using the currency precision."""
        if source_currency.is_zero(source_amount):
            raise UserError(_("The transaction amount cannot be zero."))
        if company_currency.is_zero(company_amount):
            raise UserError(_("The company-currency equivalent cannot be zero."))
        if (source_amount > 0) != (company_amount > 0):
            raise UserError(_(
                "The company-currency equivalent must keep the same sign as the "
                "transaction amount."
            ))

    def _check_reconcile_line_amount_sign(self, move_line, record_data):
        """Prevent accidental sign flips when partially allocating an invoice.

        Odoo's reconciliation editor exposes accounting-signed values.  Changing
        only the magnitude is valid, but flipping the sign turns a partial
        allocation into an amount in the opposite direction and can increase the
        residual instead of reducing it.
        """
        self.ensure_one()
        if not move_line.reconciled_lines_excluding_exchange_diff_ids:
            return

        fields_to_check = (
            ('balance', self.company_id.currency_id),
            ('amount_currency', move_line.currency_id),
        )
        for field_name, currency in fields_to_check:
            if field_name not in record_data:
                continue
            current_amount = move_line[field_name]
            new_amount = record_data[field_name]
            if currency.is_zero(current_amount) or currency.is_zero(new_amount):
                continue
            if (current_amount > 0) != (new_amount > 0):
                raise UserError(_(
                    "Keep the same accounting sign as the selected invoice line. "
                    "Change only the amount, not its direction. For example, if "
                    "the line currently shows -5,150, enter -3,000 to apply "
                    "3,000; do not enter +3,000."
                ))

    def edit_reconcile_line(self, move_line_id, record_data):
        """Keep native Odoo editing while guarding invoice-allocation signs."""
        self.ensure_one()
        move_line = self.env['account.move.line'].browse(move_line_id).exists()
        if move_line and move_line in self.line_ids:
            self._check_reconcile_line_amount_sign(move_line, record_data)
        return super().edit_reconcile_line(move_line_id, record_data)

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_open_taj_currency_amount_wizard(self):
        """Open the modal wizard editing the company-currency equivalent."""
        self.ensure_one()
        self._check_currency_amount_edit_allowed()
        self._check_currency_amount_edit_state()
        info = self._get_currency_amount_edit_info()
        company_currency = self.company_id.currency_id
        self._check_currency_amount_edit_values(
            info['source_amount'], info['source_currency'],
            info['company_amount'], company_currency,
        )
        wizard = self.env['taj.bank.statement.currency.amount.wizard'].create({
            'statement_line_id': self.id,
            'source_amount': info['source_amount'],
            'source_currency_id': info['source_currency'].id,
            'company_amount': info['company_amount'],
            'company_currency_id': company_currency.id,
            'company_amount_field': info['company_amount_field'],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _("Edit Company Equivalent"),
            'res_model': 'taj.bank.statement.currency.amount.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
        }
