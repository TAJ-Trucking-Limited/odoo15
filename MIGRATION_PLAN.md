# Odoo 17 → 19 Migration Plan

**Project:** Custom addons migration — Odoo 17 Enterprise (Odoo.sh) → Odoo 19
**Path:** Direct 17 → 19 (single step, via Odoo.sh upgrade platform)
**Branch:** `19_upgrade`
**Date:** 2026-07-22
**Status:** v10 — production discovery complete; migration code implemented locally; Odoo.sh runtime pending

---

## 1. Scope

4 custom addons, ~3,500 lines of custom code:

| Module | Type | Notes |
|---|---|---|
| `account_cheque_printing` | Custom (Rahaf Moualla) | Cheque management, print wizard, OWL action, custom PDF reports. Depends on EE `account_accountant` + CE `account_check_printing` (✅ confirmed present in 19.0) |
| `move_invoice_line` | Custom (Madfox) | "Taj Customizations" — invoice/sale/purchase line inheritance, heavy XPath overrides. Depends on EE `account_asset` |
| `report_xlsx` | Vendored OCA | Base XLSX report framework — byte-identical to OCA 19.0.1.0.2 commit `6692523980cbc57d414935311d7f7bf1c834edc6` |
| `send_report_via_mail` | Custom (Madfox) | Aged payable/receivable PDF email + hardcoded P&L XLSX (500xxx codes). Depends on EE `account_reports`. **Highest-risk module** |

---

## 2. Current State (verified 2026-07-20)

A first-pass migration is **already committed** on branch `19_upgrade` (2026-07-13).

### Done
- [x] All 4 manifests bumped to `19.0.x`, `installable: True`
- [x] `send_report_via_mail` duplicate `depends` key fixed
- [x] `<tree>` → `<list>` conversions done; no `attrs=` / `states=` left in any XML
- [x] Parent menu IDs fixed for Odoo 19; removed `ir.cron` `numbercall`/`doall`
- [x] `report_xlsx` `ir_report.py` + tests aligned with OCA 19
- [x] Invoice/sale/purchase XPaths updated (`invoice_line_ids`, list views)
- [x] Misc fixes: invoice rate guard, purchase invoice action return, invisible field justification

### Verified against Odoo 19.0 source (v5)
- [x] `purchase.order.line._prepare_account_move_line(self, move=False)` — **exists in 19.0, signature unchanged** → Task 1.4 de-risked
- [x] `sale.order.line._prepare_invoice_line(**optional_values)` — **exists in 19.0** (line 1510) → Task 1.4b de-risked
- [x] `request.not_found(self, description=None)` — **accepts a description in 19.0** → former `request.not_found` risk refuted, no fix needed
- [x] `serialize_exception` — **exists in 19.0** `odoo/http.py:469` → "import crashes on 19" claim wrong; other controller concerns (`url_decode`, `ReportController` override) still unverified, OCA replacement stays
- [x] `account.account` type `liability_payable` — exists in 19.0
- [x] `account_check_printing` — exists in 19.0 community
- [x] `sale_purchase` adds `purchase.order.line.sale_line_id` — confirmed in 19.0; this is the preferred PO-line → SO-line mapping where populated
- [x] Function-based client actions remain supported in 19.0 (`action_service.js` calls `clientAction(env, action, options)` for non-component actions)
- [x] Official Odoo 19 requirements already include `XlsxWriter`, `xlrd`, `openpyxl`, and `num2words` — a root `requirements.txt` is **not** required for these packages
- [x] `force_save="1"` remains supported by the Odoo 19 web client; it does not cause view validation failure
- [x] Odoo 19 `account.report_invoice_document` still contains `t-set="tax_totals"`; the custom XPath exists but should be scoped to the main totals block because the generic selector is ambiguous
- [x] The local `report_xlsx` has material controller, JavaScript, model, test, manifest, and translation deltas from pinned OCA commit `6692523980cbc57d414935311d7f7bf1c834edc6`

### Implemented Locally
- [x] Replaced `report_xlsx` exactly from the pinned OCA commit after byte-diff review
- [x] Corrected direct addon dependencies and removed unused `payment`
- [x] Reworked aged-report mail to exact XML IDs, managed parameters, proper attachments, and no manual commit
- [x] Replaced corrupt PO post-processing with line preparation plus the production-approved header fallback
- [x] Completed SO → invoice custom-field propagation
- [x] Removed 18 tracked bytecode files, tracked `.idea`, and dead `layout_bold.xml`; added `.gitignore`
- [x] Preserved broad ACLs after production verification and explicit customer approval for migration parity
- [x] Removed dead dates, debug output, duplicated calculations, and the unused mail template
- [ ] Run all modules/tests and render reports in a real Odoo 19 Enterprise Odoo.sh build
- [ ] Validate the upgraded Studio views and Enterprise aged-report export on staging

