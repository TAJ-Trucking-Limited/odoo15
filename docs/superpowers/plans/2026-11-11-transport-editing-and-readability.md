# Transport Editing and Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve shipment entry, confirmed-order analytical account editing, Fleet truck/trailer pairing, file numbering, and transport list readability.

**Architecture:** Extend the existing Odoo models and views in place. Keep confirmed-order exceptions limited to the requested fields, reuse the existing shipment audit workflow, and store the truck/trailer relationship once so its reverse display cannot drift.

**Tech Stack:** Odoo 19 ORM, PostgreSQL, XML views, existing SCSS assets, Odoo TransactionCase tests.

**Spec:** The requirements and acceptance criteria embedded below, based on the user's November 11 request and clarifications in this conversation.

## Global Constraints

- This delivery is a plan only. The user explicitly requested that this agent not write the implementation. Do not execute this plan without a separate implementation instruction.
- The module is Odoo 19 (`taj_transport_operations` version `19.0.1.43.0`) despite the checkout directory being named `odoo15`.
- Allow decimal weights, as confirmed by the user.
- One default trailer per truck and one assigned truck per trailer, as confirmed by the user.
- Interpret “ETO” as the existing `eta` field. No `eto` field was found. Keep the ETA label unless the user requests a different name.
- Keep existing order locks, record rules, company boundaries, accounting behavior, and assignment history except for the explicit editing exceptions below.
- Add no dependency and perform no unrelated refactoring.
- Existing file references remain unchanged. The new numbering format applies to newly created files.
- Use an isolated database copy for upgrades and test runs. Implementation and deployment are separate from approval of this document.

## Requirements and decisions

| # | Requested behavior | Planned result |
|---|---|---|
| 1 | ETO/ETA and ETOL as dates | ETA becomes a Date across Sales, File Numbers and Trips; ETOL already is a Date and remains one. |
| 2 | Analytical account editable after confirmation | Edit the line's `analytic_account_id` directly on the confirmed Sales Order, including when the order is locked. |
| 3 | Hide Size by default in Sales lines | Set the Sales Order line column to optional/hidden; users can show it. |
| 4 | Weight accepts numbers only | Accept decimal values and empty input; reject text and unit suffixes on save through both UI and API. |
| 5 | Replace Clean with Clear | Display “Clear Container” and “Clear All Variables” in the duplicate-line dialog. |
| 6 | Always clear Weight | Both duplication modes clear Weight in the new line; the source remains unchanged. |
| 7 | Assign trailer from truck and backfill truck on trailer | Add Default Trailer on trucks and Assigned Truck on trailers, derived from the same relationship. |
| 8 | File reference format | New references use `FN/YYYY/MM/NNNNN`, for example `FN/2026/11/00042`. |
| 9 | Enlarge list columns | Apply the existing Trip Not Started readability pattern to Without Allocation and Trips Summary, with useful widths and wrapping. |
| 10 | Edit ETOL without Assign Truck | Inline ETOL editing in Sale Orders without Allocation; saving must not allocate anything. |
| 11 | Hide checkpoint progress for draft trips | Hide draft checkpoint status/progress displays in the Trips milestone list and draft progress bars in Trips Summary. |

### Explicit interpretation of checkpoint progress

The milestone list (`taj.transport.milestone`, labelled Trips) currently has a checkpoint `state` badge, but no progress-bar field. Trips Summary (`taj.transport.trip`) has `milestone_progress`. Apply the draft condition to both displays to cover the wording of the request. Keep the separate Trip Status badge visible. Hide cells for draft records, not the whole column in mixed lists. Do not hide checkpoint names or delete checkpoint data.

### Smallest compatible numeric-weight approach

Weight is currently a Char field and is copied into invoice/purchase flows. Keep that storage type and add strict numeric validation at the Sales entry boundary; this satisfies numeric-only input without changing historical accounting schemas. Accept empty input, `0`, `1250`, and `1250.5`; trim surrounding whitespace. Reject negatives, letters, unit suffixes, thousands separators, scientific notation, `NaN`, and infinity. Explain the expected decimal format in the field help/error message. Numeric validation occurs on save; a custom keystroke-blocking widget is outside this plan.

Existing text weights such as `25T` must be reported for review and preserved until explicitly corrected. Editing an unrelated field must not fail because of an unchanged historical text weight. Never silently infer units or turn an invalid historical value into zero.

