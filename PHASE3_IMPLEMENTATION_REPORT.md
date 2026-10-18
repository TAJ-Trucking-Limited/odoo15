# TAJ Phase 3 — Implementation Report

**Worktree:** `/Users/hayanjabr/Desktop/TAJ_Phase3_Model_Comparison/deepseek-v4-1-flash`
**Baseline:** commit `a425a8c` (TAJ Transport Operations `19.0.1.6.4`, branch `19_upgrade`)
**Result:** TAJ Transport Operations `19.0.1.7.0` — route templates, trip milestone
records and the manual operational workflow, implemented, tested and verified on an
isolated Odoo 19 database.

Everything below was executed in this worktree only. Nothing was committed, pushed or
deployed; the shared staging/production systems and the other model's worktree were not
touched. `PHASE3_IDENTICAL_TASK.md` is unchanged.

---

## 1. Runtime verification summary

| Check | Command (from the addon's parent folder, see §7 for the environment) | Result |
|---|---|---|
| Fresh install + full test suite | `odoo -d taj_phase3_verify_fresh -i taj_transport_operations --test-enable --test-tags /taj_transport_operations --stop-after-init` | **0 failed, 0 error(s) of 179 tests** (log `phase3_verification/01_fresh_install_tests.log`, exit 0) — final round, see §10; it supersedes the 173-test round of §9 and the 156-test rows below |
| Module update + full test suite | `odoo -d taj_phase3_ds41_verify -u taj_transport_operations --test-enable --test-tags /taj_transport_operations --stop-after-init` | **0 failed, 0 error(s) of 156 tests** (log `run_tests6.log`, exit 0) |
| Phase 2 → Phase 3 upgrade drill | install `19.0.1.6.4` from `git archive HEAD`, seed Phase 2 data, then `-u` on the Phase 3 code | update exit 0, **0 ERROR/CRITICAL** in the upgrade log, Phase 2 data intact, **0 checkpoints backfilled** (§6) |
| Phase 2 static gate | `python3 scripts/taj_transport_phase2_static_gate.py` | PASS (module `19.0.1.7.0`, 34 Python / 17 XML files, 239 view fields, 35 server buttons, 27 ACL rows, 156 test cases defined) |
| Phase 3 static gate | `python3 scripts/taj_transport_phase3_static_gate.py` | PASS (17 models, snapshot-generation policy, manual-only workflow, audit on every movement, company rules, no Phase 4 code) |
| Byte compilation | `python3 -m compileall -q taj_transport_operations/ scripts/` | OK |

The suite run under `--test-tags /taj_transport_operations` executes the Phase 3 suites
**and** the whole Phase 2 regression suite in the same transaction, so the numbers above
include the Phase 2 behaviour. Per file (from the Phase 3 gate): `test_route_templates.py`
26, `test_trip_milestones.py` 29, `test_trip_foundation.py` 64,
`test_trip_lifecycle.py` 30, `test_sales_accounting_integration.py` 5,
`test_trailer_migration.py` 2.

> The counts in this section describe the state before the independent review. The review
> pass added 17 test cases (`test_route_templates.py` 35, `test_trip_milestones.py` 34,
> new `test_milestone_concurrency.py` 3), i.e. **173** in total; the final correction round
> added 6 more (`test_trip_milestones.py` 40), i.e. **179** in total. The current evidence
> lives in `phase3_verification/` — see §9 for the review round and §10 for the final
> measured numbers.

The tests were *not* a single lucky green run. The first real run of the new suites
reported `1 failed, 49 error(s) of 155 tests`; after fixing the causes it went
`5 failed, 6 error(s) of 156`, then `4 failed, 1 error`, then green (`run_tests3.log` →
`run_tests4.log` → `run_tests5.log` → `run_tests6.log`). Every failure was diagnosed to a
root cause in either the implementation or a test expectation; §5 lists the harness
compatibility fixes that were needed for this Odoo build.

---

## 2. What was implemented

### 2.1 Models

| Model | Type | Purpose |
|---|---|---|
| `taj.transport.route` | new | Company-aware route template (name, company, origin/destination, notes, active). |
| `taj.transport.route.line` | new | Ordered editable checkpoint template line: name, optional type, `sequence`, requires-business-confirmation, planned offset in hours, optional coordinates, notes. |
| `taj.transport.milestone.type` | new | Editable checkpoint-type catalog (name, sequence, active). |
| `taj.transport.milestone` | new | The trip's own checkpoint record: name, `type_name` snapshot, type link, sequence, planning snapshot (offset, requires-confirmation, template line), planned/actual arrival, actual departure, confirmed time, state, `notes`, open/closed helpers, `event_ids`. |
| `taj.transport.milestone.event` | new | Immutable audit row per movement: event type, actor, business timestamp, recorded timestamp, note, reason, before/after values, source. |
| `taj.transport.trip` | extended | `route_id`, `generated_route_id`, `milestone_ids`, counters/progress, generation/regeneration entry points, route-change policy, completion note. |
| `taj.transport.trip.create.wizard` | extended | Optional route template applied to every trip created from the Sales-backed wizard. |
| 4 wizards | new | `…milestone.action.wizard` (arrival/departure/confirmation), `…milestone.add.wizard`, `…milestone.correction.wizard` (manager), `…regenerate.milestones.wizard`. |

State machine of a checkpoint: `pending → arrived → awaiting_confirmation → completed`,
plus `departed` (confirmed departure without a separate business confirmation) and
`skipped` (manager-only, requires a reason). `completed`/`departed` count as done in the
progress bar; `skipped` is excluded.

### 2.2 Behaviour (acceptance scenarios A–I)

* **A — template lifecycle and roles.** Managers create/edit/archive templates and
  checkpoint types; Dispatchers read them but cannot create, write or delete
  (`test_route_template_keeps_ordered_checkpoints`,
  `test_route_template_checkpoints_can_be_reordered`,
  `test_route_template_refuses_duplicate_checkpoint_names`,
  `test_route_template_name_is_unique_per_company`,
  `test_dispatcher_cannot_maintain_route_templates`,
  `test_checkpoint_type_catalog_is_manager_maintained`,
  `test_route_menu_is_manager_scoped_and_views_are_valid`).
* **B — deterministic per-trip snapshot.** Selecting a template copies its lines, in
  sequence order, into the trip; both `_trip()` creation and a later explicit action
  work; two trips from one template are independent copies; editing or archiving the
  template afterwards changes neither trip
  (`test_selected_template_generates_ordered_snapshots`,
  `test_two_trips_from_one_template_are_independent_copies`,
  `test_archived_template_keeps_trip_history_and_leaves_new_trips_alone`,
  `test_create_trips_wizard_applies_the_selected_template`).
* **C — idempotent generation, safe changes.** Repeated clicks, repeated saves and a
  concurrent insert cannot duplicate a checkpoint (partial unique index
  `(trip_id, template_line_id)` plus a trip row lock); new template lines are only added;
  switching templates before any operation replaces untouched generated rows and keeps
  manual ones; after the first recorded event both regeneration and template switching are
  refused (`test_repeated_generation_and_saving_create_no_duplicates`,
  `test_generated_checkpoint_cannot_be_duplicated_by_concurrency`,
  `test_generation_picks_up_new_template_checkpoints`,
  `test_switching_template_before_operations_replaces_unrecorded_copies`,
  `test_switching_template_after_recording_is_refused`,
  `test_regeneration_wizard_reports_scope_and_keeps_manual_checkpoints`,
  `test_regeneration_is_refused_once_an_event_is_recorded`,
  `test_regeneration_is_refused_on_terminal_trips`).
* **D — manual workflow.** Arrival, departure and business confirmation are recorded by
  Dispatchers through dedicated wizards with user-supplied (default: now) timestamps;
  chronology, future timestamps, double recording, confirmation before arrival and
  recording on a non-started or terminal trip are all rejected
  (`test_arrival_departure_and_confirmation_flow`,
  `test_departure_requires_a_recorded_arrival`,
  `test_confirmation_requires_a_recorded_event`, `test_double_recording_is_refused`,
  `test_recorded_timestamps_cannot_be_in_the_future`,
  `test_departure_cannot_precede_arrival`, `test_checkpoints_follow_the_route_order`,
  `test_checkpoints_cannot_be_recorded_before_the_trip_is_ready`,
  `test_checkpoints_cannot_be_recorded_after_the_trip_arrival`).
* **E — corrections.** Transport-Manager-only correction with a mandatory reason,
  before/after audit row, chronology re-validation of the whole timeline, refusal of
  identical values, and refusal of direct writes and unauthorised users
  (`test_manager_correction_audits_before_and_after_values`,
  `test_correction_refuses_a_missing_reason_or_an_identical_value`,
  `test_correction_revalidates_the_trip_timeline`,
  `test_dispatcher_cannot_correct_recorded_timestamps`,
  `test_events_record_the_actor_and_are_immutable`,
  `test_direct_writes_are_rejected_but_notes_stay_editable`,
  `test_milestones_cannot_be_deleted`).
* **F — multi-company isolation.** Four `ir.rule` records scope templates, template lines,
  checkpoints and audit rows to the user's allowed companies; a trip refuses another
  company's template (`test_trip_refuses_another_company_template`,
  `test_templates_and_checkpoints_are_company_isolated`).