### Access
- [x] Odoo.sh development-branch access confirmed for `hayan-gh`
- [x] Odoo.sh Admin role confirmed for `hayan-gh`; production Shell, Logs, Backups, and Upgrade discovery can proceed
- [x] Repository source is GitHub-backed: `https://github.com/TAJ-Trucking-Limited/odoo15.git`

---

## 3. Recommended Sequencing

```
Phase 0 (production discovery) ─parallel─► Phase 1a (code that does not need staging)
                                                    │
                                                    ▼
                                              Phase 2 (Odoo.sh test upgrade → 19)
                                                    │
                                               Step 2.5: API check
                                                    │
                                                    ▼
                                              Phase 1b (report API rewrite only if needed)
                                                    │
                                                    ▼
                                              Phase 3 UAT → Phase 4 production
```

Do **not** treat Phase 1 as fully done before staging. Task 1.5b depends on Phase 2 step 5.

---

## 4. Phase 0 — Access & Inventory (pre-code discovery)

| # | Task | Owner |
|---|------|-------|
| 0.1 | Obtain Odoo.sh **Admin** role for `hayan-gh` | ✅ Complete |
| 0.2 | Export full installed-module inventory, including Enterprise and Studio | ✅ Complete |
| 0.3 | Inspect Studio fields/views and identify Odoo 19 parent/XPath risks | ✅ Complete — preserve fields; validate four views on upgraded staging |
| 0.4 | Verify all 52 profitability account codes in TAJ Trucking Limited | ✅ Complete — none missing |
| 0.5 | Identify exact aged report XML IDs | ✅ Complete — `account_reports.aged_payable_report` / `aged_receivable_report` |
| 0.6 | Query `move_invoice_line.taj_external_layout_bold` | ✅ Complete — absent; dead file deleted |
| 0.7 | Inspect cron, recent mails, and attachments | ✅ Complete — active daily OdooBot cron; recent runs sent two sane PDFs |
| 0.8 | Verify required Enterprise/community modules | ✅ Complete — all required modules installed |
| 0.9 | Determine PO/SO mapping from production data | ✅ Complete — standard link unused; approved sole header-linked SO-line fallback |
| 0.10 | Approve production recipients and staging safety | ✅ Production list preserved in managed parameters; staging catcher/cron neutralization required |
| 0.11 | Decide cheque/report ACL policy | ✅ Preserve current effective production access for migration parity |

**Remaining blocker rule:** do not run aged-report UAT until staging outgoing mail is captured and the cron is neutralized. Do not schedule production until the upgraded Studio views and all Enterprise modules are validated.

Read-only production discovery is complete. The latest automatic backups were verified, and no production records were written. Implementation remains local on `19_upgrade` pending review and Odoo.sh execution.

---

## 5. Phase 1a — Code Prep (no staging required) (~16–24 h)

Task numbers are retained from earlier plan versions for traceability; closed tasks are omitted.

### Housekeeping
- **1.1** Do **not** add an unpinned root `requirements.txt` for `XlsxWriter`, `xlrd`, `openpyxl`, or `num2words`; official Odoo 19 already pins them. Add a root requirement only if a genuinely new, non-core Python dependency appears.
- **1.2 ✅** Removed tracked `.pyc` and `.idea` files; added root `.gitignore` (`*.py[cod]`, `__pycache__/`, `.DS_Store`, `.idea/`).

### Mandatory replacements & refactors
- **1.3 ✅** Diffed and replaced `report_xlsx/` with exact OCA 19.0.1.0.2 commit `6692523980cbc57d414935311d7f7bf1c834edc6`; corrected direct dependencies.
- **1.4 ✅** Replaced the PO cartesian overwrite with `purchase.order.line._prepare_account_move_line(self, move=False)`. Standard `sale_line_id` wins; otherwise the sole header-linked product line is used; ambiguous fallback raises a clear error; unlinked lines stay blank. Composite analytics are parsed safely.
- **1.4b ✅** Completed SO → invoice field propagation and guard on the prepared Odoo 19 product-line value.
- **1.5a ✅** Safe cleanup of `send_email_with_pdf_attach()`:
  - Replace hardcoded report IDs `=9`/`=8` with the exact XML-ID lookups confirmed in 0.5; **no name-based fallback**
  - Remove `self.env.cr.commit()` (line 47)
  - Remove bogus `store_fname` values (lines 33, 41)
  - Fix `attachment_ids` write to `[(6, 0, [ids])]` / `Command.set` (line 57)
  - Remove dead hardcoded date assignments and the debugging `print()`
  - Move sender/recipient addresses out of Python into approved configuration (mail template, company fields, or system parameters). Configure staging to an approved test mailbox/mail catcher before UAT; never send production-derived financial PDFs to the current external addresses
  - Attach PDFs to retained mail history, matching current production retention; revisit auto-deletion only under an approved retention policy
  - Decide whether to use or delete the currently unreferenced mail template. It is outside `noupdate`; only the cron is protected by `noupdate="1"`
  - If cron values must change, update the existing record explicitly through a migration/administrative step because normal XML upgrade will not overwrite a `noupdate` record
  - Leave `dispatch_report_action` / `get_options` calls as-is until Phase 2.5