### Sequence and date assumptions

- Preserve the current five-digit padding and continuous sequence counter; the requested format does not imply monthly resets.
- Use the file creation/sequence date for year and month, not ETA or ETOL.
- Convert stored ETA timestamps to their calendar date in `Asia/Damascus`, the user's supplied timezone, consistently for all existing records. Review a before/after sample, especially timestamps near midnight, before applying the upgrade. A Date has no timezone after conversion.
- Preserve an export of original ETA timestamps so discarding the time component is reversible from the upgrade backup.

## File map

| Files | Responsibility |
|---|---|
| `taj_transport_operations/models/sale_order.py` | Confirmed-order write gate and dynamic form readonly modifiers. |
| `taj_transport_operations/models/sale_order_line.py` | ETA definition, numeric-weight validation, narrow analytical-account/ETOL write exceptions. |
| `taj_transport_operations/models/transport_trip_summary.py` | Related ETA Date on Trips. |
| `taj_transport_operations/models/transport_trip_file.py` | Related ETA Date and date-only file summary label/refresh. |
| `taj_transport_operations/models/fleet_vehicle.py` | Stored truck/trailer pairing, reverse display and validation. |
| `taj_transport_operations/views/sale_order_views.xml` | Hidden Size, editable analytical account, inline ETOL and dispatch widths. |
| `taj_transport_operations/views/transport_trailer_views.xml` | Fleet pairing display; currently its Vehicle Availability form is read-only. |
| `taj_transport_operations/views/transport_trip_views.xml` | Summary widths and draft progress visibility. |
| `taj_transport_operations/views/transport_milestone_views.xml` | Draft checkpoint-state visibility. |
| `taj_transport_operations/static/src/scss/dispatch_readability.scss` | Scoped list wrapping using existing `o_taj_dispatch_readable` class. |
| `taj_transport_operations/wizard/duplicate_sale_line_wizard.py` | Labels and unconditional weight clearing. |
| `taj_transport_operations/wizard/duplicate_sale_line_wizard_views.xml` | Accurate duplication help text. |
| `taj_transport_operations/data/ir_sequence.xml` | New file prefix. |
| `taj_transport_operations/__manifest__.py` | Reserve next unused release version; proposed `19.0.1.44.0`. |
| `taj_transport_operations/migrations/19.0.1.44.0/pre-migration.py` (new) | Explicit, guarded ETA timestamp-to-date conversion. |
| `taj_transport_operations/migrations/19.0.1.44.0/post-migration.py` (new) | Refresh stored file summaries and verify existing sequence configuration. |
| Existing tests named below; `taj_transport_operations/tests/test_shipment_entry_edits.py` (new) | Focused validation tests for numeric entry and current date migration. |
| `taj_transport_operations/tests/__init__.py` | Register the new focused test file. |
| `taj_transport_operations/README.md` | User instructions and upgrade behavior. |

`move_invoice_line/models/sale_order_line.py` defines the original Weight and Analytical Account fields. Override only the required behavior in the transport module. Review invoice/purchase propagation in that addon when testing compatibility; no schema change there is planned.

## Task 1: Sales column defaults and duplication wording/clearing

**Files:** `views/sale_order_views.xml`, `wizard/duplicate_sale_line_wizard.py`, `wizard/duplicate_sale_line_wizard_views.xml` under `taj_transport_operations`.

- [ ] Set `size` to `optional="hide"` in the inherited Sales Order line list. Keep the column available through the column selector.
- [ ] Change the selection labels to Clear Container / Clear All Variables. Retain the internal `clean_mode` name and `container`/`all` keys to avoid unnecessary compatibility work.
- [ ] Move `weight: False` into the unconditional values cleared by duplication. Preserve current behavior for all other fields, including fresh vehicle/analytical allocation.
- [ ] Update the Clear Container help to say that Container Number and Weight are cleared. Keep the Clear All Variables list accurate.
- [ ] Update `tests/test_duplicate_sale_line.py`: remove Weight from the preserved-fields assertion for container mode; assert both modes clear Weight, preserve the original Weight, and show the new labels.
- [ ] Verify the compiled Sales view in `tests/test_sale_line_columns.py` and manually show/hide Size using a fresh user's column preferences.

**Acceptance:** Both duplication modes produce an empty Weight, the original line is unchanged, and a fresh Sales view hides Size.