* **G — Phase 2 invariants.** Draft trips without a template stay valid and are never
  backfilled; route data never overwrites dispatcher origin/destination or Sales data
  (the route only *suggests* missing locations, on a form-level onchange); one confirmed
  sale line still yields at most one trip; the Phase 2 suites re-run green in the same
  transaction (`test_trip_without_template_stays_valid_and_is_never_backfilled`,
  `test_existing_trip_can_adopt_a_template_later`,
  `test_route_selection_only_suggests_missing_locations`, and in the Phase 2 suite
  `test_batch_three_route_timing_optional_and_independent_of_sales`, which re-checks
  origin/destination/planning independence and the unchanged invoice preparation).
* **H — terminal trips and concurrency.** Completed trips accept only the audited manager
  correction; cancelled trips refuse recording and correction; open checkpoints never
  block Ready/Start/Complete (a Chatter note lists them); every mutation locks the trip row
  (`test_completed_trip_requires_a_manager_correction`,
  `test_cancelled_trip_refuses_recording_and_correction`,
  `test_open_checkpoints_never_block_the_trip_lifecycle`).
* **I — regression, tests and UAT.** The Phase 2 gate still passes, the whole suite is
  green on a fresh install and on an update, and a staged manual UAT checklist is in
  `docs/TRANSPORT_PHASE3_UAT.md`.

### 2.3 UI

* `Transport Operations > Route Templates` (manager only) — list + form with the ordered
  `line_ids` editor.
* `Transport Operations > Checkpoint Types` (manager only); `Transport Operations >
  Milestones` (dispatchers) — list, search and form with the workflow buttons and the
  read-only *Checkpoint History*.
* Trip form — new **Milestones** tab (progress bar, counters, checkpoint list, Generate /
  Regenerate / Add Checkpoint actions) and a `milestones` smart button.
