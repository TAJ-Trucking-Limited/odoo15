# TAJ Transport Operations — Phase 2, Thursday/Friday release gate

**Scope:** Branch 19_upgrade, TAJ Transport Operations version 19.0.1.6.2,
and existing move_invoice_line / Fleet Navirec modules.

**Safety:** Development/staging only. Upgrade modules manually from Odoo Apps;
never install/update them automatically on Git push. Do not touch Production.
Do not send actual reports or customer email from a test environment. Run
destructive or test-transaction CLI commands only against an **isolated clone**
of the Odoo.sh development/staging database, never the shared staging database.

## What can be checked offline

From the project root on the developer's computer:

    python3 scripts/taj_transport_phase2_static_gate.py

This checks the committed module's Python/XML structure, manifest, ACL model
references, view fields and action methods. **It does not run Odoo.**

## Environment confirmation before runtime checks

- [ ] The current Odoo.sh development/staging build for 19_upgrade is green
      and shows the same commit SHA as GitHub. Do not assume a new GitHub
      commit automatically means the Odoo.sh build is current.
- [ ] A restorable staging database backup exists.
- [ ] In Odoo Apps manually upgrade TAJ Navirec Fleet Integration to
      19.0.1.3.2 if that upgrade has not happened yet; then manually upgrade
      TAJ Transport Operations to 19.0.1.6.2.
- [ ] Confirm assigned Fleet trucks and trailers have the intended Truck or
      Trailer category. Existing original Sales trucks with blank Category
      must be reviewed by a Fleet administrator; do not bulk guess categories.
- [ ] Do not change existing Sales, VAT, invoice or accounting data to make
      a transport QA test pass. Use a new test Sales Order where possible.

## Read-only staging database integrity report

Once the manual upgrade is complete, open an Odoo.sh development/staging shell
and run:

    odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
        < ~/src/user/scripts/taj_transport_phase2_staging_gate.py

The script only reads; it reports Fleet categorization warnings, ambiguous
history, impossible statuses, company mismatches and unreleased resources.
Errors require investigation, but historical invoice-to-Sales drift is only a
warning because Sales could have been edited independently.

## Thursday — authorization and financial boundary UAT

- [ ] Dispatcher may create a Trip from a confirmed Sales Order and assign
      an existing Truck, driver and optional trailer/co-driver.
- [ ] Dispatcher cannot edit Fleet vehicles or assignment history directly.
- [ ] Transport Manager inherits the Dispatcher ability, plus may cancel
      an In Progress trip. Dispatcher cannot cancel In Progress.
- [ ] Neither role can see or mutate another company's transport records.
- [ ] Sales Order, linked sales line, company and original vehicle remain the
      source of truth across resource replacement and lifecycle transitions.
- [ ] The Trip Commercial tab's order reference, price, currency, payment
      terms and quantities match Sales and cannot be edited from Trip.
- [ ] An existing customer invoice keeps its original Truck, Route/Sales
      line, container, quantity, price, taxes, VAT percentage and payment term
      after an operational truck/driver replacement.
- [ ] Creating an invoice *after* Trip completion still uses the original
      Sales line's Truck and metadata, not the last operational Truck.
- [ ] Cancelling a Trip does not cancel its Sales Order or invoice, refund
      anything, or create an accounting journal entry.

## Friday — end-to-end operational scenarios

- [ ] Confirm a new Sales Order with a non-unit transport service quantity,
      original Truck, VAT and payment term; create a Trip for the specific
      sale line. Duplicate Trip creation must reuse or refuse, never duplicate.
- [ ] Record initial resources, actual route, origin, destination and planned
      times. Verify immutable history and a single active assignment.
- [ ] Mark Ready, Start Trip, replace Truck/driver and Complete Trip.
      Only legal state transitions succeed, current resources disappear on
      completion, and Last Assigned Resources and history remain visible.
- [ ] Attempt replacement using a stale second wizard: refuse without
      changing the active assignment or creating duplicate history.
- [ ] Reject wrong Fleet category, archived Truck, invalid company, duplicate
      driver/co-driver, empty replacement, overlapping assignment periods,
      future effective replacement and invalid actual departure/arrival.
- [ ] Cancel a Draft or Ready Trip with reason; cancel In Progress with manager;
      attempts to reopen a terminal trip or edit it must fail.
- [ ] Check Fleet Navirec tracking eligibility: normal unmapped trailers are
      excluded, explicitly device-equipped trailers can be matched by unique
      plate and selected in reports. No report email should be sent.
- [ ] Inspect the Sales Order, original truck snapshot, customer invoices and
      payment terms after all scenarios. No unexpected Accounting changes.

## Isolated-clone full test suite (not the shared staging database)

After preparing an **isolated copy** of a development/staging database and
setting PGDATABASE to that *clone*, run Odoo 19 tests on that copy:

    odoo-bin -d "$PGDATABASE" --no-http --max-cron-threads=0 \
        --stop-after-init -u taj_transport_operations \
        --test-enable --test-tags /taj_transport_operations

The -u option in this test-only command **upgrades the clone**. It must not be
used on the shared staging database because installation/upgrades there
remain manual. New tests include real draft customer invoice generation,
the original Sales truck after a completed trip, VAT, payment terms,
multi-line invoicing, cancellations and multi-company visibility. These
tests have **not** been run in this local checkout; no Odoo runtime exists
on this Mac.

## Release outcome (fill after runtime evidence)

- Offline Python/XML/ACL preflight: **PASS locally** (21 Python files,
  10 XML files, 112 view fields, 12 action buttons, 11 ACL rows).
- Defined Odoo TransactionCase tests: **85** (defined, not executed).
- Read-only staging gate: **offline synthetic checks PASS** (empty/valid
  Draft scenarios accepted; an invalid Ready trip correctly rejected).
  Real Odoo ORM/database execution is still pending.
- Odoo.sh build/actual deployed SHA: **not verified**; the previously
  used development URL was unreachable from the connected Mac.
- Manual module upgrade on staging: **not performed by this change**.
- Read-only staging database gate on Odoo.sh: **not executed**.
- Full ORM integration tests on isolated clone: **not executed**.
- Manual UAT (two roles, Sales/Invoices, Fleet): **not executed**.

No Production release is authorized by this checklist. Review the results,
resolve critical issues, and confirm the staging gate before deciding on
a subsequent merge/promotion.