## Task 2: Date-only ETA and ETOL across shipment views

**Files:** `models/sale_order_line.py`, `models/transport_trip_summary.py`, `models/transport_trip_file.py`, the new release migrations and manifest.

- [ ] Change all three ETA definitions from Datetime to Date together; related fields must match their source type. ETOL requires no type conversion.
- [ ] Change the file aggregate label from ETAs (UTC) to ETAs. Continue using the existing formatter, which already handles Date fields without a time suffix.
- [ ] Add a pre-migration guarded by the actual PostgreSQL column type. Convert non-null legacy ETA timestamps to the agreed calendar date; preserve nulls. Do not alter allocation, activity, milestone or trip lifecycle timestamps.
- [ ] Refresh stored `etas` summaries after upgrade so historical files no longer show cached time/UTC strings. Leave old migration scripts unchanged; they document historical releases and may still run during older upgrades.
- [ ] Replace current datetime-specific ETA expectations in `tests/test_trip_dispatch_summary.py` with date propagation tests. Retain tests of earlier migrations and add separate coverage for the new conversion, null values, near-midnight cases, and repeated execution.
- [ ] Inspect Sales Order lines, Without Allocation, Trip Not Started, File Number shipment lines, and Trip Summary forms/lists: all ETA and ETOL editors show a calendar date without a time selector.

**Acceptance:** One saved calendar date appears consistently across every related record, and migrated date values match the reviewed sample.

## Task 3: Numeric-only Weight

**Files:** `models/sale_order_line.py`; new `tests/test_shipment_entry_edits.py`; test registration.

- [ ] Add one shared validator invoked for explicit Weight input in both create and write. Validate the entire string using a simple decimal grammar; normalize surrounding whitespace only. Treat `False`, `None`, and empty text as clearing the field; retain numeric zero as `0`.
- [ ] Return a clear ValidationError for invalid input, for example: “Weight must be a number, such as 1250 or 1250.5. Do not include units.”
- [ ] Apply validation before any shipment-write escalation so File Numbers and normal Sales writes enforce the same rule. An RPC context flag must not disable validation.
- [ ] Verify duplication clears Weight successfully. Verify unchanged historical Weight is not revalidated by ETOL or analytical-account writes.
- [ ] Test valid integer/decimal/zero/empty values and invalid `25T`, `abc`, `1,250`, `-5`, `1e3`, and `NaN` through create, ordinary write, and editable File Number rows.
- [ ] Check invoice and purchase propagation using `tests/test_sales_accounting_integration.py` and `move_invoice_line/tests/test_invoice_line_propagation.py`. Some existing base-addon fixtures deliberately use unit suffixes; adapt transport-installed fixtures to numeric text while keeping meaningful propagation coverage.

**Acceptance:** Every new or changed Sales shipment Weight is numeric or empty, decimals work, and existing accounting data is preserved.

## Task 4: Analytical account editable on confirmed Sales Orders

**Files:** `models/sale_order.py`, `models/sale_order_line.py`, `views/sale_order_views.xml`; `tests/test_confirmed_sale_lock.py`.

- [ ] Allow users with normal write access to the order/line to change only `analytic_account_id` after confirmation, including locked orders. Restrict the selected account to the order's allowed company or a shared account.
- [ ] Add an explicit field whitelist in the line write gate. Do not reuse a client-supplied context Boolean to bypass the confirmed-order lock.
- [ ] Support saving from the actual Sales Order form: the parent `sale.order.write()` currently rejects all nested edits. Accept only nested updates to existing lines belonging to that order, containing only the analytical account field. Reject line create/delete/link/reparent commands and any mixed commercial changes.
- [ ] Adjust `_get_view()` and the inherited line modifiers so the parent One2many no longer prevents the allowed cell edit. On confirmed orders, retain readonly modifiers for every other line/header field and retain the restrictions on adding/removing lines. Check both inline editing and the line popup.
- [ ] Preserve the current `analytic_distribution` behavior; do not silently overwrite it or rewrite posted accounting entries as part of this request. Log the analytical account change with old/new values on the Sales Order.
- [ ] Add positive tests for setting, changing and clearing the account through line writes and nested order saves as an ordinary salesperson. Add negative tests for mixed account/price writes, cross-company accounts, foreign line IDs, line creation/removal and spoofed context flags.
- [ ] Verify that existing Edit Unit Price and Sales-to-invoice flows still pass their regression tests.

