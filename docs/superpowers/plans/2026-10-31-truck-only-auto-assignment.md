# Truck-only Draft Auto-assignment Implementation Plan

**Goal:** Create an audited current assignment from a valid Sales-line truck even when Fleet has no usable driver. Keep a valid driver mandatory before Ready.

**Architecture:** Extend `taj.transport.trip._assign_initial_fleet_defaults(required=False)` using the existing assignment ORM and locking helpers. Preserve the immutable truck snapshot, assignment history, company boundaries and lifecycle controls. Use the existing Replace Resources workflow to add a driver afterward.

**Tech stack:** Odoo 19, Python ORM, PostgreSQL, XML views, Odoo transaction tests.

**Spec:** The requirements and acceptance criteria below are the implementation specification. Execute tasks sequentially; no implementation is included in this handoff.

## Constraints

- Use existing Fleet vehicles and Contacts. Do not seed trucks, drivers or other business data into staging/production.
- Keep active, Truck-category and same-company/shared-vehicle validation.
- Do not edit historical assignments or `initial_vehicle_id`, and do not synchronize later Sales/Fleet changes automatically onto saved trips.
- Do not relax Ready/Start validation, access controls, or assignment immutability.
- No new dependencies, model abstractions or schema changes are needed.
- Do not commit unrelated workspace changes. Do not push/deploy without a separate instruction.

## Task 1: Add failing behavior tests

Files: `taj_transport_operations/tests/test_trip_foundation.py`, `taj_transport_operations/tests/test_trip_lifecycle.py`; add focused tests in an imported test file if that is clearer.

- [ ] Test a valid Sales truck without a Fleet driver: creating a trip creates exactly one current truck-only assignment; the trip stays Draft.
- [ ] Test a valid Fleet driver: truck and driver are both copied.
- [ ] Test inactive and wrong-company Fleet drivers: truck assignment succeeds with no driver. Shared drivers are accepted.
- [ ] Test missing, inactive, wrong-category and wrong-company trucks: automatic assignment is skipped; explicit Apply Fleet Defaults raises a useful error.
- [ ] Test driverless assignment cannot become Ready. Add a driver using Replace Resources while retaining the truck; verify immutable history and that Ready succeeds once other prerequisites are filled.
- [ ] Test repeated application never creates another initial assignment or changes existing history.
- [ ] Test other-trip assignments/reservations prevent automatic assignment, including shared vehicles used in another company, two new batch trips using the same truck, and concurrent attempts.
- [ ] Run the new tests and confirm the missing-driver cases fail for the expected reason before implementation.

## Task 2: Implement optional-driver defaults and availability checks

Files: `taj_transport_operations/models/transport_trip.py`. Consult `models/fleet_vehicle.py`, `models/transport_assignment.py` and `wizard/initial_resource_assignment_wizard.py` for existing locking/conflict patterns.

- [ ] Preserve the Draft/no-assignment-history guard and existing truck validation.
- [ ] Select the Fleet driver only when active and same-company/shared; otherwise pass `driver_id=False`.
- [ ] Create the assignment using `taj.transport.assignment.create()`, with the truck, optional driver, current effective time and accurate reason/chatter text. Do not bypass the audited workflow with direct SQL writes.
- [ ] Under the existing vehicle lock, inspect actual assignments/reservations rather than requiring `transport_availability_state == 'standby'`: the current Draft trip can reserve its own truck already.
- [ ] Reject conflicts with another trip's open assignment in Draft/Ready/In Progress, or another unassigned Draft trip reserving that truck. Check truck/trailer usage; exclude the current trip's reservation. Use elevated reads only where needed to detect cross-company occupancy, without disclosing foreign trip details.
- [ ] On automatic conflict, preserve the Draft trip and snapshot, skip assignment and post a clear explanation in chatter. On explicit Apply Fleet Defaults, raise a generic actionable error. Never steal another trip's resources.
- [ ] Recheck after acquiring locks. Concurrent automatic attempts must never create two active assignments for the same vehicle; batch conflicts may leave the affected trips unassigned, but must not double-book.
- [ ] Keep conflict checks scoped to this automatic workflow; do not claim global resource exclusivity across other APIs without verifying those paths.

## Task 3: Align UI, docs and fixtures

Files: `taj_transport_operations/views/transport_trip_views.xml`, `taj_transport_operations/README.md`, affected tests.

- [ ] Replace driver-required auto-assignment error/help text. Explain: truck auto-assigned now, driver required before Ready.
- [ ] Explain adding the driver through Replace Resources. Assign Initial Resources and Apply Fleet Defaults remain unavailable after an assignment exists; do not expose them as ways to append a driver.
- [ ] Preserve existing button visibility unless text clarification is required.
- [ ] Explain that adding a truck to Sales after trip creation cannot populate the historical snapshot. Such trips use the manual resource workflow.
- [ ] Audit tests that relied on a driverless Sales truck leaving the trip unassigned. For tests specifically exercising initial/manual assignment, use a genuinely unassigned truckless source fixture; retain meaningful assertions and separate snapshot checks. Do not broadly weaken tests.

## Task 4: Validate and prepare upgrade

- [ ] Run focused behavior/lifecycle tests, then the module suite in an isolated test database using Odoo's runner, not plain pytest:

```sh
odoo-bin -d <isolated_test_database> -u taj_transport_operations --test-enable --test-tags /taj_transport_operations --stop-after-init --no-http
```

- [ ] Report unrelated baseline failures separately and reproduce them on the unchanged baseline before classifying them as pre-existing. Known investigation area: `fleet.vehicle.transport_trip_ids` is non-stored but appears in `transport_can_assign` dependencies.
- [ ] Bump `taj_transport_operations/__manifest__.py` from `19.0.1.17.2` to `19.0.1.17.3` if still current; otherwise choose the next version. Update expected versions in `scripts/taj_transport_phase2_static_gate.py` and `scripts/taj_transport_phase3_static_gate.py`.
- [ ] Run both static gates and `git diff --check`.
- [ ] Verify manually in an isolated test database: confirmed Sales line with valid driverless truck → Create Trip → current truck visible, current driver empty → Ready refused → Replace Resources adds driver, retains truck → Ready succeeds with route/times completed.
- [ ] Review the final diff. Deliver changed files, test results, known limitations and upgrade requirements. No bulk backfill or migration of saved trips is part of this change.
