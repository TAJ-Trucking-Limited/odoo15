# TAJ Transport Operations — Phase 3 (route templates and trip milestones) UAT

**Scope:** branch of this delivery, TAJ Transport Operations version
`19.0.1.7.0`, on top of the Phase 2 release `19.0.1.6.4`.

**What Phase 3 is:** manual checkpoint planning and recording. A route template
holds an ordered, editable list of checkpoints; selecting a template on a trip
copies those checkpoints into the trip as its own snapshot; Dispatchers record
arrival, departure and business confirmation by hand, with a full audit trail
and manager-only corrections.

**What Phase 3 is not:** no GPS or event-driven automation, no automatic
timestamps, no retroactive template edits, no backfill of existing trips, no
changes to Sales, Accounting, invoicing or Fleet behaviour.

**Safety:** development/staging only. Never install or update modules
automatically on Git push, never touch Production, never run test-transaction
CLI commands against the shared staging database.

## What can be checked offline

From the project root:

    python3 scripts/taj_transport_phase2_static_gate.py
    python3 scripts/taj_transport_phase3_static_gate.py

Both gates are read-only and need no database. They check Python/XML syntax,
manifest wiring, view fields and server actions, ACL and record-rule
consistency, the snapshot/generation policy markers and the deliberate absence
of Phase 4 code. **They do not run Odoo.**

## Runtime verification (isolated database only)

    odoo -d <testdb> -u taj_transport_operations --test-enable \
        --test-tags /taj_transport_operations --stop-after-init --no-http

Phase 3 adds 78 test methods (`tests/test_route_templates.py` 35,
`tests/test_trip_milestones.py` 40 and `tests/test_milestone_concurrency.py` 3)
covering acceptance scenarios A to I plus the independent-review and final
correction regressions; the whole suite is 179 tests, and the same command also
re-runs the Phase 2 regression suites.

## Manual UAT script

### 1. Checkpoint type catalog (Transport Manager)

- [ ] **Transport Operations > Checkpoint Types** is visible to
      a Transport Manager and hidden from a Dispatcher.
- [ ] Seven editable suggestions exist: Arrival at Site, Loading, Departure,
      Border Crossing, Destination Arrival, Offloading, Other Checkpoint.
      They are starting suggestions, **not** a TAJ-approved list.
- [ ] Rename a type, then open a trip whose checkpoint already used it: the
      checkpoint keeps the **old** name in *Checkpoint Type at Planning*.

### 2. Route template (Transport Manager)

- [ ] **Transport Operations > Route Templates > New**:
      name, company, origin, destination and ordered checkpoint lines.
- [ ] Each line: name, type (optional), requires business confirmation,
      planned offset in hours, optional latitude/longitude.
- [ ] Edit the line order (drag the sequence) and reorder with the
      *Sequence* column; the form keeps the order after saving.
- [ ] A duplicate template name inside the same company is refused; the same
      name in another company is accepted.
- [ ] Dispatcher users see the templates read-only (no New/Delete, no save).

### 3. Trip checkpoints generated from a template (Dispatcher)

- [ ] On a Draft trip, **Milestones tab > Generate Checkpoints** with a route
      selected copies each template line once, in sequence order.
- [ ] Editing the template afterwards (rename a line, change the offset, add a
      line) never changes the trip; **Generate Checkpoints** only adds the
      missing lines.
- [ ] **Add Checkpoint** adds an operational variance (weighbridge, extra
      stop) that is kept by every later regeneration.
- [ ] **Regenerate Checkpoints** is refused once a checkpoint has any recorded
      event, with the message naming the reason. When nothing is recorded it
      replaces the generated, untouched checkpoints and keeps manual ones.
- [ ] Switching the trip's template is refused as soon as an event exists.
- [ ] The **Progress** bar counts a checkpoint as done once it **departed**
      (a checkpoint that needs no confirmation) or was **confirmed by hand**;
      an arrival on its own is not completion, it only means the truck reached
      the stop. Skipped checkpoints are excluded from the bar altogether.
- [ ] Open checkpoints never block Ready / Start / Complete; completing a trip
      with open checkpoints posts an informational note in the Chatter.

### 4. Recording checkpoints by hand (Dispatcher)

- [ ] Checkpoint form: **Record Arrival**, then **Record Departure**, then
      **Confirm Checkpoint** when the checkpoint requires a confirmation.
- [ ] Every wizard asks for the time (defaults to now) and an optional note;
      a future time is refused.
- [ ] Recording is only allowed while the trip is Ready or In Progress.
- [ ] Recording twice is refused (no duplicate arrival/departure/confirmation).
- [ ] Departure before arrival is refused; confirmation before arrival is
      refused.
- [ ] **Checkpoint History** on the form shows one row per event with the
      acting user, the timestamp, the recorded time and the note.

### 5. Corrections (Transport Manager only)

- [ ] A Dispatcher has no **Skip Checkpoint**, no **Cancel Confirmation** and
      no **Correct Timestamp** button; a Transport Manager has all three.
- [ ] **Correct Timestamp** requires a reason, offers only arrival/departure
      and refuses a correction that would break the checkpoint chronology.
- [ ] After the correction, the checkpoint shows the new time, the history
      shows one *Correction* row with reason, and the Chatter names the
      correcting user.
- [ ] **Cancel Confirmation** (Transport Manager only) appears as soon as a
      confirmation is recorded. It requires a reason and writes its own
      *Confirmation Cancelled* history row holding the withdrawn confirmation
      (time, user, note), the reason, the manager and the time of the
      cancellation. The checkpoint then follows the events that remain.