**Acceptance:** A salesperson can save the analytical account directly on a confirmed locked order while unrelated edits remain blocked.

## Task 5: Truck and trailer pairing with reverse display

**Files:** `models/fleet_vehicle.py`, `views/transport_trailer_views.xml`; `tests/test_fleet_defaults.py`, `tests/test_allocation_concurrency.py`.

- [ ] Store a `transport_default_trailer_id` Many2one from truck to `fleet.vehicle`, with trailer category, active-record and company filters.
- [ ] Enforce uniqueness of the selected trailer at database level. Reject assigning a trailer already paired to another truck with a message identifying the existing pairing; the user can clear that pairing first.
- [ ] Expose a readonly `transport_assigned_truck_id` on trailers, derived from the inverse of the stored truck link. Clearing or replacing a truck's trailer must automatically clear/update that reverse display. Avoid two independently stored links and recursive write synchronization.
- [ ] Validate resource kinds, self-links, allowed company pairing, and category/company changes that would invalidate a pairing. Preserve existing truck treatment where an uncategorized Fleet vehicle is accepted as a truck. Validate the reverse dependents when the trailer itself changes.
- [ ] Add Default Trailer to the editable standard Fleet truck form and Assigned Truck to the standard Fleet trailer form by inherited views in the existing XML file. Also expose them read-only in Vehicle Availability, whose current form intentionally has `edit="0"`.
- [ ] Pairing is a default relationship only: it does not reserve a trailer, change Availability, or modify current/historical trip assignments. Automatically preselecting trailers in trip resource wizards is a separate optional behavior and is not required here.
- [ ] Test assignment, clearing, replacement, duplicate-pair rejection, invalid categories/company links, and reverse display. Include one two-transaction uniqueness check using the existing concurrency-test pattern.

**Acceptance:** Selecting trailer T on truck A immediately shows A on T. Clearing/replacing the selection updates T, and two trucks cannot share one default trailer.

## Task 6: File numbers as FN/year/month/number

**Files:** `data/ir_sequence.xml`, release post-migration if required; `tests/test_trip_files.py`, `tests/test_file_grouping_concurrency.py`.

- [ ] Change the file sequence prefix to `FN/%(year)s/%(month)s/`, retaining padding 5, the current counter, and existing company behavior. Do not change the separate Trip sequence.
- [ ] Confirm that a module upgrade updates the existing sequence record without resetting `number_next`. Add only a narrow migration update if the real database configuration prevents the XML update from taking effect.
- [ ] Test deterministic references for November and December using the sequence date/context. Verify the prefix month changes while the counter remains continuous.
- [ ] Verify old `TF/...` names are unchanged and concurrent file creation remains unique.

**Acceptance:** A newly created file looks like `FN/2026/11/00042`; existing files keep their current references.

## Task 7: Wider, readable dispatch and summary lists

**Files:** `views/sale_order_views.xml`, `views/transport_trip_views.xml`, `static/src/scss/dispatch_readability.scss`.

- [ ] Apply `o_taj_dispatch_readable` to the Without Allocation and Trips Summary lists. Keep the existing Trip Not Started treatment.
- [ ] Set matching starting widths: file reference 210px, Sales Order 160px, customer/consignee 220px, product description 280px, truck/driver 180px, ETA/ETOL 160px, Weight/Size 120px, cargo description 240px, handling instructions 280px. Leave status/action columns compact.
- [ ] Retain scoped wrapping for long text and headers; use horizontal scrolling for the wide table. Do not shrink all columns to fit the screen or globally change every Odoo list.
- [ ] Check long file references, customer names, vehicle plates, cargo text and date labels on all three screens, including ETOL while editing and grouped summary rows.
- [ ] Verify at normal laptop and wide-screen sizes and record screenshots for the implementation review. A fresh user should get the new defaults; existing saved optional-column preferences should remain respected.

**Acceptance:** Relevant values and headers are readable without relying on truncated tooltips, and input fields/buttons remain usable.

## Task 8: Inline ETOL on Sale Orders without Allocation

**Files:** `models/sale_order_line.py`, `views/sale_order_views.xml`; `tests/test_without_allocation.py`, `tests/test_confirmed_sale_lock.py`.

