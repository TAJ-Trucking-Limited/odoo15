# Odoo 19 Custom Addons Implementation Plan

**Repository:** `TAJ-Trucking-Limited/odoo15`
**Working branch:** `19_upgrade`
**Baseline commit:** `2daef86`
**Target:** Odoo 19 Enterprise on Odoo.sh
**Production branch:** `production` (Odoo 17; do not modify)
**Plan date:** 2026-07-22

This document is the code implementation plan. `MIGRATION_PLAN.md` remains the broader upgrade, UAT, production, and rollback plan.

---

## 1. Rules

1. All code changes stay on `19_upgrade` until the Odoo 19 development build and staging UAT pass.
2. Do not merge, fork, or push to `production`.
3. Do not push while the current Odoo.sh rebuild is running. Local edits are safe while it runs.
4. Keep commits small and scoped. Do not combine unrelated module changes.
5. Run local static gates before every push.
6. Push in waves so Odoo.sh failures can be attributed to a small set of commits.
7. Do not guess production data mappings, XML IDs, account codes, or recipients.
8. Do not add a root `requirements.txt` for `XlsxWriter`, `xlrd`, `openpyxl`, or `num2words`; Odoo 19 already installs them.

---

## 2. Current Baseline

Already committed on `19_upgrade`:

- Manifests use `19.0.x` versions and all addons are installable.
- XML list views use `<list>` rather than `<tree>`.
- Removed Odoo 19-invalid `ir.cron` fields.
- Updated several view XPaths and menu references.
- Applied first-pass `report_xlsx` and report fixes.

Implementation completed locally on `19_upgrade` (not committed or pushed):

- `report_xlsx` is byte-identical to OCA commit `6692523980cbc57d414935311d7f7bf1c834edc6`.
- Repository bytecode and IDE metadata are removed and ignored.
- Cheque creation is batch-safe and protected by a database unique constraint.
- SO-to-invoice and PO-to-bill cargo propagation use Odoo 19 preparation hooks.
- Invoice and purchase QWeb inheritance targets the verified Odoo 19 templates.
- Aged-report IDs and recipients are configuration-backed; report calculations are consolidated.
- Automated regression tests were added to the three custom modules.

Remaining gates are the Odoo.sh Odoo 19 runtime build, Enterprise report export, upgraded-Studio view validation, PDF/XLSX rendering, and staging UAT.

---

## 3. Build and Push Strategy

### Wave 0: Baseline

Wait for the currently running Odoo.sh rebuild.

Capture:

- Build result
- Failing module names
- First complete traceback from `install.log`
- Commit tested by the build

Do not rebuild repeatedly. A rebuild does not change the code.

### Wave 1: Production-Independent Work

Push after Batches 1, 2, and 3A are complete and local gates pass.

Planned local commits:

1. `[MIG] Sync report_xlsx with OCA 19 and clean repository`
2. `[FIX] Make cheque creation batch-safe on Odoo 19`
3. `[FIX] Complete sale invoice custom field propagation`
4. `[MIG] Align invoice and purchase report templates with Odoo 19`

### Wave 2: Production-Discovery Work

Read-only production discovery is complete. The PO mapping, report XML IDs, approved managed recipients, account codes, cron state, Studio inventory, Enterprise modules, and security-parity decision are recorded below.

Planned local commits:

1. `[FIX] Map purchase lines to vendor bill custom fields`
2. `[MIG] Adapt emailed financial reports to Odoo 19`

### Wave 3: Integration Fixes

Only fixes discovered by Odoo.sh builds and staging UAT belong in this wave.

---

## 4. Batch 1 - Repository and `report_xlsx`

**Status:** Implemented locally; Odoo.sh runtime pending
**Estimate:** 3-5 hours

### Files

- `.gitignore` (new)
- Tracked `**/__pycache__/*.pyc` files (remove)
- Tracked `.idea/**` files (remove)
- `report_xlsx/**` (replace from upstream)
- `send_report_via_mail/__manifest__.py`