- [ ] Correcting a time never withdraws a confirmation on its own, and clearing
      the last recorded event of a confirmed checkpoint is refused with a
      message that points to **Cancel Confirmation**.

### 6. Audit and integrity

- [ ] Operational fields (times, users, state) cannot be edited by hand: only
      **Notes** is editable on the checkpoint form.
- [ ] Checkpoints and their history rows cannot be created or deleted by
      users; the messages explain how to proceed instead.
- [ ] Chatter on the trip records checkpoint generation, additions, skips and
      completions.
- [ ] Nothing operational disappears silently: a changed time leaves a
      *Correction* row, and a withdrawn confirmation leaves its own
      *Confirmation Cancelled* row with the previous values. No confirmation
      value can vanish without such a row.

### 7. Company isolation

- [ ] A Dispatcher in company A does not see trips, checkpoints or templates of
      company B, and cannot select a template of another company.
- [ ] A trip cannot use a template from another company, in any field.

### 8. Phase 2 regression and upgrade

- [ ] Existing trips created before the upgrade have **no** checkpoints and are
      never backfilled; they can still be marked Ready, started, completed and
      cancelled exactly as before.
- [ ] Sales order, sales line, invoice, VAT and Fleet assignment behaviour is
      unchanged: creating trips does not write to Sales or Accounting.
- [ ] Upgrading the module from `19.0.1.6.4` to `19.0.1.7.0` only adds new
      tables, columns and views. No post-migration script runs, and no Phase 2
      record is rewritten.

## Regression scenarios added by the independent review

These are the manual checks for the defects the review found. They are covered
by automated tests as well; run them by hand before Staging sign-off.

### 9. Template changes on a trip already in use (review issue 2)

- [ ] On a **Draft** trip, select, switch and clear a template: the checkpoints
      follow the selection every time.
- [ ] Make the trip **Ready** (and later **In Progress**) and try to switch or
      clear the template, or to press Generate/Regenerate: the buttons are not
      there, and a direct call/import gets a refusal that names the state. The
      plan and the "generated from" marker are unchanged.
- [ ] On a Ready trip with **zero** recorded events, switching the template is
      still refused — the plan is already in operational use.
- [ ] On **Completed** and **Cancelled** trips, no generation action exists at
      all and none can be triggered through a call or import.
- [ ] A Draft trip with one checkpoint already recorded (a time entered, or a
      skip): switching or clearing refuses and the recorded checkpoint stays.
- [ ] **Add Checkpoint** still works on Ready/In Progress trips (the sanctioned
      way to handle a route variance) and is refused on Completed/Cancelled.

### 10. Clearing a template (review issue 1)

- [ ] Draft trip, generated checkpoints untouched: clear the template. The
      generated checkpoints are gone, the "generated from" marker is empty, and
      no orphan checkpoint remains.
- [ ] Clear the template a second time: nothing happens, no error, nothing is
      deleted.
- [ ] Draft trip with a hand-added checkpoint plus generated ones: clearing the
      template removes only the generated rows; the manual checkpoint stays.
- [ ] Trip whose template was archived after planning: its checkpoints stay as
      recorded snapshots; while Draft it can still clear or replace the
      template.

### 11. Archived and foreign templates (review issue 3)

- [ ] Archive a template that a Draft trip uses: the trip keeps its checkpoints.
- [ ] The archived template is no longer offered in any template field, and
      assigning it through an import or a direct call is refused with a clear
      message.
- [ ] Creating a trip with an archived template, or with a template of another
      company, fails completely: no trip row, no leftover checkpoint.
- [ ] A user without read rights on a template cannot attach it to a trip.

### 12. Forged values and simultaneous editing (review security and concurrency pass)

- [ ] A Dispatcher cannot set a checkpoint's state, arrival/confirmation times,
      checkpoint type, or the trip's template field: the fields are absent or
      read-only in the wizard and form, and an import or call that tries it is
      refused.
- [ ] **Notes** are editable on live trips and refused on Completed/Cancelled
      trips.
- [ ] The "generated from" marker on the trip cannot be edited by hand.
- [ ] Two users plan the same trip at the same time (two browsers, Generate on
      both, or two template switches at once): exactly one complete plan is
      saved, no checkpoint appears twice, and one user sees an error. That user
      simply retries — the retry adds nothing and the plan stays complete.

### 13. Confirmation integrity and progress semantics (final correction round)

- [ ] Correct the **departure** of a confirmed checkpoint: the confirmation
      stays recorded (same time, same user, same note), no *Confirmation
      Cancelled* row appears, and the checkpoint stays Completed.
- [ ] Clear the **departure** of a confirmed checkpoint that still has an
      arrival: the confirmation is kept and the checkpoint stays Completed,
      because the arrival still supports it. The history shows only the
      *Correction* row for the departure.
- [ ] Try to clear the **last** recorded event (an arrival-only checkpoint that
      is confirmed): the correction is refused with a message that names the
      confirmation and points to **Cancel Confirmation**. Nothing changes — no
      time, no state, no history row.
- [ ] Cancel the confirmation with a reason: the checkpoint follows its
      remaining events (it returns to Awaiting Confirmation), the Progress bar
      reopens it, and the history shows the withdrawal with the previous
      values, the reason, the user and the time.
- [ ] An empty reason, a Dispatcher calling the action, or a Dispatcher sending
      the wizard cannot cancel a confirmation: all three are refused and the
      confirmation stays recorded.
- [ ] Progress semantics: a checkpoint that only **arrived** is not counted as
      done, one that **departed** or was **confirmed** is, and a **skipped**
      one is excluded from the total; cancelling a confirmation reopens the
      checkpoint and lowers the bar again.