* Checkpoint form — header actions *Record Arrival* / *Record Departure* / *Confirm
  Checkpoint*, manager-only *Skip Checkpoint* / *Correct Timestamp*; only **Notes** is
  editable, everything else is readonly.
* Understandable refusals: every rejected operation raises a user-facing error that names
  the supported path (e.g. “regenerate the trip's checkpoints before operations begin, or
  skip a checkpoint with a reason”).

### 2.4 Security and integrity

* 14 new ACL rows (`security/ir.model.access.csv`): read-only Dispatcher access to
  templates, template lines and types; read + write checkpoints; **no create/unlink for
  anyone** on checkpoints and audit rows; the correction wizard is manager-only.
* 4 new record rules (`security/transport_security.xml`) on route, route line, milestone
  and milestone event.
* Server-side enforcement, not UI-only: direct `write()` on a checkpoint accepts only
  `notes`; `unlink()` is refused for everyone (permission error first for users without
  delete rights); audit rows cannot be created, updated or deleted through the ORM by
  users; generation and audit writes run with `sudo()` **after** an explicit
  `check_access("write")` and manager/company validation, so no RPC path escalates
  privileges; the milestone-type and template parents are validated against the trip's
  company.
* Concurrency: a `SELECT … FOR UPDATE` on the trip row serialises generation and
  recording; a partial unique index backs the in-memory duplicate check.

---

## 3. Files

**New (18 files, ~4 600 lines):**

```
taj_transport_operations/models/transport_route.py                 141
taj_transport_operations/models/transport_route.line (same file)
taj_transport_operations/models/transport_milestone_type.py         26
taj_transport_operations/models/transport_milestone.py             908
taj_transport_operations/models/transport_trip_milestones.py       511
taj_transport_operations/views/transport_route_views.xml           107
taj_transport_operations/views/transport_milestone_type_views.xml   46
taj_transport_operations/views/transport_milestone_views.xml       185
taj_transport_operations/wizard/trip_milestone_action_wizard.py     67
taj_transport_operations/wizard/trip_milestone_add_wizard.py        58
taj_transport_operations/wizard/trip_milestone_correction_wizard.py 76
taj_transport_operations/wizard/regenerate_milestones_wizard.py     78
taj_transport_operations/wizard/trip_milestone_wizard_views.xml    128
taj_transport_operations/data/milestone_types.xml                   36
taj_transport_operations/tests/phase3_case.py                      268
taj_transport_operations/tests/test_route_templates.py             657
taj_transport_operations/tests/test_trip_milestones.py             707
scripts/taj_transport_phase3_static_gate.py                        504
docs/TRANSPORT_PHASE3_UAT.md                                       130
```

**Modified (13 files):** `__manifest__.py` (version `19.0.1.7.0` + the new data/view
files; **no** post-migration script), `models/__init__.py`, `models/transport_trip.py`
(`route_id` in the guarded operational field set, `_report_open_checkpoints_on_completion`,
`generated_route_id` moved out of the Phase 2 audit-guard set because the generation
workflow legitimately writes it — it is now guarded by the Phase 3 mixin instead),
`views/transport_trip_views.xml`, `wizard/__init__.py`,
`wizard/create_trips_wizard.py` + `…_views.xml`, `security/ir.model.access.csv`,
`security/transport_security.xml`, `tests/__init__.py`, `README.md`,
`scripts/taj_transport_phase2_static_gate.py` (version constant; the view checker now
resolves inline sub-views to their comodel and merges `_inherit`-only classes — needed so
the Phase 2 gate still validates the Phase 3 trip form), plus the two Phase 2 test
harness fixes listed in §5.

---

## 4. Key design decisions and tradeoffs

1. **Configurable catalog instead of a hard-coded checkpoint list.** The Client
   Agreement's exact TAJ checkpoint field list is not available, so `taj.transport.milestone.type`
   was introduced and seven *editable suggestions* ship as `noupdate="1"` data (Arrival at
   Site, Loading, Departure, Border Crossing, Destination Arrival, Offloading, Other
   Checkpoint). The README, the view help text and the report all state explicitly that
   this list is **not** presented as a TAJ-approved list, and a checkpoint works with no
   type at all. This is the least-invented option that still satisfies “configurable, not
   assumed”.
2. **Snapshot semantics by construction.** The checkpoint copies the template's planning
   values at generation time (`name`, `type_name`, `requires_confirmation`,
   `planned_offset_hours`, `template_line_id`). The planned time is *derived*, not copied:
   `planned_arrival_at = trip.planned_departure_at + planned_offset_hours`, so a trip's own
   planning always wins and a template edit cannot silently rewrite history.
3. **Additive generation, explicit regeneration.** Idempotent “add what is missing” is the
   default; destructive regeneration (replace untouched generated rows, keep manual ones) is
   an explicit manager/dispatcher action that is refused as soon as any event exists. This
   is the documented, safe policy the brief asks for instead of an implicit overwrite.
4. **`generated_route_id` separates “what the user selected” from “what generated these
   rows”.** It lets the model detect a template switch and refuse it once operations have
   started, without overloading `route_id`.
5. **Manual checkpoints never get removed**, and their `sequence` is `max + 10` at creation.
   Consequence (documented in the tests): a manual stop created *before* a later template
   switch can interleave with the new template's checkpoints; the test asserts preservation
   and template order rather than a specific position.
6. **Zero-offset planning.** `planned_offset_hours = 0` means “at the trip's planned
   departure”, not “not planned” — a checkpoint whose offset is not set still shows a
   meaningful planned time when the trip has one.
