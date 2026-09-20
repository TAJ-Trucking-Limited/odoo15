# TAJ Account Reconciliation Enhancements Implementation Plan

> **For agentic workers:** Implement this plan test-first. Do not commit; the orchestrator will review and commit the verified result.

**Goal:** Add a small Odoo 19 module that exposes the native exact allocation editor and safely edits the company-currency equivalent of an unreconciled foreign-currency bank transaction.

**Architecture:** Reuse Odoo Enterprise's existing `edit_reconcile_line` flow for invoice allocations. Add one transient wizard backed only by standard `account.bank.statement.line` currency fields, plus an OWL patch and template extensions for the reconciliation UI.

**Tech Stack:** Odoo 19 Python ORM, XML views, OWL JavaScript/QWeb assets, Odoo `TransactionCase`-style tests.

**Spec:** `docs/superpowers/specs/2026-09-20-taj-account-reconciliation-design.md`

## Global Constraints

- Work only inside the new `taj_account_reconcile` module and its tests, except for the two design/plan documents already present.
- Depend on `account_accountant`; do not copy or replace the Enterprise reconciliation engine.
- Do not create, write, or delete `res.currency.rate` records.
- Do not allow equivalent edits after counterpart lines have been added, after full reconciliation, or after review.
- Use standard Odoo fields and synchronization logic.
- Do not commit or push.

## Task 1: Scaffold the module and failing behavioral tests

Create:

- `taj_account_reconcile/__init__.py`
- `taj_account_reconcile/__manifest__.py`
- `taj_account_reconcile/models/__init__.py`
- `taj_account_reconcile/models/account_bank_statement_line.py`
- `taj_account_reconcile/wizard/__init__.py`
- `taj_account_reconcile/wizard/bank_statement_currency_amount.py`
- `taj_account_reconcile/security/ir.model.access.csv`
- `taj_account_reconcile/views/bank_statement_currency_amount_views.xml`
- `taj_account_reconcile/views/bank_reconciliation_views.xml`
- `taj_account_reconcile/static/src/js/bank_reconciliation.js`
- `taj_account_reconcile/static/src/xml/bank_reconciliation.xml`
- `taj_account_reconcile/tests/__init__.py`
- `taj_account_reconcile/tests/test_bank_statement_currency_amount.py`
- `taj_account_reconcile/tests/test_reconciliation_views.py`

Start with tests for the design contract before implementing the models and UI.

## Task 2: Implement the guarded currency-equivalent wizard

Extend `account.bank.statement.line` with a record action that:

- verifies accounting-user membership and record write access;
- verifies the transaction is not checked, reconciled, or already carrying counterpart lines;
- resolves the source amount/currency and company equivalent for the two supported currency directions;
- creates and opens a transient wizard in a modal.

Implement the transient wizard with readonly source fields and an editable company amount. On apply, re-read and revalidate the statement line, enforce non-zero/same-sign amounts with currency precision, write only the appropriate standard fields, and post an audit note on the journal entry.

## Task 3: Expose both workflows in the reconciliation UI

Patch `BankRecStatementLine` to call the server action and reload the record when the modal closes. Show **Edit Currency Amount** only when:

- the user is an accountant;
- journal/transaction currency differs from company currency;
- the transaction is not checked or reconciled;
- no matched/counterpart reconciliation lines exist.

Extend `account_accountant.BankRecLineToReconcile` so its existing `toggleEditLine` button visibly says **Set Amount** and has an explanatory tooltip. Extend `account_accountant.view_bank_rec_edit_line` with short help text; do not replace its save mechanism.

Register JavaScript/XML in `web.assets_backend`.

## Task 4: Verify

Run and fix all failures from:

```bash
python3 -m compileall -q -x '/\.git/' .
python3 - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

for path in Path('.').rglob('*.xml'):
    ElementTree.parse(path)
print('XML parse: PASS')
PY
```

If an Odoo 19 executable and database are available, also run the module's tagged server tests. If they are unavailable, report that constraint without claiming the integration tests passed.

Review `git diff --check`, `git status --short`, and the complete diff. Confirm only the intended module and the plan/spec documents changed.