- **1.6 ✅** Made cheque creation batch-safe and added SQL uniqueness after confirming production has no blanks/duplicates.
- **1.7 ✅ decision** Preserve source ACLs because they exactly match production and the customer approved parity. Any tightening is post-migration scope.
- **1.8 ✅** Rebuilt PO report inheritance without whole-table replacement, duplicate templates, BS4 classes, or `order_line[0]`.
- **1.8b ✅** Insert the TSH row after the main Odoo 19 tax-total `t-call`, preserve all three address branches, keep valid `force_save`, and remove legacy `qweb` metadata.
- **1.9 ✅** Production XML ID was absent; deleted dead `layout_bold.xml`.

### Deferred to Phase 1b (after staging)
- **1.5b** API rewrite of aged-report export **only if** Phase 2.5 shows `dispatch_report_action` / `get_options` broken on 19 enterprise (budget 6–10 h if needed).
- **1.11** Legacy `analytic_account_id` fields — customer decision (keep or drop). Not a breaker.

---

## 6. Phase 2 — Odoo.sh Test Upgrade to 19 (~8–16 h)

1. Confirm `19_upgrade` is the branch Odoo.sh builds for the test upgrade (document merge/retarget path to production branch for Phase 4).
2. Push Phase 1a code; trigger **test upgrade → 19.0** from the Upgrade tab.
3. Fix build errors (external-dependency checks, view validation, removed fields).
4. Fix upgrade-time errors from Odoo upgrade scripts on custom data.
5. **Gate check:** does `account.report.dispatch_report_action` exist and work in 19 **enterprise**? (Enterprise source is not public — this is the one API that genuinely requires staging.)
   - **Yes** → skip or minimize 1.5b; go to Phase 3.
   - **No** → Phase 1b (rewrite export path) before full UAT.
6. Iterate until staging builds clean with all modules installed.

Reference: https://www.odoo.com/documentation/19.0/administration/upgrade.html

---

## 7. Phase 1b — Post-staging code (only if needed) (~0–10 h)

- **1.5b** Reimplement aged PDF export via the Odoo 19 `account_reports` API if 2.5 failed.
- **1.11** Apply customer decision on `analytic_account_id` if any.

---

## 8. Phase 3 — UAT on Staging (~12–20 h incl. fixes)

| # | Flow | Risk covered |
|---|------|---|
| 1 | Sales → invoice → **invoice PDF** | Fragile XPaths (`invoice_views.xml`) |
| 2 | Purchase → **PO PDF** → vendor bill | Template dedup + BS5 (1.8) |
| 3 | PO → vendor bill: standard `sale_line_id` priority, sole-header fallback, unlinked blank behavior, and explicit rejection of ambiguous fallback | **R2** (PO mapping + propagation) |
| 4 | SO with custom fields → customer invoice: accountable AML has container/srn/file/weight/size/route; section/note lines remain clean | **R12** (SO field copy) |
| 5 | Cheque: create → print cheque + voucher + preview (incl. invalid preview URL) | Controller + function-based client action (confirmed supported in 19.0) |
| 6 | Aged payable/receivable email: run cron only with the approved staging mailbox/mail catcher; verify PDFs, recipients, send result, and attachment cleanup | **R1 / R14** (report API + data leakage) |
| 7 | Trip profit XLSX — figures match production CoA | R6 / 0.4 |
| 8 | XLSX framework end-to-end | OCA `report_xlsx` + declared dep |
| 9 | Create 2 cheques in one batch (import/RPC) | R7 / 1.6 |
| 10 | Translations: custom report strings render correctly | i18n (`.pot` only if non-English needed) |
| 11 | Confirm dead bold-layout view remains absent after upgrade | R9 / 1.9 |
| 12 | Verify cheque/report ACL and inherited menu visibility match approved production parity | R16 / 1.7 |