7. **Open checkpoints never block the trip lifecycle.** Imposing new mandatory steps on
   already-running Phase 2 trips would be a surprise behaviour change; completion instead
   posts an insistent Chatter note listing the open checkpoints.
8. **Audit model instead of a computed “last change” field.** A separate append-only
   `taj.transport.milestone.event` model was used because the brief explicitly warns that
   Chatter alone is not an independent audit trail; the events carry before/after values
   and the reason, and are shown in the checkpoint form.
9. **`sudo()` is confined and always preceded by an access check.** Users have no
   create/unlink right on checkpoints or audit rows; the internal writes are performed with
   elevated rights *after* `check_access("write")` and company/manager validation, which is
   the only way to keep the create/unlink ACL at 0 and still generate records.
10. **Phase 3 code lives in a separate mixin file** (`transport_trip_milestones.py`) so the
    Phase 2 `transport_trip.py` diff stays at ~28 semantic lines — easier to review and to
    prove that Phase 2 behaviour is untouched.
11. **No migration script.** The change is purely additive (new tables, columns, views,
    menus, data). A script would have to invent backfilled checkpoints, which the brief
    forbids.

---

## 5. Phase 2 test-harness compatibility fixes (test-only, declared)

This Odoo build (19.0 nightly `post20261016`) changed behaviour that the Phase 2 tests
assumed. The fixes below are confined to **test files** and to the Phase 2 *static gate*
script; no Phase 2 product behaviour was altered. They are listed here because a reviewer
diffing Phase 2 files should see them.

| File | Change | Why |
|---|---|---|
| `tests/test_sales_accounting_integration.py` | `cls.env = cls.env(su=True)` in `setUpClass` (with comment) | Odoo 19's accounting `BaseCommon` switches the test environment to an independent internal user with neither Sales nor Fleet rights, so the integration boundary tests could not run at all (`AccessError` on `sale.order`). This case is about the Sales/Accounting *boundary*, not permissions; permission coverage lives in `test_trip_foundation.py`/`test_trip_milestones.py` with explicit `new_test_user` users. |
| same file | sale-order-line filter `display_type == "product"` → `not display_type` | In Odoo 19 a regular `sale.order.line` has `display_type = False` (only sections/subsections/notes set it), so the old filter returned an empty recordset. Invoice lines were left untouched (`account.move.line.display_type` really is `"product"`). |
| same file | the two `.sudo()` Fleet-seed creates reverted to plain `create` | No longer needed once the environment is `su`; keeps the test honest. |
| `tests/test_trip_foundation.py` | new `assert_raises_any(*exceptions)` helper replacing `self.assertRaises((A, B))` (6 sites), plus acceptance of `psycopg2.errors.CheckViolation` | Odoo 19's `TestCase.assertRaises` calls `issubclass(exception, AccessError)` internally and rejects a tuple of classes; violated PostgreSQL CHECK constraints also surface unwrapped as `CheckViolation`. |
| `scripts/taj_transport_phase2_static_gate.py` | version constant `19.0.1.7.0`; view checker resolves inline sub-views to their comodel and merges `_inherit`-only model classes | Without this the gate fails on the Phase 3 trip form (`route_id`, `milestone_ids`) and on fields defined in the Phase 3 mixin — i.e. it would report a false regression. The checks themselves were not weakened. |

Also noted for reviewers: several `ERROR:` lines in the Odoo logs are *expected* — they are
PostgreSQL constraint violations deliberately triggered by negative tests (duplicate trip
per sale line, duplicate checkpoint per template line, assignment check constraints) and
caught inside `assertRaises`/`savepoint()` blocks. The authoritative result line is
`odoo.tests.result: 0 failed, 0 error(s)`.

---

## 6. Safe upgrade from Phase 2 (drill log)

Executed on a dedicated database, never on a shared one:

1. `git archive HEAD | tar -x` → pristine `19.0.1.6.4` addons tree in a temp folder.
2. `odoo -d taj_p3_upgrade_check -i taj_transport_operations --without-demo=all` (exit 0,
   0 errors) — a clean Phase 2 installation.
3. Seeded real Phase 2 data through the ORM: customer, driver, Fleet truck (Truck
   category), service product, confirmed sales order + line, a trip with origin/destination,
   planned dates and route notes, one initial resource assignment, then `Ready` →
   `In Progress`.
4. `odoo -d taj_p3_upgrade_check -u taj_transport_operations` with the Phase 3 addons path
   → exit 0, `grep -cE 'ERROR|CRITICAL'` on the upgrade log = **0** (no migration script ran).
5. Verification script results:

```
MODULE version=19.0.1.7.0 state=installed
TRIP TRIP/2026/00003 state=in_progress from=Dar es Salaam to=Lusaka notes=Seeded before the Phase 3 upgrade
TRIP checkpoints=0 milestones=0            <- existing trips are never backfilled
ROUTE templates=0
MILESTONE types=7                          <- catalog seeded by the upgrade
LIFECYCLE state=completed open_checkpoints=0 progress=0.0   <- Phase 2 lifecycle still works
NEW trip=TRIP/2026/00004 checkpoints=['Loading', 'Offloading'] generated_from=Upgrade drill route
VERIFY_OK
```

So: no backfill, no corruption, no Phase 2 record rewritten, and a brand-new trip can
immediately plan checkpoints from a template after the upgrade. The same guarantees are
covered by automated tests (`test_trip_without_template_stays_valid_and_is_never_backfilled`,
`test_existing_trip_can_adopt_a_template_later`).

---

## 7. Reproducing the runtime verification

Local isolated environment used here (all of it disposable, none of it shared):

