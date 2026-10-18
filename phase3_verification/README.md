# Phase 3 verification evidence (DeepSeek V4.1 Flash worktree)

Every log in this folder was produced by a real run in the isolated Odoo 19
environment below, on 2026-10-17. No result in
`PHASE3_IMPLEMENTATION_REPORT.md` is copied from an earlier session: the
commands, exit codes and Odoo summaries quoted there are the ones recorded
here.

The whole set is reproduced by one self-contained script:

```sh
bash phase3_verification/run_verification.sh      # 135 s in this run
```

It creates its own databases, exports the frozen Phase 2 tree from the release
commit, and ends with `VERIFICATION RESULT: PASS` plus exit code 0 only when
every stage below passed. Re-running it is safe:

* it only ever creates or drops `taj_phase3_verify_fresh` and
  `taj_phase3_verify_upgrade`, by exact name, and **refuses to touch a database
  that already exists** unless `RECREATE=1` is passed;
* it refuses any database host that is not a local socket (`/tmp` here), so it
  cannot reach a staging or production server;
* the Phase 2 side of the drill is exported with `git archive a425a8c`, never
  from the working tree and never from the git index;
* `KEEP_DBS=1` keeps both databases for inspection instead of dropping them,
  `KEEP_TMP=1` keeps the exported Phase 2 tree.

## Environment