---

## 9. Phase 4 — Production Upgrade + Hypercare (~6–10 h)

1. **Branch strategy:** merge or retarget so the production Odoo.sh branch runs the verified 19-compatible code from `19_upgrade` (document exact steps with customer before the window).
2. **Freeze window:** schedule maintenance (min. 2–4 h expected; confirm after staging timing); notify users; name one person with abort authority.
3. **Backup:** confirm Odoo.sh automatic pre-upgrade backup exists; note restore procedure **before** starting.
4. **Rollback:** if production upgrade fails → restore pre-upgrade backup; keep `19_upgrade` for fixes.
5. Request production upgrade → 19 via Odoo.sh; monitor.
6. **Post-upgrade data validation:** custom fields on recent invoices/bills? `analytic_distribution` intact? cron active (aged-report mail)? cheque sequences intact? EE apps still installed?
7. Smoke-test Phase 3 flows on production.
8. 1 week hypercare for report/layout tweaks.

---

## 10. Effort & Calendar

| Phase | Hours |
|---|---|
| Phase 0 — customer inventory + mapping/recipient decisions (elapsed, not all billable eng) | 2–6 eng + customer lag |
| Phase 1a — code prep | 16–24 |
| Phase 2 — test upgrade cycles | 8–16 |
| Phase 1b — post-staging report API rewrite (0 if API OK) | 0–10 |
| Phase 3 — UAT + fixes | 12–20 |
| Phase 4 — production + hypercare | 6–10 |
| PM / communication buffer | 6–10 |
| **Total remaining** | **50–96 h** (call it **55–90** typical) |

**Calendar (eng time only):**
- ~30 h/week → **2–4 weeks**
- ~20 h/week → **3–5 weeks**

Add customer lag for Phase 0 answers and UAT sign-off (often +1 week wall-clock).

> The PO mapping is resolved. The largest remaining technical uncertainty is the Odoo 19 Enterprise aged-report export API and upgraded Studio-view compatibility.

---

## 11. Definition of Done

1. Staging test upgrade to 19 succeeds with all 4 addons installed and no build errors.
2. All 12 UAT flows green on staging (or explicitly waived in writing by the customer).
3. Production upgrade to 19 completed; post-upgrade data validation checklist passed.
4. Phase 3 smoke tests green on production.
5. One week of hypercare with no open P1 (data loss, failed cron, unprintable cheque/invoice/PO).

---

## 12. Risk Register

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | Odoo 19 Enterprise aged-report export contract remains runtime-unverified | **Critical** | Cleanup complete; Phase 2.5 + UAT #6 |
| R2 | PO → vendor bill mapping/corruption (**pre-existing**) | High | Resolved mapping + line-level hook; UAT #3 |
| R3 | Local `report_xlsx` drift | Medium | Exact pinned OCA replacement complete; runtime XLSX smoke test |
| R4 | Missing report dependencies | High | Direct dependencies corrected |
| R5 | Invoice/PO/cheque PDF XPaths vs 19 templates (ambiguous tax-total selector, duplicate template, `order_line[0]`, BS4 classes) | High | 1.8/1.8b + UAT #1–2, #5 |
| R6 | Profitability 500xxx account mapping | Medium | All 52 codes validated; active-company scope + UAT #7 |
| R7 | `@api.model_create_multi` singleton crash on batch cheque create | Medium | 1.6 + UAT #9 |
| R8 | Hidden production customizations not in this repo | High | 0.2 |
| R9 | Dead destructive `layout_bold.xml` | Medium | Production XML ID absent; file deleted |
| R10 | Legacy `analytic_account_id` disconnected from analytic engine | Low | 1.11 customer decision |
| R11 | Aged-report cron continuity across upgrade | Medium | Production is healthy; verify active state, schedule, sender, and two PDFs after upgrade |
| R12 | SO → invoice incomplete custom field copy (**pre-existing**) | High | 1.4b (hook confirmed) + UAT #4 |
| R13 | Target loses Enterprise apps (`account_reports`, `account_accountant`, `account_asset`) | High | 0.8 |
| R14 | Staging sends production-derived financial reports externally | High | Managed parameters, fresh-install cron inactive, mandatory staging catcher/neutralization + UAT #6 |
| R15 | Existing cron values silently ignore changed XML because the record is under `noupdate="1"` | Medium | Inspect in 0.7; explicit update in 1.5a; verify in UAT #6 |
| R16 | Broad empty-group ACLs | Accepted | Verified as current production behavior; customer approved migration parity; UAT #12 |

---

## 13. Decisions