```
Odoo 19.0 nightly  odoo-19.0.post20261016
Python             3.11.14 (venv)
PostgreSQL         16, unix socket /tmp, role hayanjabr
test database      taj_phase3_ds41_verify (update path), taj_phase3_final (fresh install)
```

```sh
cd <odoo-src>
python -m odoo server -d <isolated-db> --db_user=hayanjabr --db_host=/tmp \
  --addons-path=odoo/addons,<extra-addons>,<this-worktree> \
  --no-http --max-cron-threads=0 --stop-after-init \
  -i taj_transport_operations --test-enable --test-tags /taj_transport_operations
# use -u instead of -i when the module is already installed
```

Offline, no database needed:

```sh
python3 scripts/taj_transport_phase2_static_gate.py
python3 scripts/taj_transport_phase3_static_gate.py
```

Manual UAT: `docs/TRANSPORT_PHASE3_UAT.md` — a checklist over checkpoint types, template
maintenance and reordering, generation/regeneration/switching, the manual recording
wizards, manager skips and corrections, audit integrity, company isolation, and the Phase 2
regression/upgrade expectations. It also repeats the exact CLI commands above.

---

## 8. Known risks and remaining items

1. **No human UAT was performed.** All runtime evidence is ORM/TransactionCase level plus
   view-architecture assertions (menu scoping, button groups, readonly fields, o2m wiring).
   No browser was opened, so visual/layout issues cannot be excluded; the checklist in
   `docs/TRANSPORT_PHASE3_UAT.md` is the pending manual step.
2. **The Odoo.sh staging gate (`scripts/taj_transport_phase2_staging_gate.py`) was not
   run** — it requires the shared staging database, which is out of bounds for this task.
3. **The checkpoint-type suggestions are not a TAJ-approved list** (see §4.1). TAJ must
   review and edit `data/milestone_types.xml`/the catalog UI before go-live; the delivered
   seven rows are only a starting vocabulary.
4. **Phase 2 test-harness edits** (§5) are the one place where pre-existing files needed
   behaviour-neutral changes. They are test-only, but a reviewer should still confirm they
   are acceptable in the Phase 2 deliverable.
5. **Manual-checkpoint interleaving** after a template switch (§4.5) is intentional but
   subtle; if TAJ wants manual stops to always sort last, the generator — not the
   dispatcher — must re-sequence them.
6. **Concurrency coverage is real but in-process only.** Two genuine database sessions now
   prove the row lock, the unique index and the safe retry
   (`tests/test_milestone_concurrency.py`), and they show Odoo 19 runs connections at
   `REPEATABLE READ`, so a losing writer can be refused with SQLSTATE `40001` and must
   retry. Two *separate Odoo worker processes* were not exercised; what is verified is the
   database-level outcome (one plan, no duplicates, safe retry), not the HTTP-level error
   handling of the refused request. See §9.5 and `phase3_verification/README.md`.
7. **No downgrade path.** Once upgraded, `19.0.1.7.0` cannot be rolled back to
   `19.0.1.6.4` without dropping the new tables; there is no destructive uninstall script.
8. **Deliberately unimplemented (Phase 4+):** GPS ingestion, geofencing, automatic
   timestamps, cron jobs and event-driven transitions. The audit model reserves a `gps`
   source value, and route lines carry optional coordinates, so Phase 4 can extend the same
   records without a redesign — both are inert placeholders and the Phase 3 gate asserts
   that no Phase 4 code exists.

---

## 9. Independent Review Fixes and Final Verification

An independent review of the implementation above scored it **84/100** and flagged three
critical defects plus security and concurrency gaps. This section records what changed in
response and the evidence measured afterwards. Every command below was executed in this
worktree on **2026-10-17**; the logs are in
`phase3_verification/` (`README.md` there explains each one).

### 9.1 Issue 1 — clearing the template left checkpoints and a stale marker

*Defect:* `write({"route_id": False})` cleared the relation but left the generated
checkpoints and `generated_route_id` pointing at the old template.

*Fix (policy: history is never silently deleted):* the write path now calls
`_clear_generated_milestones()`, which

1. is **Draft-only** and refuses outright when the trip is Ready/In Progress/Completed/
   Cancelled;
2. re-validates recorded progress inside the removal layer
   (`_check_milestone_change_allowed`): any milestone event, arrival/confirmation
   timestamp, or non-pending state closes the clear path with a message naming the trip;
3. removes **only** template-generated rows that hold no event at all
   (`_unlink_pending_for_regeneration`, with its own refusing backstop);
4. always keeps manually added checkpoints;
5. clears `generated_route_id` in the same transaction through
   `super(TajTransportTrip, self).write`, so the marker can never outlive the selection.
   A clear of a trip where nothing was ever generated skips all of it, so repeated clears
   are no-ops.

Callers cannot bypass the layers: the wizard, the private helpers and direct ORM writes
all pass through the same guards. A trip that was planned from a template which was
archived later **keeps its snapshot checkpoints**; it can still be re-planned while Draft,
but nothing recorded is discarded.

*Tests (section D of `test_route_templates.py`, 9 new cases):* untouched generated rows are
removed; manual checkpoints survive a clear; a repeated clear is a no-op; a clear after any
recorded event is refused (both by the client path and by the removal backstop); a failed
generation rolls the entire assignment back (no partial plan, no half-written marker);
Ready/In Progress/Completed/Cancelled reject generation, regeneration and switching;
frozen plans are not touched when events exist.

### 9.2 Issue 2 — destructive plan changes on trips already in use

*Defect:* route switching and regeneration were reachable outside Draft, and Ready trips
with zero events could lose their plan.