| Item | Value |
| --- | --- |
| Odoo | `19.0-20261016` (nightly source tree `odoo-19.0.post20261016`, no git metadata) |
| Python | 3.11.14 (isolated venv: `…/opencode/odoo19env/venv`) |
| PostgreSQL | 16.14, local socket `/tmp`, user `hayanjabr` |
| Module path | `/Users/hayanjabr/Desktop/TAJ_Phase3_Model_Comparison/deepseek-v4-1-flash` |
| Module base commit | `a425a8c` on `benchmark/phase3-deepseek-v4-1-flash` (all Phase 3 changes uncommitted, as required) |
| Databases | `taj_phase3_verify_fresh` (fresh install + suite), `taj_phase3_verify_upgrade` (upgrade drill). Both created and dropped by the script in this run. `taj_phase3_glm` (the other model's database) was never touched. |

Addons path for every run: `odoo/addons,$ODOO_ENV/local_addons,<tree>`.
`local_addons` only provides `account_asset` for the Fleet/asset mapping in
this isolated environment; the sibling addons the module depends on
(`move_invoice_line`, `fleet_navirec`, …) come from the tree under test.

## 1. Fresh install + full Phase 2 and Phase 3 suite — `01_fresh_install_tests.log`

```sh
createdb -h /tmp -U hayanjabr taj_phase3_verify_fresh
cd $ODOO_ENV/odoo-19.0.post20261016
../venv/bin/python -m odoo server -d taj_phase3_verify_fresh --db_user=hayanjabr --db_host=/tmp \
  --addons-path=odoo/addons,$ODOO_ENV/local_addons,$WORKTREE --no-http --max-cron-threads=0 \
  --stop-after-init -i taj_transport_operations --test-enable --test-tags /taj_transport_operations
```

Result (verbatim from the log):

```
odoo.modules.loading: Modules loaded.
odoo.service.server: 179 post-tests in 37.94s, 24320 queries
odoo.tests.stats: taj_transport_operations: 193 tests 37.88s 24320 queries
odoo.tests.result: 0 failed, 0 error(s) of 179 tests when loading database 'taj_phase3_verify_fresh'
```

The suite includes Phase 2 (101 tests) and Phase 3 (78 tests) in one run, on a
database that did not exist before the run. The script refuses to report PASS
unless the run line says `0 failed, 0 error(s)` **and** the number of executed
tests equals the number of `def test_` cases the Phase 3 static gate counts in
the module (179 = 179), so a suite that silently shrinks cannot pass.

Test counts per file, from `03_static_gates.log` (defined cases) and the run
itself:

| File | Tests | Phase |
| --- | --- | --- |
| `test_trip_foundation.py` | 64 | 2 |
| `test_trailer_migration.py` | 2 | 2 |
| `test_trip_lifecycle.py` | 30 | 2 |
| `test_sales_accounting_integration.py` | 5 | 2 |
| `test_route_templates.py` | 35 | 3 |
| `test_trip_milestones.py` | 40 | 3 |
| `test_milestone_concurrency.py` | 3 | 3 |
| **Total** | **179** | |

Of those, 17 came from the independent-review pass (section D of
`test_route_templates.py`, section E of `test_trip_milestones.py` and all of
`test_milestone_concurrency.py`), and 6 from the final correction round
(section F — confirmation integrity under manager corrections — and section G —
progress semantics — of `test_trip_milestones.py`); see
`PHASE3_IMPLEMENTATION_REPORT.md` §9 and §10.

### Expected noise in a green run

This log contains 25 lines with `ERROR`, and none of them is a failure:

| Count | Lines | Why they are expected |
| --- | --- | --- |
| 11 | `odoo.sql_db: bad query: …` | Odoo logs a refusing query before raising |
| 11 | `ERROR: canceling statement due to lock timeout` / `new row … violates check constraint` / `duplicate key value violates unique constraint` | the negative tests: concurrent planning, duplicate planning, duplicate names, assignments without a resource or with the same driver |
| 3 | `<string>:NNN: (ERROR/3) Unexpected indentation.` | docutils rendering the module `README.md` description; identical in the Phase 2 install log, so pre-existing |

A run that is *not* green fails on the result line above, not on these.

## 2. Upgrade drill 19.0.1.6.4 → 19.0.1.7.0 — `02_upgrade_19.0.1.6.4_to_19.0.1.7.0.log`

The scripted drill replaces the hand-run one of the first pass. Stage by
stage, with the raw logs kept next to the narrative:

| Stage | Raw log | What it does |
| --- | --- | --- |
| 1 | `02a_p2_install.log` | `git archive a425a8c` → whole committed tree, asserts the manifest reads `19.0.1.6.4`, installs it into the fresh database |
| 2 | `02b_p2_seed.log` | `odoo shell` + `seed_p2_upgrade.py P2_DRILL_MODE=seed`: customer, service product, confirmed sale order (2100.00), two trips, one driver assignment, committed |
| 3 | `02c_upgrade.log` | `-u taj_transport_operations` from the worktree (19.0.1.7.0) |
| 4 | `02d_p3_check.log` | `odoo shell` + `seed_p2_upgrade.py P2_DRILL_MODE=check`: re-reads the records, asserts the invariants, prints the same snapshot |
| 5 | — | diffs the snapshot of stage 2 against stage 4: must be empty |

`EXIT_P2_INSTALL=0`, `EXIT_SEED=0`, `EXIT_UPGRADE=0`, `EXIT_CHECK=0`, and the
drill log ends with the two snapshots (`02_p2_snapshot.txt`,
`02_p3_snapshot.txt`) quoted in full.

The snapshot the two stages must agree on (`SNAP` lines are diffed byte for
byte; `version`, `milestones`, `routes`, `events`, `route_free` and `progress`
are asserted separately because they are *supposed* to differ or do not exist
yet in Phase 2):

| Snapshot | Before (`19.0.1.6.4`) | After (`19.0.1.7.0`) |
| --- | --- | --- |
| sale order | id 1, `S00001`, `2100.00`, `sale`, 2 lines | **identical** |
| trips | ids 1, 2, both `draft` | **identical** |
| record ids | order 1, product 1, customer 6, trips 1/2, driver 7, assignment 1 | **identical** |
| assignment | trip 1 → driver 7, `effective_from` set | **identical** |
| installed version | `19.0.1.6.4` | `19.0.1.7.0` |
| checkpoint rows | table does not exist | `0` on both trips |
| route rows | table does not exist | `0` |
| milestone event rows | table does not exist | `0` |
| trip `route_id` | column does not exist | empty on both trips |
| checkpoint progress | field does not exist | `0.0` on both trips |

Stage 4 ends with `SNAPCHECK OK`, printed only after every in-database
assertion held (`odoo shell` keeps reading after a traceback, so the script
requires that token instead of the exit code alone). Exit criteria of the
drill:

* no backfill of checkpoints, routes or events onto existing Phase 2 data;
* existing trips stay valid, in their lifecycle state, with their assignment;
* sales amounts and lifecycle untouched.

## 3. Static gates — `03_static_gates.log`

```
EXIT_P2_GATE=0
EXIT_P3_GATE=0
Defined Odoo test cases: 179 {'test_milestone_concurrency.py': 3, 'test_route_templates.py': 35,
  'test_sales_accounting_integration.py': 5, 'test_trailer_migration.py': 2,
  'test_trip_foundation.py': 64, 'test_trip_lifecycle.py': 30, 'test_trip_milestones.py': 40}
```

The Phase 3 gate checks, among others, the review-driven guarantees by reading
the sources: template usability at every entry point
(`_check_route_template_usable` used ≥ 4 times), Draft-only planning markers,
the atomic clear path, the manual-checkpoint field whitelist, the wizard
refusal and the view-level button visibility — plus, since the final correction
round, confirmation integrity (`check_confirmation_integrity()`): the
correction path may not write `confirmed_at` and may not call the cancellation,
the explicit cancellation must be manager-gated, reasoned, savepoint-wrapped
and must log its own `confirmation_cancelled` event, and the new manager-only
button must exist with the right group and visibility.

## 4. Byte compilation — `04_compileall.log`

`python -m compileall -q taj_transport_operations scripts` → `EXIT_COMPILEALL=0`,
Python 3.11.14.

## 5. What the concurrency tests prove (and what they cannot)

`tests/test_milestone_concurrency.py` uses **two real database sessions** on
real commits — the only way to test this, because a `TransactionCase` cursor
wraps the test's own transaction and cannot see (or be refused by) another
one.

Verified, deterministically:

1. **Planning holds a database row lock.** With session A's planning
   transaction open, session B's `SELECT … FOR UPDATE` on the same trip is
   refused with `canceling statement due to lock timeout`, and a direct
   duplicate `INSERT` is refused either by the lock or by
   `taj_transport_milestone_trip_template_line_idx`
   (`duplicate key value violates unique constraint`). Both refusals leave
   the plan untouched.
2. **A refused writer can retry safely.** After the winner commits, the
   identical `_generate_milestones_from_route()` call in a fresh session
   creates **0** checkpoints and leaves exactly the winner's three.
3. **Two racing plan switches cannot mix templates.** Session A switches to
   route C (uncommitted), session B's switch to route B is refused
   (`lock timeout`, or `could not serialize access due to concurrent update`
   when A committed first), and the committed plan is exactly route C's four
   checkpoints with `route_id == generated_route_id == route C`.

Checkpoint corrections and the new confirmation cancellation take the same trip
row lock (`_lock_trip_for_work()`), so they serialise with lifecycle
transitions and with each other; they are not covered by an additional
two-session test, which is the main residual gap listed in §7 below.

Relevant engine fact, found while building these tests: **Odoo 19 sets every
connection to `REPEATABLE READ`** (`odoo/sql_db.py`,
`set_isolation_level(ISOLATION_LEVEL_REPEATABLE_READ)`), so a writer that
loses the race may be aborted by PostgreSQL with SQLSTATE `40001`
(`could not serialize access due to concurrent update`) instead of silently
waiting. That is a refusal, not corruption: the losing request must be
retried, and the retry is the no-op proven above. Claims about "serialized
concurrent planning" therefore mean *one plan, one writer* — not "both
requests always succeed".

Not verified here (and out of scope of an in-process test): the behaviour of
two *separate Odoo worker processes* under production load, and whether a
deployment retries a `40001` at the HTTP layer. The database-level guarantees
above hold in both cases; only the user-visible error handling of the losing
request differs. An in-process thread race was tried first and deliberately
dropped: inside one process the two sessions also contend on Odoo's registry
lock around commits, which deadlocked the test runner itself (a test-harness
artifact, not a product defect — production workers are separate processes).

## 6. Confirmation integrity: the rule the correction path now follows

`_correct_timestamp()` is audited before/after and wrapped in a savepoint, so a
failure in the audit row or in the Chatter leaves no half-corrected checkpoint.
It never writes `confirmed_at`:

* correcting a time keeps the confirmation (same time, same user, same note);
* clearing a departure keeps it too, as long as an arrival remains — the
  arrival still supports the confirmation;
* clearing the **last** recorded event of a confirmed checkpoint is refused
  with a message that names the confirmation and points to the explicit path.

The only way a confirmation can disappear is `_cancel_confirmation(reason,
note=None)`: manager-only, mandatory reason, trip row lock, cancelled trips
refused, savepoint, `_recompute_state()` afterwards and one immutable
`confirmation_cancelled` audit row carrying the withdrawn confirmation (time,
user, note), the reason, the author and the moment of the withdrawal. The UI
entry point is the **Cancel Confirmation** button on the checkpoint form
(Transport Manager only, visible once a confirmation exists), which opens the
existing action wizard; the wizard is not the gate, the model refuses a
Dispatcher exactly the same way.

## 7. Progress semantics: code and documentation now agree

`_compute_milestone_stats()` counts a checkpoint as done when its state is
`completed` (confirmed by hand) or `departed` (it needs no confirmation);
`arrived`, `awaiting_confirmation`, `pending` are not done, and `skipped`
checkpoints leave both the numerator and the denominator. The UAT script said
"done once it arrived", which never matched the implementation; the wording was
corrected and the behaviour is now pinned by
`test_progress_counts_departures_and_confirmations_not_arrivals`. No progress
logic was changed, because the implementation was the correct one.

## Files

| File | Content |
| --- | --- |
| `01_fresh_install_tests.log` | fresh DB, `-i`, full 179-test suite, `EXIT=0` |
| `02_upgrade_19.0.1.6.4_to_19.0.1.7.0.log` | upgrade drill: stages, snapshots, diff, exit codes |
| `02a_p2_install.log` | raw log, Phase 2 (`a425a8c`) install |
| `02b_p2_seed.log` | raw log, seeding the Phase 2 records |
| `02c_upgrade.log` | raw log, `-u` to 19.0.1.7.0 |
| `02d_p3_check.log` | raw log, post-upgrade verification |
| `02_p2_snapshot.txt` / `02_p3_snapshot.txt` | the `SNAP` lines before and after |
| `03_static_gates.log` | Phase 2 and Phase 3 static gates, test census |
| `04_compileall.log` | byte-compilation of module and scripts |
| `05_verification_summary.log` | environment, every step with its exit code, `VERIFICATION RESULT: PASS` |
| `run_verification.sh` | the whole run above, self-contained |
| `seed_p2_upgrade.py` | the two-mode Phase 2 seed/check script the drill feeds to `odoo shell` |

Pre-existing noise, neither caused nor fixed by this pass: `Missing 'author'
key in manifest for 'account_asset'` (the isolated env's filler module),
`Two fields … have the same label` (label pairs Odoo warns about), the
`taj.transport.trailer` access-rule hint, and the RST parse messages from the
module `README.md` description (14 lines including the 3 `(ERROR/3)` ones,
identical before and after this pass).