| # | Decision | Status |
|---|----------|--------|
| 1 | `report_xlsx` strategy | ✅ Replace with clean OCA 19.0.1.0.2 and record the upstream commit |
| 2 | Cheque module | ✅ Migrate current |
| 3 | Migration path | ✅ Direct 17 → 19 via Odoo.sh |
| 4 | Odoo.sh access | ✅ Admin role confirmed; read-only production discovery can proceed |
| 5 | Legacy `analytic_account_id` fields | ✅ Preserve for migration; reconsider only after staging/business validation |
| 6 | `layout_bold.xml` add or delete | ✅ Delete; production XML ID absent |
| 7 | Stay on Odoo 19 Enterprise with required apps | ✅ Required apps verified installed |
| 8 | PO-line → SO-line mapping for manually linked orders | ✅ Standard link, then sole header fallback; reject ambiguity |
| 9 | Production aged-report recipients | ✅ Preserve current list in managed parameters; staging must intercept |
| 10 | Cheque and financial-report security policy | ✅ Preserve current effective production access for migration parity |

---

## 14. Optional cleanups (not blocking)

- Review sent-mail/PDF retention after migration; current implementation preserves production history.
- Generate `.pot` / update `.po` only if customer needs non-English UI/reports.
- Tighten broad custom ACLs only as a separately approved post-migration change.
- Correct the legacy per-order/last-line-wins profitability model only after business validation.

---

## 15. Review Provenance

- **v2** — Gemini 3.1 Pro (High) via Antigravity; state claims confirmed; several new risks; some overstatements rejected after verification.
- **v3** — Claude Opus 4.6 (Thinking) via Antigravity; accepted missing dep, `cr.commit()`, m2m syntax, `layout_bold.xml`, BS4 classes; rejected false claims that APIs were already removed in 17.
- **v4** — Orchestrator plan review: fixed 1.5 vs Phase 2 ordering; added SO→invoice gap (now R12); added Enterprise dep confirmation (now R13); added calendar, DoD, branch strategy.
- **v5** — Verified API assumptions against **public Odoo 19.0 source** (GitHub): `_prepare_account_move_line` ✅ exists (signature unchanged), `_prepare_invoice_line` ✅ exists, `liability_payable` ✅ exists, `account_check_printing` ✅ exists, OCA 19.0 `report_xlsx` ✅ exists. **Refuted:** the former `request.not_found` risk (`not_found` accepts description in 19.0); the old `serialize_exception` import-crash claim was also wrong. Remaining unverifiable-without-staging API: enterprise `dispatch_report_action` only.
- **v6** — Execution review: refuted the root-requirements blocker and function-client-action risk using Odoo 19 source; identified missing PO-line mapping, standard `sale_purchase.sale_line_id`, and staging financial-data leakage; made recipient configuration mandatory; required exact report XML IDs and a pinned OCA source commit.
- **v7** — Full Odoo.sh access confirmed. Phase 0 module, XML-ID, cron, CoA, Enterprise-app, and PO/SO mapping checks moved from customer questions to direct read-only production-shell verification.
- **v8** — Corrected access status after UI verification: `hayan-gh` has development access but not the Admin role required for production Shell, Backups, Logs, and Upgrade. Production-independent code work can continue on `19_upgrade`.
- **v9** — Admin access subsequently confirmed. Verified external review findings against the repository and Odoo 19 source: accepted missing cross-module dependencies, hardcoded/dead report code, cron `noupdate`, destructive/dead artifacts, ambiguous tax-total targeting, and broad ACL risks; rejected claims that local `report_xlsx` had no meaningful delta or that `force_save` is invalid in Odoo 19.
- **v10** — Completed read-only production discovery and local implementation. Recorded approved PO fallback, managed recipients, all-valid account codes, ACL parity, Studio preserve/validate decision, dead-layout deletion, pinned OCA replacement, and remaining Odoo.sh runtime gates.

---

## 16. References

- Odoo.sh upgrade docs: https://www.odoo.com/documentation/19.0/administration/upgrade.html
- Upgrade platform: https://upgrade.odoo.com/
- Custom module upgrade howto: https://www.odoo.com/documentation/19.0/developer/howtos/upgrade_custom_db.html
- OCA `report_xlsx` 19.0: https://github.com/OCA/reporting-engine (branch 19.0)
- Odoo 19.0 community source: https://github.com/odoo/odoo/tree/19.0
- Odoo 19.0 official Python requirements: https://github.com/odoo/odoo/blob/19.0/requirements.txt
- Odoo 19.0 `sale_purchase` line mapping: https://github.com/odoo/odoo/blob/19.0/addons/sale_purchase/models/purchase_order.py