*Fix:* every destructive path — `write({"route_id": …})` (set, switch, clear),
`action_generate_milestones()`, `_regenerate_milestones_from_route()`, both private
replace/additive helpers and the regeneration wizard (open *and* confirm) — is **Draft-only
and re-validated server-side**, so direct ORM/RPC calls get the same answer as the UI. On
Completed/Cancelled trips the whole generation surface is closed. The trip form's
Generate/Regenerate buttons are additionally `invisible` unless the trip is in Draft.
Adding a manual checkpoint stays the sanctioned additive path for route variances and is
only refused on terminal trips.

*Tests:* `test_generation_and_regeneration_are_draft_only`,
`test_regeneration_is_refused_on_terminal_trips`,
`test_clearing_the_template_is_refused_once_the_trip_left_draft`,
`test_regeneration_is_refused_once_an_event_is_recorded`,
`test_only_a_draft_trip_can_adopt_a_template_later` (replaces the earlier, laxer
`test_existing_trip_can_adopt_a_template_later`), plus the wizard and view-architecture
assertions.

### 9.3 Issue 3 — archived and foreign templates accepted by the backend

*Defect:* the backend accepted any `route_id`; only the UI filtered archived templates, and
nothing checked company ownership or access rights.

*Fix:* one choke point, `_check_route_template_usable(route)`, used by `create()`, by the
pre-write validation in `write()` and by generation (which also covers action, regeneration
and switching). It requires, in order: the record exists, `check_access("read")` passes,
the template belongs to the trip's company, and `active` is true. `_route_from_value()`
normalises the incoming value (id, recordset, list, `False`). Validation errors roll back
the whole create, so a refused template can never leave a half-built trip.

*Tests:* `test_archived_template_is_refused_on_assignment_and_creation`,
`test_archived_template_keeps_snapshots_and_can_still_be_replaced`,
`test_archived_template_keeps_trip_history_and_leaves_new_trips_alone`, plus the
cross-company and access-right cases in the route-template and milestone suites.

### 9.4 Security hardening (adversarial pass)

* A Dispatcher cannot forge recorded facts through a manual checkpoint:
  `MANUAL_MILESTONE_FIELDS` is a whitelist and rejects `state`, `actual_arrival_at`,
  `confirmed_at`, `type_name`, `template_line_id`, `route_id` and `arrived_by_id`.
  Negative tests inject each forbidden field and assert the refusal.
* Operational notes are closed on Completed/Cancelled trips
  (`transport_milestone.write`), while remaining editable on live trips.
* The actor of every movement is taken from the environment, never from the context
  (test forges the context and asserts the environment user wins).
* The generation marker stays system-managed: direct writes to `generated_route_id` are
  refused (`test_generation_marker_is_system_managed`); only the workflow writes it, via
  `super()`.
* Timestamp corrections remain a Transport-Manager-only action with a mandatory reason and
  an immutable before/after audit row; milestone deletion is refused for everyone.
* The static gate re-checks all of this from the sources: validator usage at every entry
  point, Draft-only markers, the clear path, the whitelist, the wizard refusal and the
  view-level button visibility (`check_route_guards()`).

### 9.5 Concurrency: what is now proven, and one engine fact

`tests/test_milestone_concurrency.py` uses **two real database sessions with real
commits** (a `TransactionCase` cursor wraps the test's own transaction and cannot be
refused by anyone). It proves, deterministically and with bounded waits
(`lock_timeout`/`statement_timeout`):

1. planning holds the trip's row lock — a second session's `SELECT … FOR UPDATE` is
   refused with `canceling statement due to lock timeout`, and a duplicate `INSERT` is
   refused by the lock or by `taj_transport_milestone_trip_template_line_idx`;
2. a refused writer can retry: the identical generation call in a fresh session creates
   **0** checkpoints and leaves exactly one complete plan;
3. two racing template switches cannot mix plans: the loser is refused
   (`lock timeout`, or `could not serialize access due to concurrent update` when the
   winner committed first) and the committed plan is the winner's, complete, with
   `route_id == generated_route_id`.

While building these tests we established an engine fact worth knowing for operations:
**Odoo 19 sets every connection to `REPEATABLE READ`** (`odoo/sql_db.py`). A request that
loses a race on an already-updated row is therefore aborted by PostgreSQL with SQLSTATE
`40001` rather than quietly waiting; the request must be retried, and the retry is the
no-op proven above. "Serialized concurrent planning" therefore means *one plan, one
writer* — not "both requests always succeed".

A first attempt used an in-process thread race; it **deadlocked the test runner itself**
because both sessions additionally contend on Odoo's registry lock around commits. That is
a test-harness artifact (production workers are separate processes), and it is why the
delivered test is deterministic and thread-free. What remains unverified is therefore
narrow and stated explicitly: two separate Odoo worker processes and any HTTP-level retry
of a `40001`. The database-level outcome is verified.

### 9.6 Final verification for the review round (measured)

> Numbers of the independent-review round. The final correction round re-ran everything on
> a freshly created database and supersedes them: see §10.4.

| Check | Command | Result |
|---|---|---|
| Fresh install + full suite | `-i taj_transport_operations --test-enable --test-tags /taj_transport_operations` on new DB `taj_phase3_ds2` | **0 failed, 0 error(s) of 173 tests**, exit 0, 77 s total (install + tests). Log `01_fresh_install_tests.log` |
| Upgrade `19.0.1.6.4` → `19.0.1.7.0` | install from the committed Phase 2 tree (export of `a425a8c`), seed real records, then `-u` with this worktree | both exits 0; sales order unchanged (`2100.00`, `sale`); both trips still `draft`; driver assignment intact; version `19.0.1.7.0`; **0 milestones, 0 events, 0 routes backfilled**. Log `02_upgrade_19.0.1.6.4_to_19.0.1.7.0.log` |
| Phase 2 static gate | `scripts/taj_transport_phase2_static_gate.py` | PASS, exit 0 |
| Phase 3 static gate | `scripts/taj_transport_phase3_static_gate.py` | PASS, exit 0, 173 defined test cases |
| Byte compilation | `python -m compileall -q taj_transport_operations scripts` | exit 0 |

