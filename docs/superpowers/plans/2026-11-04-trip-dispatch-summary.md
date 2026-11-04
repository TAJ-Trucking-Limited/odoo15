# Trip dispatch and summary

User requirements: the November 4 request in this session authorizes implementation.
Extend existing models and workflows; preserve existing uncommitted work and historical data.

- [x] Protect first Sales truck allocation under a row lock, exclude down payments,
  and assign resources when the selected line already has an unassigned Draft trip.
  Record the first allocation time automatically and retain it across replacements.
- [x] Rename the dispatch queue to Sale Orders - Trip Not Started, remove default
  grouping, show requested optional columns, include unstarted existing trips,
  and use the existing route chooser/create workflow to open a saved Draft trip.
- [x] Allow Start Trip directly from Draft after validating resources and route
  locations. Default actual start to now, allow a historical start through a dialog,
  retain audit timestamps, and validate corrections against assignment/checkpoint history.
  Remove planned schedules from the form and mandatory readiness checks.
- [x] Add a Summary tab and optional summary columns, recent checkpoint event/time,
  current driver, progress, allocation date, position/map and inactivity/status colors.
  Remove the commercial Sales block and summary list Record Activity action.
- [x] Rename Milestones navigation to Trips with In Progress, Completed and Not Started
  menus filtered by the parent trip state. Keep checkpoint states distinct. Add row
  Record Activity next to Open Trip and arrival actions. Inspect Ali's favorite if available;
  no matching account exists in the local test database, so user clarification is pending.
- [x] Run allocation and lifecycle regressions, actual concurrency checks, module upgrade,
  targeted suites and full suite. Independently review the final changes.

Use the existing company inactivity threshold for overdue coloring. Keep cancelled trips
visible in Trips Summary with a distinct badge. Never infer GPS coordinates from text;
reuse the existing position provider, checkpoint coordinates and map links.
Historical allocation times are populated only from actual assignment records where available.

Navigation clarification: grouped files use File Numbers to avoid a second Trips menu.
The individual-trip list is Trips Summary; checkpoint operations use Trips.

Verification: 357/357 module post-tests passed with zero failures/errors.
Focused final regression: 86/86 passed; shipment and dispatch follow-up: 36/36 passed.
Module install/upgrade and runtime views checked in disposable local Odoo 19 databases.
Independent review findings for repeatable-read allocation, start replacement boundaries,
and incomplete map coordinates are covered by regression tests and resolved.
Ali's favorite remains deferred at the user's request; remind the user at handoff.