### Implementation

1. Add `.gitignore` entries for `__pycache__/`, `*.pyc`, `.DS_Store`, and `.idea/`.
2. Remove all tracked Python bytecode and `.idea` files.
3. Byte-diff the complete local `report_xlsx` directory against OCA `reporting-engine` branch `19.0`, commit `6692523980cbc57d414935311d7f7bf1c834edc6`.
4. Classify the controller, JavaScript, model, test, manifest, and translation deltas before replacing anything; do not assume the local copy has no patches.
5. Replace the complete local `report_xlsx` directory with the pinned OCA module.
6. Retain the upstream technical module name `report_xlsx` and version `19.0.1.0.2`.
7. Reapply only behavior proven to be an intentional TAJ requirement, with a focused test for each retained delta. Expected upstream drift is not a reason to retain old code.
8. Add `report_xlsx`, `mail`, and `move_invoice_line` to `send_report_via_mail` dependencies. The last dependency is required because `send_mail.py` reads custom sale and accounting fields defined by `move_invoice_line`.
9. Remove unused `payment`; direct dependencies are `account_reports`, `mail`, `move_invoice_line`, and `report_xlsx`.
10. Do not create a root `requirements.txt`; Odoo 19 provides the OCA module's declared Python packages.

### Tests

- Confirm the vendored `report_xlsx` matches the recorded upstream commit except for any explicitly documented and tested TAJ delta.
- Retain and run upstream `report_xlsx/tests` through Odoo.sh.
- Install `report_xlsx` and `move_invoice_line` before `send_report_via_mail` on the development database.
- Generate one basic XLSX report and verify its content type and filename.
- Confirm `send_report_via_mail` loads without `Model 'report.report_xlsx.abstract' does not exist`.

### Acceptance Gate

- No tracked `.pyc` or `.idea` files.
- No unreviewed delta from the pinned OCA module.
- `report_xlsx` and `send_report_via_mail` install successfully.
- XLSX download works in the Odoo 19 development build.

---

## 5. Batch 2 - `account_cheque_printing`

**Status:** Implemented locally; Odoo.sh runtime pending
**Estimate:** 3-5 hours

### Files to Change

- `account_cheque_printing/models/account_cheque.py`
- `account_cheque_printing/wizard/cheque_print_wizard.py`
- `account_cheque_printing/tests/__init__.py` (new)
- `account_cheque_printing/tests/test_account_cheque.py` (new)

### Files to Validate Without Rewriting

- `account_cheque_printing/controllers/cheque_preview_controller.py`
- `account_cheque_printing/static/src/js/print_cheque_action.js`
- `account_cheque_printing/views/*.xml`
- `account_cheque_printing/reports/*.xml`
- `account_cheque_printing/security/ir.model.access.csv`

### Implementation

1. Keep the existing `@api.model_create_multi` decorator and change its argument from `vals` to `vals_list`.
2. Call `cheques = super().create(vals_list)`.
3. Iterate over `cheques` when assigning `CH/<cheque_number>`.
4. Preserve the existing global cheque-number uniqueness behavior.
5. Remove the unused direct `num2words` import from the wizard; continue using `currency.amount_to_text()`.
6. Do not change `request.not_found("Cheque wizard not found!")`; Odoo 19 accepts a description argument.
7. Do not convert the function-based client action to an OWL component; Odoo 19 explicitly supports function client actions.
8. Keep the existing client action and validate it functionally.
9. Avoid unrelated cheque workflow or report-layout redesign in this batch.

### Security Decision

1. Production ACLs exactly match the source: global read/write/create and no unlink for cheque and wizard, with no custom record rules.
2. The customer chose migration parity rather than an access-policy change.
3. Preserve these ACL rows unchanged. Menu visibility remains inherited from Accounting, and manual cheque access remains under `account.group_account_user`.
4. Revisit tightening only as a separate, approved post-migration security change.