Two failures appeared in the first run of the *new* tests and were diagnosed, not patched
over: the concurrency helper refused to accept a `duplicate key` refusal (the unique index
was doing its job earlier than expected) and leaked its probe cursor; and one route-template
assertion still expected the pre-hardening message text while the removal layer now refuses
one step earlier. Both were test defects; the fixes are in the delivered tests.

The databases (`taj_phase3_ds2`, `taj_phase3_upg`) were dropped after the runs were
captured; `taj_phase3_glm` (the other model's database) was never touched, and
`phase3_verification/run_verification.sh` recreates the whole evidence set from scratch.

### 9.7 Status against the review

* Issue 1, Issue 2, Issue 3: fixed, with the explicit policies above and 14 regression
  tests plus a dedicated concurrency file.
* Security findings: addressed at the model layer (whitelist, notes closure, actor from the
  environment, immutable audit, system-managed marker) and re-checked by the static gate.
* Concurrency: two-session, deterministic tests; the REPEATABLE READ / `40001` behaviour is
  documented rather than hidden.
* Phase 2 compatibility: unchanged behaviour, no backfill, upgrade drill green; no existing
  test was weakened.
* Not done, by scope: no human/browser UAT, no Odoo.sh staging gate (shared database), no
  commit/push/deploy, no Phase 4 features.

**Staging-UAT readiness:** the three critical defects and the review's security and
concurrency gaps are fixed and covered by executed tests on both a fresh and an upgraded
database, so Phase 3 is ready to enter Staging UAT with
`docs/TRANSPORT_PHASE3_UAT.md` (its new sections 9–12 cover exactly these fixes). It is
not ready for Production until that manual pass happens and TAJ approves the checkpoint
type vocabulary (`data/milestone_types.xml`).

---

## 10. Final correction round

Three items were raised after the review round: a silent loss of a recorded confirmation
inside the timestamp-correction path, a UAT sentence that contradicted the progress logic,
and a verification script that did not reproduce the upgrade drill by itself. Nothing else
was touched: the plan/route guards, the security hardening and the lifecycle behaviour of
§9 are untouched, and no Phase 2 behaviour changed.

### 10.1 Recorded confirmations are no longer lost by a correction

`_correct_timestamp()` used to clear `confirmed_at`, `confirmed_by_id` and
`confirmation_note` whenever a timestamp was cleared, without an audit row of its own.
That is what the review flagged, and it is gone. The path now follows one rule:

> A timestamp correction never withdraws a confirmation. The confirmation can only
> disappear through an explicit, reasoned, manager-only cancellation that leaves its own
> audit row.

Concretely, in `models/transport_milestone.py`:

* `_surviving_events(cleared_field)` returns the recorded events the checkpoint keeps
  after the change. Clearing a departure while an arrival remains keeps the confirmation
  (the arrival still supports it) — this is the case the old code destroyed.
* When the correction would leave the confirmation with **no** operational event at all,
  the call is refused before anything is written:
  *"… is confirmed by … on …. Clearing its last recorded event would leave the
  confirmation without any operational fact. Cancel the confirmation first (with a
  reason), then correct the timestamp."*
* `_cancel_confirmation(reason, note=None)` is the sanctioned withdrawal path. It is
  manager-only (`AccessError` otherwise), requires a non-empty reason
  (`ValidationError`), takes the same trip row lock as lifecycle transitions, refuses
  cancelled trips and checkpoints without a confirmation, recomputes the state from the
  events that remain, and logs one immutable `confirmation_cancelled` audit event
  carrying the withdrawn confirmation (time, user, note), the reason, the author and the
  moment of the withdrawal, plus a Chatter message on the trip.
* Both methods run their writes inside `self.env.cr.savepoint()`: a failure in the audit
  row or in the Chatter leaves no half-corrected checkpoint behind.
* UI: the *Cancel Confirmation* button on the checkpoint form (Transport Manager only,
  visible once a confirmation exists) opens the existing action wizard, which gained the
  `cancel_confirmation` action with a mandatory reason. The wizard is not the gate — the
  model refuses a Dispatcher exactly the same way.
* Vocabulary: one new audit event type, `confirmation_cancelled` ("Confirmation
  Cancelled"), `field_name = "confirmation"`, `old_value` holding the withdrawn
  confirmation, `new_value` empty. The original `confirmation` event is never touched:
  history is superseded, never erased.

Regressions (section F of `tests/test_trip_milestones.py`, 5 cases): correcting a departure
keeps the confirmation; clearing a departure with an arrival remaining keeps it; clearing
the last event of a confirmed checkpoint is refused with nothing written (no timestamp, no
state change, no audit row); the explicit cancellation produces the complete audit row
(before values, reason, user, time) and reopens the checkpoint; an empty reason, a
Dispatcher calling the method, and a Dispatcher sending the wizard are all refused while
the confirmation stays recorded.

### 10.2 Progress: the code was right, the UAT sentence was wrong

`_compute_milestone_stats()` counts a checkpoint as done when its state is `completed`
(confirmed by hand) or `departed` (the checkpoint needs no confirmation);
`arrived`/`awaiting_confirmation`/`pending` are not done, and `skipped` checkpoints leave
both the numerator and the denominator. `_recompute_state()` derives exactly those states
from the recorded events plus the confirmation, so the two agree by construction.

`docs/TRANSPORT_PHASE3_UAT.md` claimed a checkpoint counts as done "once it arrived",
which never matched the implementation. The sentence is corrected (arrival is not
completion; departure or confirmation is), and the behaviour is now pinned by
`test_progress_counts_departures_and_confirmations_not_arrivals` (section G): arrival-only
is 0 % done, a departure and a confirmation each add one, a skipped checkpoint leaves the
denominator, and cancelling a confirmation reopens the checkpoint and lowers the bar.
No progress logic was changed, because there was no functional defect to fix.

### 10.3 The verification script now reproduces the whole upgrade drill

`phase3_verification/run_verification.sh` used to call the upgrade half "run by hand" and
depended on a seed script and a Phase 2 tree that were not part of the deliverable. It is
now self-contained and safe to re-run:

* Stage 1 exports the frozen Phase 2 tree with `git archive a425a8c` (the whole committed
  tree, because the module depends on sibling addons such as `move_invoice_line`), asserts
  the exported manifest reads `19.0.1.6.4`, and installs it into a fresh database.
* Stage 2 seeds real records through `odoo shell` + `phase3_verification/seed_p2_upgrade.py`
  (customer, service product, confirmed sale order of 2100.00, two trips, one driver
  assignment) and commits them.
* Stage 3 upgrades the database with `-u` from the worktree, stage 4 re-reads the records
  in the same database and asserts the Phase 2 invariants plus the absence of any backfill,
  and stage 5 diffs the before/after snapshot byte for byte (must be empty).
* Stage 6 runs both static gates and compares their test census with the number of tests
  the suite actually executed; stage 7 byte-compiles the module and scripts.
* Safety: only the two `taj_phase3_verify_*` databases by exact name, never a pre-existing
  database unless `RECREATE=1`, only a local socket host, `KEEP_DBS=1` to inspect, exit
  code 0 only on a full pass, and a single `05_verification_summary.log` listing every step
  with its exit code.

### 10.4 Final measured verification

| Check | Command (see `phase3_verification/README.md` for the environment) | Result |
|---|---|---|
| Fresh install + full suite | `-i taj_transport_operations --test-enable --test-tags /taj_transport_operations` on the new DB `taj_phase3_verify_fresh` | **0 failed, 0 error(s) of 179 tests**, exit 0, 37.94 s of tests. Log `01_fresh_install_tests.log` |
| Upgrade `19.0.1.6.4` → `19.0.1.7.0` | `git archive a425a8c` (whole tree, manifest asserted) → install → seed → `-u` → verification pass → snapshot diff | all stages exit 0, `SNAPCHECK OK`, empty diff: sale order `S00001 / 2100.00 / sale`, both trips `draft`, ids and assignment unchanged, version `19.0.1.7.0`, **0 milestones, 0 events, 0 routes, no `route_id`** on the Phase 2 trips. Log `02_upgrade_19.0.1.6.4_to_19.0.1.7.0.log` + raw stages `02a`–`02d` |
| Phase 2 static gate | `scripts/taj_transport_phase2_static_gate.py` | PASS, exit 0 |
| Phase 3 static gate | `scripts/taj_transport_phase3_static_gate.py` | PASS, exit 0, **179 defined test cases** (census per file in `03_static_gates.log`) |
| Test census cross-check | gate census vs executed tests | **179 = 179**, enforced by the script |
| Byte compilation | `python -m compileall -q taj_transport_operations scripts` | exit 0 |
| Whole run | `bash phase3_verification/run_verification.sh` | **`VERIFICATION RESULT: PASS`**, exit 0, 135 s |

Per file in the final census: `test_trip_foundation.py` 64, `test_trailer_migration.py` 2,
`test_trip_lifecycle.py` 30, `test_sales_accounting_integration.py` 5 (Phase 2 = 101);
`test_route_templates.py` 35, `test_trip_milestones.py` 40, `test_milestone_concurrency.py` 3
(Phase 3 = 78).

Two defects were found and fixed **by running** the new script rather than by reading it:
the drill exported only the module directory (so the sibling dependency
`move_invoice_line` was missing and the Phase 2 install failed), and the `SNAPCHECK OK`
token was grepped from the filtered snapshot file instead of the raw log. Both were
script defects, not product defects; the final log set is the run after the fixes.

The two databases created by this round were dropped by the script itself; `taj_phase3_glm`
(the other model's database) and `~/Desktop/odoo15` were never touched, and nothing was
committed, pushed or deployed.

### 10.5 Remaining risks after this round

* The confirmation-cancellation and correction paths take the trip row lock, but unlike
  planning they are **not** covered by an additional two-session concurrency test
  (§9.5/§7 of the verification README). The lock can be lost as `lock timeout` or
  `40001`; the operation is idempotent in the sense that it refuses rather than
  duplicates, but a user-visible retry story is still only proven for planning.
* No human/browser UAT has run, and no Odoo.sh staging gate exists for this worktree.
* The checkpoint-type vocabulary in `data/milestone_types.xml` still needs TAJ approval.
* Two Odoo worker processes under production load, and HTTP-level retry of a `40001`, are
  outside what an in-process test can prove.

**Status:** unchanged in scope and now fully re-verified — Phase 3 is ready for Staging
UAT (`docs/TRANSPORT_PHASE3_UAT.md`, sections 9–13), not for Production.
