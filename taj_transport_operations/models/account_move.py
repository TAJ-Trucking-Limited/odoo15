"""Keep native down-payment accounting working on protected sales orders."""
from odoo import models

from .sale_order import _CONFIRMED_SALE_ACCOUNTING


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_post(self):
        moves = self.with_context(_confirmed_sale_accounting=_CONFIRMED_SALE_ACCOUNTING)
        return super(AccountMove, moves).action_post()

    def button_draft(self):
        moves = self.with_context(_confirmed_sale_accounting=_CONFIRMED_SALE_ACCOUNTING)
        return super(AccountMove, moves).button_draft()

    def button_cancel(self):
        moves = self.with_context(_confirmed_sale_accounting=_CONFIRMED_SALE_ACCOUNTING)
        return super(AccountMove, moves).button_cancel()

    def unlink(self):
        moves = self.with_context(_confirmed_sale_accounting=_CONFIRMED_SALE_ACCOUNTING)
        return super(AccountMove, moves).unlink()