### Automated Tests

Add tests for:

- Creating one cheque and generating its internal reference.
- Creating two cheques in one ORM `create()` call.
- Rejecting duplicate cheque numbers.
- Computing the payment/payee comparison note.
- Creating a manual cheque through the wizard.
- Creating and reprinting a payment-linked cheque.
- Incrementing cheque and payment print counters.
- Rejecting changed cheque data when the cheque number is unchanged.
- ACL behavior matching production parity.

### Odoo.sh Functional Tests

- Open the wizard from a payment.
- Create a manual cheque.
- Preview the cheque.
- Print the cheque and download the voucher.
- Reprint and verify counters.
- Confirm the invalid preview URL returns a normal 404.

### Acceptance Gate

- Module installs and upgrades without errors.
- Batch creation test passes.
- Cheque, voucher, and preview flows work in Odoo 19.
- Cheque ACL and menu behavior match the approved production parity decision.
- No OWL rewrite is introduced unless runtime evidence requires it.

---

## 6. Batch 3A - `move_invoice_line`: Sale and Static Report Work

**Status:** Implemented locally; Odoo.sh PDF/runtime validation pending
**Estimate:** 5-8 hours

### Files

- `move_invoice_line/models/sale_order_line.py`
- `move_invoice_line/__manifest__.py`
- `move_invoice_line/views/account_move_views.xml`
- `move_invoice_line/views/sale_order_views.xml`
- `move_invoice_line/views/invoice_views.xml`
- `move_invoice_line/views/layout_bold.xml`
- `move_invoice_line/report/inherit_purchase_template.xml`
- `move_invoice_line/tests/__init__.py` (new)
- `move_invoice_line/tests/test_invoice_line_propagation.py` (new)

### SO-to-Invoice Implementation

1. Keep the confirmed Odoo 19 hook `_prepare_invoice_line(**optional_values)`.
2. Call `super()` first.
3. Update custom fields only when the prepared line has `display_type == "product"`.
4. Copy `vehicle_id`, `container_num`, `file_name`, `consignee`, `weight`, `size`, and `srn`.
5. Set `order_id` to the source sale order.
6. Set `route_id` to the source sale order line.
7. Do not manually duplicate `analytic_distribution`; Odoo 19 handles it through the standard sale invoicing flow.
8. Preserve all standard values returned by `super()`.

### Invoice PDF Implementation

1. Validate every XPath against Odoo 19 `account.report_invoice_document`.
2. Insert the TSH row after the main `//div[@id='total']//t[@t-call='account.document_tax_totals']`; inserting inside the `t-call` body would not render.
3. Keep the exchange-rate division guard.
4. Ensure the Truck, Container Number, and Consignee columns target accountable product rows.
5. Replace any remaining Bootstrap 4 alignment classes with Bootstrap 5 equivalents.
6. Preserve all three Odoo 19 invoice-address branches and the verified bank-details anchor.
7. `force_save="1"` remains supported by the Odoo 19 web client; leave it unless testing proves the two analytic fields no longer require it.
8. Do not redesign the invoice layout beyond changes required for compatibility.

### Purchase PDF Static Cleanup

1. Merge the two inheritance records targeting `purchase.report_purchaseorder_document` into one coherent inherited view.
2. Replace `text-right` with `text-end`.
3. Replace `font-weight-bold` with `fw-bold`.
4. Replace `font-italic` with `fst-italic`.
5. Replace `mr16` with a Bootstrap 5 spacing class.
6. Remove large obsolete commented XML blocks when they obscure the active template.
7. Remove `order_line[0]`; render Container from the resolved `source_sale_line_id` safely.
8. Keep section/note rows safe through the standard Odoo `colspan="99"` behavior.

### Manifest and Dead-View Cleanup

1. Remove the empty legacy `'qweb': []` manifest key.
2. Check production for XML ID `move_invoice_line.taj_external_layout_bold`.
3. If the XML ID is absent, delete `layout_bold.xml` as dead code.
4. If it is active, do not simply add the current file to the manifest: it replaces the entire standard footer. Document the required visual change and reimplement only that specific change against the Odoo 19 layout.