- [ ] Enable inline editing on `view_sale_order_line_without_allocation_list` with create/delete disabled. Make ETOL the only editable data column; explicitly mark all other fields readonly.
- [ ] Add a narrow direct-write path for ETOL-only updates to confirmed, unallocated, non-cancelled shipment lines for Transport Operations users. Reuse `_write_file_shipment()` and the existing File Number row so the audit entry and shipment checks remain consistent.
- [ ] Validate the real company/record access and unallocated state server-side. Serialize against truck allocation using the existing line lock, then re-read vehicle/trip state before accepting the edit.
- [ ] Keep the capability internal: clients cannot grant themselves shipment-write permission through context. Mixed ETOL plus price/quantity/vehicle writes must be rejected.
- [ ] Saving or clearing ETOL must not create a trip, change `vehicle_id`, or set `allocation_date`. Preserve Assign Truck and its existing requirement for a nonempty ETOL before allocation.
- [ ] Test a Transport user saving from the editable list, unauthorized access, cancelled/foreign-company lines, a concurrent allocation, mixed-field requests, audit logging and unchanged truck/trip/allocation data.

**Acceptance:** A dispatcher can edit ETOL in the row without opening Assign Truck; allocating later uses the saved date.

## Task 9: Draft checkpoint progress visibility

**Files:** `views/transport_milestone_views.xml`, `views/transport_trip_views.xml`; `tests/test_trip_dispatch_summary.py`, `tests/test_trip_milestones.py`.

- [ ] Set the milestone list checkpoint `state` badge invisible when `trip_state == 'draft'` while keeping Trip Status visible.
- [ ] Set the Trips Summary `milestone_progress` cell invisible when `state == 'draft'`. Use a per-row visibility modifier, not a record-dependent `column_invisible` expression.
- [ ] Keep ready/in-progress/completed/cancelled behavior unchanged. Only exact Draft is hidden, as requested.
- [ ] Check one mixed list containing draft, ready and in-progress trips and the grouped Trips → Not Started view. Confirm progress reappears after transitioning out of Draft.
- [ ] Verify checkpoint values, arrival/departure history and underlying progress calculations remain unchanged.

**Acceptance:** Draft records display no checkpoint progress/status cell, and started records retain their normal displays.

## Task 10: Combined verification and handoff

- [ ] Run the existing static gates documented in the module README: `python3 scripts/taj_transport_phase2_static_gate.py` and `python3 scripts/taj_transport_phase3_static_gate.py`. Update assertions only where an approved behavior intentionally changes.
- [ ] On a disposable database clone, run the relevant TransactionCase classes named in Tasks 1–9, followed by the full module suite using the README command: `odoo-bin -d "$PGDATABASE" --no-http --max-cron-threads=0 --stop-after-init -u taj_transport_operations --test-enable --test-tags /taj_transport_operations`.
- [ ] Run the affected `move_invoice_line` propagation and tax tests with the transport module installed. Verify numeric validation and narrow edit paths do not accidentally trigger blocked VAT synchronization writes.
- [ ] Rehearse the upgrade from the existing version with legacy ETA timestamps and text weights. Compare record counts, original/new ETA samples, cached summaries, counters, and existing references. Repeat the migration checks to establish guarded behavior.
- [ ] Complete UI checks as a salesperson and a Transport Operations user: confirmed-order account edit; Size default; both Clear modes; decimal/text Weight input; Fleet pairing and clearing; new file reference; long-column examples; inline ETOL; mixed-state progress visibility.
- [ ] Update the README with the numeric format, pairing location, new sequence format and editing exceptions. Record exact passing commands and screenshots in the implementation handoff.
- [ ] Review the final diff for requirement coverage and lock bypasses. Group commits by independently testable task; do not deploy as part of writing or approving the plan.

## Plan coverage review

All eleven requested outcomes map to Tasks 1–9. The confirmed user choices (decimal weights and exclusive truck/trailer pairing) are incorporated. ETA naming, continuous numbering, preservation of existing references, calendar-date conversion and the meaning of checkpoint progress are stated explicitly so the implementer does not need to invent those behaviors.

**Execution update:** The user subsequently authorized the subagents to implement this plan. The reviewed changes are in the local checkout. See [the verification record](../../TRANSPORT_EDITING_VERIFICATION_2026-11-11.md) for the results and remaining browser checks.
