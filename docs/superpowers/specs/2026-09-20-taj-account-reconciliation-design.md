# TAJ Account Reconciliation Enhancements Design

## Goal

Make two Odoo 19 reconciliation workflows clear and safe for accountants:

1. Let the accountant enter the exact amount applied to an invoice during bank reconciliation.
2. Let the accountant view and edit the company-currency equivalent of a foreign-currency bank transaction, such as a USD payment expressed in TZS.

## Scope

Create a new standalone module named `taj_account_reconcile` on the Odoo 19 upgrade branch. The module depends on `account_accountant` and does not modify the existing TAJ modules.

## Design Decisions

### Exact invoice allocation

Odoo 19 Enterprise already supports an exact partial allocation. Each matched reconciliation line has a pencil action that opens `account_accountant.view_bank_rec_edit_line`. That form allows the user to edit `balance` and, for a foreign-currency line, `amount_currency`. Saving calls the standard `account.bank.statement.line.edit_reconcile_line` method.

The module will keep this native mechanism and make it discoverable:

- Show a visible **Set Amount** label beside the existing edit control.
- Add a tooltip that explains that the user can enter the exact amount applied to the invoice.
- Add concise help text to the standard edit dialog.

The module will not add a second allocation engine or a custom allocation table.

### Editable USD/TZS equivalent

The module will add **Edit Currency Amount** beside the bank transaction amounts when the transaction contains two currencies and remains safe to edit.

The action opens a transient wizard that shows:

- the transaction or journal amount and its currency;
- the company-currency equivalent;
- the effective company-currency amount per one unit of the source currency.

The accountant edits only the company-currency equivalent. The source amount remains unchanged.

The wizard writes the standard `account.bank.statement.line` fields:

- For a foreign-currency journal, such as a USD bank journal in a TZS company, it sets `foreign_currency_id` to TZS and writes the TZS equivalent to `amount_currency`.
- For a company-currency journal whose transaction already has a foreign currency, it keeps the foreign amount and writes the company-currency equivalent to `amount`.

This preserves Odoo's transaction-specific rate behavior. It never creates or edits `res.currency.rate` records.

## Safety Rules

Only users in `account.group_account_user` can open or apply the wizard.

The wizard rejects an edit when:

- the transaction is already reviewed (`checked`);
- the transaction is fully reconciled;
- counterpart lines have already been added, including a partial match;
- the source or equivalent amount is zero;
- the source and equivalent amounts have different signs;
- the journal uses the company currency and no foreign transaction currency exists;
- a foreign-currency journal already carries a third currency that is not the company currency;
- the record is outside the user's allowed companies or is not writable.

If a transaction was already matched, the accountant must undo or remove its reconciliation lines, edit the equivalent, and then select the invoices again. This prevents Odoo's standard synchronization from deleting or rebuilding matched counterpart lines unexpectedly.

## User Interface

The bank reconciliation card will show:

- the standard two amount displays;
- **Edit Currency Amount** only for accountants, only for a two-currency transaction, and only before counterpart lines are added or the transaction is reviewed;
- **Set Amount** on each matched line, reusing the standard edit action.

The wizard uses a modal form with **Apply** and **Cancel** buttons. On close, the bank reconciliation record reloads so both displayed amounts reflect the saved value.

## Audit Behavior

When the company-currency equivalent changes, the module posts a short message on the related journal entry with the old and new equivalents and their currency. Standard access checks remain in force.

## Tests

Server tests will cover:

- the native partial-allocation view remains editable and the discoverability extension is present;
- a USD journal in a TZS company receives an editable TZS equivalent through standard fields;
- a TZS journal with a USD transaction keeps the USD amount while its TZS amount changes;
- zero and opposite-sign equivalents are rejected;
- reviewed, reconciled, and already-matched transactions are rejected;
- a third-currency case is rejected;
- no global currency-rate record is created or modified;
- the standard liquidity and suspense move lines stay balanced after a valid change.

Static verification will compile Python files and parse every XML file. Full Odoo integration tests require an Odoo 19 runtime and will be run on Odoo.sh before production deployment.