### Automated Tests

Add tests for:

- A sale product line copying all custom values to its invoice line.
- `order_id` and `route_id` pointing to the correct source records.
- Section, note, and combo-heading invoice lines not receiving custom accounting values.
- Multiple sale lines retaining different custom values.

### Acceptance Gate

- Sale-to-invoice tests pass.
- Invoice and PO QWeb views load without XPath errors.
- The invoice total row targets only the main Odoo 19 totals block.
- Invoice PDF renders on Odoo 19.
- PO PDF renders, even before the Batch 3B data-mapping enhancement.
- Empty manifest metadata and the destructive dead-layout ambiguity are resolved.

---

## 7. Batch 3B - `move_invoice_line`: PO-to-Bill Mapping

**Status:** Implemented locally from completed production discovery; Odoo.sh runtime pending
**Estimate:** 4-8 hours

### Verified Production Facts

- `purchase.order.line.sale_line_id` exists but is populated on 0 of 234 production PO lines.
- The custom PO-header `sale_order_id` is populated on 129 POs.
- Every linked production PO has exactly one non-display SO product line; PO/SO product-line counts are `((0,1),1), ((1,1),95), ((2,1),32), ((3,1),1)`.
- No historical backfill is required. New code prefers `sale_line_id`, falls back to the sole header-linked SO product line, leaves unlinked lines blank, and blocks ambiguous multi-line fallback with a clear error.

### Files

- `move_invoice_line/__manifest__.py`
- `move_invoice_line/models/inherit_purchase_order.py`
- `move_invoice_line/report/inherit_purchase_template.xml`
- `move_invoice_line/tests/test_purchase_invoice_line.py` (new)
- Optional migration scripts only if existing records need backfilling

### Implementation

1. Add `sale_purchase` as an explicit dependency.
2. Override confirmed Odoo 19 hook `purchase.order.line._prepare_account_move_line(self, move=False)`.
3. Use standard `purchase.order.line.sale_line_id` as the source relation when populated.
4. Copy the same custom fields used by the SO-to-invoice flow.
5. Never map lines by list position, sequence, or product guesswork.
6. For manually linked POs, use the sole non-display header-linked SO line; block bill creation if multiple candidates exist without `sale_line_id`.
7. Remove the nested cartesian loop from `purchase.order.action_create_invoice()`.
8. Use computed `line.source_sale_line_id.container_num` in the PO report; it follows the same standard-link/fallback rule and renders blank safely.
9. Remove the unguarded `order_line[0]` access.
10. Guard missing analytic account names in `set_cargo_rout`.
11. Replace repeated ID searches with record browsing and handle combined analytic-distribution keys safely.
12. Remove debugging log spam after behavior is covered by tests.
13. Retain the custom PO-header `sale_order_id`; production relies on it.

### Automated Tests

Add tests for:

- Standard `sale_line_id` taking priority over the header fallback.
- Sole-header fallback, unlinked blank behavior, and ambiguous-header rejection.
- Payable/control lines not receiving route fields.
- Purchase report rendering with and without a linked sale line.
- Any migration/backfill being idempotent.

### Acceptance Gate

- No cartesian overwrite remains.
- Every bill line receives data from one explicit source line.
- Multi-line regression test passes.
- Existing/manual production workflow has a documented outcome.

---

## 8. Batch 4 - `send_report_via_mail`

**Status:** Implemented locally; Odoo 19 Enterprise export/runtime validation pending
**Estimate:** 8-14 hours

### Verified Production Facts

- Exact XML IDs are `account_reports.aged_payable_report` (DB ID 9) and `account_reports.aged_receivable_report` (DB ID 8).
- The active daily cron runs as OdooBot and sends two sane PDFs. Existing production schedule/output must remain unchanged.
- Approved managed defaults preserve sender `odoobot@taj-limited.odoo.com`, To `kreik.ali@gmail.com`, and the current three CC recipients.
- All 52 profitability account codes exist in TAJ Trucking Limited; lookups are scoped to the active company.
- Production ACLs exactly match source and the customer approved parity: both report models retain global full CRUD with no custom record rules. Profit-report menu visibility remains inherited from `sales_team.group_sale_manager`.
- The only unresolved fact is the Odoo 19 Enterprise runtime contract of `get_options()` / `dispatch_report_action()`.

### Files

- `send_report_via_mail/__manifest__.py`
- `send_report_via_mail/models/send_mail.py`
- `send_report_via_mail/data/report_mail.xml`
- `send_report_via_mail/models/report_profit_and_loss.py`
- `send_report_via_mail/reports/report_profit_and_loss_views.xml`
- `send_report_via_mail/tests/__init__.py` (new)
- `send_report_via_mail/tests/test_send_mail.py` (new)

### Manifest and Report Framework

1. Add direct dependencies on `report_xlsx`, `mail`, and `move_invoice_line`.
2. Depend directly on `account_reports`, `mail`, `move_invoice_line`, and `report_xlsx`; their transitive dependencies provide the base sale/account models.
3. Remove unused `payment`.
4. Confirm the XLSX abstract report works with the pinned OCA module.
5. Confirm the `ir.actions.report` record still downloads the P&L workbook.

### Aged-Report Email Rewrite

1. Replace raw database IDs `8` and `9` with exact `env.ref()` XML IDs.
2. Do not use translated report names as fallback identifiers.
3. Verify `get_options()` and `dispatch_report_action()` on Odoo 19 Enterprise.
4. If the API changed, implement the Odoo 19 export path observed in the staging runtime; do not guess from Odoo 17.
5. Remove `self.env.cr.commit()`.
6. Remove invalid/bogus `store_fname` values.
7. Use `fields.Command.set()` for attachments.
8. Move sender, recipient, and CC addresses to approved configuration.
9. Fail clearly when required recipient configuration is missing.
10. Keep attachments linked to the retained sent-mail history, matching production; reconsider automatic deletion only as a separate retention-policy decision.
11. Log report/export failures without partially committed attachments or mail records.
12. Do not send production-derived PDFs to the configured production recipients during staging tests.

### Profitability XLSX

1. Preserve current business formulas until the customer validates them.
2. Preserve the 52-code mapping, all of which were validated in the active production company.
3. Scope accounting searches to the active company.
4. Test one known trip against an existing trusted Odoo 17 result.
5. Consolidate the duplicated calculation pipeline while preserving the exact 73-key row contract, formulas, aliases, and existing last-line-wins persistence behavior.
6. Remove the dead hardcoded date assignments near the start of `send_mail.py`.
7. Remove the debugging `print()` call.
8. Keep `send_report()` and `view_report()` outputs stable and protect the mapping contract with tests.

### Cron

1. Keep only Odoo 19-supported `ir.cron` fields.
2. Confirm active state, interval, next execution time, and execution user.
3. The cron remains under `noupdate="1"`: existing production stays active with its current schedule, while fresh installations default inactive to prevent accidental external mail.
4. The mail template is outside `noupdate` and currently has no code reference. Either use it in the rewritten flow or delete it; do not leave a misleading unused template.
5. Run manually in staging with mail capture before enabling normal execution.
6. Confirm exactly one email is produced per run.

### Security

1. Production has the same empty-group full-CRUD ACL rows and no custom record rules.
2. Preserve that effective access for migration parity, as explicitly approved.
3. Treat any later tightening as a separate customer-approved security project.

### Automated Tests

Add tests for:

- Missing email configuration failing safely.
- Attachment commands and rollback behavior.
- XML-ID report lookup.
- XLSX report action registration.
- Profit workbook generation with controlled fixture data.
- ACL behavior matching the approved production parity decision.

Actual aged PDF export remains an Odoo.sh staging integration test because it depends on Enterprise `account_reports`.

### Acceptance Gate

- Daily cron runs once without manual commits.
- Correct two PDFs are attached.
- Staging email is intercepted by the approved destination/mail catcher.
- P&L workbook opens and matches a known test case.
- No raw report database IDs or hardcoded recipient addresses remain in Python.
- No dead dates, debugging prints, or unused mail-template record remain.
- Existing cron values match the approved configuration despite `noupdate="1"`.
- Financial report ACL and menu behavior match the approved production parity decision.

---

## 9. Batch 5 - Integration and Odoo.sh

**Status:** Local static validation in progress; Odoo.sh execution pending

### Local Gates Before Every Push

Run:

```bash
git diff --check
python3 -m compileall -q account_cheque_printing move_invoice_line report_xlsx send_report_via_mail
xmllint --noout $(git ls-files '*.xml')
```

Also verify:

- Only intended files changed.
- No `.pyc` files are tracked.
- No `.idea` files are tracked.
- Manifests parse and dependencies exist.
- No module is temporarily set `installable: False`.
- Every ACL row with an empty group is either fixed or explicitly approved.

### Odoo.sh Development Gate

For every push wave:

1. Wait for the Odoo 19 development build.
2. Read the first root-cause traceback, not only the module summary.
3. Fix root causes locally.
4. Push a new commit; do not use repeated rebuilds as a substitute for changes.
5. Require a green build before creating or upgrading staging.

The development gate passes when:

- Build is green.
- All four addons install.
- Custom and OCA tests pass.
- No `ERROR` or `CRITICAL` entry refers to these addons.

### Staging Gate

1. Request an Odoo 19 test upgrade from a production database copy.
2. Confirm all required Enterprise modules are installed.
3. Upgrade all four addons.
4. Complete the UAT matrix in `MIGRATION_PLAN.md`.
5. Resolve the Enterprise report API and production-data decisions.
6. Repeat until staging is green and customer-approved.

---

## 10. Immediate Work Order With Admin Access Confirmed

Start in this order:

1. Wait for and record the current rebuild result.
2. Verify the latest production backup and the restore procedure before using production Shell access.
3. Phase 0 read-only production discovery is complete; retain its findings as migration evidence.
4. Batches 1-4 are implemented locally and statically validated.
5. Review the complete diff and resolve review findings.
6. Commit the logical batches and push only when explicitly approved.
7. Require a green Odoo 19 development build before creating/promoting `19_staging`.
8. Run a fresh production-copy test upgrade, neutralize the cron/outgoing mail, validate Studio views, and complete UAT.
9. Use each Odoo.sh result to drive only evidence-based integration fixes.

---

## 11. Estimated Engineering Effort

| Batch | Estimate |
|---|---:|
| Baseline/log capture | 1-2 h |
| Batch 1: repository + OCA `report_xlsx` | 3-5 h |
| Batch 2: cheque module | 3-5 h |
| Batch 3A: sale/invoice + static reports | 5-8 h |
| Batch 3B: PO/bill mapping | 4-8 h |
| Batch 4: email/P&L reports | 8-14 h |
| Development build iterations | 6-12 h |
| Staging fixes and UAT support | 12-20 h |
| Production and hypercare | 6-10 h |

Typical total remains approximately **55-90 hours**, depending mainly on PO mapping and the Odoo 19 Enterprise financial-report API.

---

## 12. Definition of Code Complete

Code migration is complete when:

1. All four addons install on Odoo 19.
2. Odoo.sh development build is green.
3. Added custom tests and upstream OCA tests pass.
4. No production-dependent behavior is implemented from guesses.
5. Staging upgrade from the production database succeeds.
6. All UAT flows pass or are explicitly waived by the customer.
7. The reviewed code is ready to merge through staging, not directly to production.
8. Financial and cheque access matches the explicitly approved production-parity decision.
