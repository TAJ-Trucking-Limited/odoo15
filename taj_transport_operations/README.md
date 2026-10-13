# TAJ Transport Operations (Odoo 19)

## Phase 2 / Batch 1: trip foundation

This module adds operational trip identities without changing any existing
Sales, Purchase, Accounting, Navirec or invoicing workflows.

- Each confirmed **product/service sales order line** can have **one** trip.
- The relationship is enforced by a **database unique constraint**.
- A trip starts in `Draft`, including when no truck was specified on the line.
- Trip reference is generated using `TRIP/<year>/<sequence>`.
- Sales Order > **Create Trips** opens a selection wizard. Truck-assigned lines
  without trips are suggested; an operator may add other actual transport
  lines. The button **does not** create trips automatically on confirmation.
- The **Trip** button on each confirmed product line opens an existing trip or
  creates exactly one when explicitly clicked.
- Sales Order **Trips** smart button opens all linked trips.
- Standalone **Transport Operations > Trips** menu lists and creates trips.
- The original truck is snapshotted when the trip is created, and never
  synchronized back to the sales line. Customer and commercial details are
  read-only links to their source records.
- Access requires the Dispatcher or Transport Manager group (under the
  Odoo 19 Transport Operations privilege) and appropriate read access to
  the linked Sales documents. Users in Sales must be assigned the transport
  group separately. No group is granted automatic Sales edit permissions.
- Trip visibility is limited to allowed companies. No one is granted
  deletion rights on trips in this batch.
- No migration, backfill, or bulk creation of historical trips.

## Phase 2 / Batch 2: Commercial and Cargo Details

Trips now show two clear tabs in the form, **Commercial** and **Cargo**.

**Read-only Sales source (updates reflect the linked Sales records):**
- Customer, sales order and sales line, sales unit rate, currency.
- Customer reference, source document and payment terms.
- Sales line description, container number, shipment reference (SRN), file
  name, consignee, weight, size, sales quantity and unit of measure.
- The truck at trip creation stays a historical **snapshot**, not a live
  related field. The original truck is never written back to Sales.

**Dispatcher-maintained operational details (all optional):**
- Transporter (an Odoo contact/company, within the allowed company scope).
- Transport / booking reference.
- Cargo type and actual cargo description.
- Special handling instructions.

Changes to dispatcher-maintained fields are tracked in the trip Chatter via
Odoo's standard `mail.thread` tracking (including the responsible user and
timestamp). Commercial values remain owned by Sales; the trip's new fields
do not write to Sales, Purchase, Accounting or invoice records.

All new fields are nullable, so existing Draft trips from Batch 1 continue to
work. No backfill, auto-creation, or pricing recalculation is performed.
`Sales Line Quantity` is informative and is **not** a count of trips.

## Phase 2 / Batch 3: Route & Timing

The **Route & Timing** tab adds editable, tracked origin/destination text,
route instructions, and planned/actual departure/arrival timestamps.
It validates that an arrival timestamp is not earlier than its corresponding
departure. Fields remain optional while trips are Draft. These are manual
operational timestamps, not automatic Navirec or GPS events; route templates
and milestones remain later phases. Sales and invoices are never changed.

## Phase 2 / Batch 4 (Part 1): Initial Resource Assignments

Each trip now has a **Resources** tab and a button to **Assign Initial
Resources**, available only while the Draft trip has no active assignment.

- Resources use Odoo Fleet vehicles, Odoo Fleet-compatible driver/co-driver
  Contacts (`res.partner`), with both trucks and trailers stored in Fleet.
  Stable Truck and Trailer category XML IDs distinguish resource types. We do not create a second driver
  master or modify the driver's default assignment on the Fleet vehicle.
- Existing Draft trips stay unassigned until the dispatcher explicitly
  records an initial assignment; the initial Sales truck is *suggested* in
  the wizard, but never silently assigned to a trip.
- The wizard records the selected truck/trailer/driver/co-driver (at least
  one), the **effective time and location**, reason, notes, user and
  company. The current assignment is derived from active history.
- Assignment rows are **immutable and not deletable**, even for managers.
  A partial unique PostgreSQL index guarantees at most one open assignment per
  trip. Only the trusted replacement workflow can close a historical period.
- Dispatchers and Transport Managers can create assignments and read Fleet
  trucks/trailers in allowed companies. Creating/editing Fleet trailers requires
  an existing Fleet Officer/Administrator role. Transport roles do not grant
  Fleet edit permissions; existing Fleet company rules remain in force.
- Validations reject the same partner as primary driver and co-driver,
  an empty assignment, an incorrect company and a second active row.
- These operations do not change the original sales truck or invoice
  amounts, and they do not create a trip state transition.
- Odoo records assignment creation timestamps and usernames in the
  immutable assignment record. Later changes append a fresh row rather than
  mutating an existing resource assignment.

## Phase 2 / Batch 4 (Part 2): Resource Replacements and History

Once a Draft trip has an active resource assignment, **Replace Resources**
opens a prefilled wizard with the current truck, trailer, driver and co-driver.
Dispatchers may change one or several resources, or clear an individual
resource, while keeping the others unchanged. A completely empty or identical
resource set is rejected.

- Replacement requires an effective time, nonblank reason, and optional
  effective location and notes. The time must be after the current assignment
  started and no later than the present: future-dated *active* assignments
  are not supported in this batch.
- The workflow locks the Trip's database row and checks that the wizard still
  refers to the current active assignment. Concurrent dialogs cannot silently
  overwrite each other's changes.
- The old assignment is closed at the replacement time and a new assignment
  is created at the same instant. The database transaction commits both or
  neither. Historical rows retain the original truck, trailer, drivers, start
  time, author and reason; a partial PostgreSQL unique index allows only one
  active assignment per trip.
- Server-side validation rejects invalid vehicle categories, inaccessible or
  wrong-company resources, same driver/co-driver, overlapping periods,
  replacement before the previous start, and direct history edits. Archived
  historical vehicles remain valid in closed periods; they cannot be selected
  for a new active period.
- Each replacement records the responsible user and is noted in trip Chatter.
  Trip status remains Draft. The original Sales truck snapshot, sales line
  and invoice values are not modified.

## Scope intentionally deferred

Resource overlap/availability enforcement **across different trips**,
operational status transitions, incidents, route templates and milestones,
Navirec monitoring inside trips and financial analytics are **not**
implemented in Batch 4.

## Installation and testing

After the new `19_upgrade` commit is deployed to the Odoo.sh development
build, **manually upgrade** the installed TAJ Transport Operations module
in the Odoo Apps UI. Do not reinstall the module and do not upgrade or install
it automatically during Git deployment. The manifest version is
`19.0.1.5.0` (Navirec compatibility: `fleet_navirec` version `19.0.1.3.2`).

Run Odoo tests on a **staging/development database**, not production, using
the test tag:

```sh
odoo-bin -d "$PGDATABASE" --stop-after-init -u taj_transport_operations \
  --test-enable --test-tags /taj_transport_operations
```

The test suite covers one-to-one uniqueness, source/truck snapshots, no-truck
lines, non-unit quantities, draft rejection, idempotent actions, wizard selection,
related Sales commercial/cargo fields, independent editable operational fields,
tracking configuration, route/timing chronology, initial resources, form
tabs, resource replacements, stale/concurrent wizard protection, timeline
validation, failed-transaction rollback, assignment uniqueness and
immutability, company separation and invoicing invariance.

For a manual smoke test, open an existing Draft trip; the new Commercial and
Cargo tabs should show its existing Sales data while its optional dispatcher
fields remain blank. Set a transporter, booking reference, cargo type and
special handling instructions; save and reopen. The values should persist and
the Chatter should show field-change tracking. Confirm the originating Sales
Order, sales line and existing invoice remain unchanged.

For a **new** Batch 3/4 smoke test, open the existing Draft trip and enter
Origin, Destination, planned dates and actual dates in **Route & Timing**.
Saving an arrival before its departure should fail. Open **Resources**,
choose **Assign Initial Resources**, verify the original Sales truck is
suggested, and select a driver/contact or trailer. Save and reopen:
the Current Assignment and one historical row should be visible, including
its effective time and recording user. The button should disappear once
assigned. Create trailers in **Fleet** or **Transport Operations > Trailers** with an
existing Fleet role. Choose a model and vehicle category of **Trailer**;
Dispatchers and Transport Managers without Fleet rights have read-only access.

For a **replacement** smoke test, click **Replace Resources** on an already
assigned Draft trip, change one resource and enter the effective time,
location and reason. Save and reopen. The old history row should now have
an effective end time, and a new active row should have the same start time.
Try opening two replacement wizards: complete one, then confirm the older
dialog refuses to overwrite it. Verify that entering an invalid driver,
vehicle category or effective date leaves the original assignment untouched.
The Sales truck snapshot and invoice details must remain unchanged.

Do **not** manually edit assignment history, change the Sales truck
to test reassignment, or attempt an operational status transition in
this batch.

## Sales line eligibility

The wizard suggests lines with a truck; this is a *suggestion*, not proof that
the product is a transport service. The operator must review selected lines.
Pricing/fee lines should be removed from the selection. A dedicated transport
product classifier may be introduced after real product-category review.

## Production safety

Batch 1 introduced the trip table. Batches 2/3 add optional trip columns.
Batch 4 adds optional assignment and trailer tables and related UI; the
upgrade does not change or create any existing Sales, Purchase or invoice
records, does not assign historical trips, and does not backfill data. Any installation,
upgrade or future data migration should be approved and tested on development
or staging before production.


## Fleet trailer refactor and upgrade migration

Truck and trailer selectors require active Fleet records with their respective
module-linked categories. The server checks category, visibility and company,
including API calls that bypass the wizard domains. Drivers stay independent
Contacts assignments; Fleet default drivers, Sales trucks and invoice preparation
are unchanged. Fleet vehicle deletion is restricted by assignment references.
Changing an assigned vehicle's model/category cannot change its resource type.
Company changes (including clearing the company) are blocked for both trucks and
trailers with open or closed assignment history. Unassigned vehicles can still
move company; writing the existing company remains valid.

Initialization adopts an existing case-insensitive **Truck** or **Trailer** category
without renaming it, and creates it only when absent. Multiple matching categories
stop initialization for review. XML IDs `fleet_category_truck` and
`fleet_category_trailer` remain stable even when category names change.
Categorize both Fleet models and vehicle records. Standard Odoo copies a nonempty
model category when a vehicle's model changes; editing a model's category alone
is not a bulk recategorization of its existing vehicles. Existing trucks are never
recategorized automatically. Unclassified original Sales trucks remain historical
snapshots, but cannot become new truck assignments until a Fleet administrator
sets the vehicle's Truck category.

The Trailer menu uses a Fleet form with plate, model, company, status and notes.
The standard Fleet form also hides engine/seating/hitch details for trailers.
No new `vehicle_type` value or separate driver master is introduced.

The `19.0.1.4.0` **pre-migration** translates legacy trailer IDs before Odoo changes
the assignment and transient-wizard foreign keys. It matches normalized plates in
the same company, including inactive records, and reuses only Trailer-category
Fleet records. Otherwise it creates a Fleet trailer using the dedicated
**Legacy Trailer - Unknown Model** and **Legacy Trailer - Unknown Manufacturer**
placeholder, never the first available real trailer model. The legacy identifier
and notes are preserved in Fleet description; new records preserve active status.
Existing Fleet records retain their state, driver, telemetry and plate. Ambiguous
plates, incompatible categories/companies or missing legacy references abort the
upgrade transaction instead of guessing. Empty legacy databases are supported.

A durable `fleet_vehicle_id` mapping remains on every old trailer row. A completion
marker makes a repeated migration a no-op. Historical rows retain their trip,
truck, drivers, author, dates, reason and notes; only the trailer foreign key is
translated to the equivalent Fleet entity. No production migration has been run.

Because the actual staging database has not been inspected, the old model/table
is deliberately retained as a **locked migration archive**, with no menu, no
transport ACL and blocked create/write/delete methods. It is not a trailer master
and no operational field references it. Retire the archive only in a later upgrade
once its mapping and historical references have been verified on staging.

Navirec excludes trailers without a mapping or an explicit tracking request from
automatic plate matching, mapping audit candidates and **Select All Vehicles**
for client reports. They show **No Tracking Device Required**. For a new
device-equipped trailer, a Fleet editor opens the standard **Fleet** vehicle form,
goes to **Navirec / Telematics**, enables **Navirec Tracking Required** and saves.
A Fleet administrator then runs vehicle matching in Fleet settings (or waits for
the existing matching job). Matching sets the read-only UUID after a unique plate
match; duplicate local or remote plates remain rejected. An opted-in, unmatched
trailer shows **Not Mapped**, and is included in report selection. Existing mapped
trailers remain candidates even with the checkbox off. No Fleet edit permissions
are granted to Dispatchers or Transport Managers by this change. Existing report
subscriptions and truck device mappings are not rewritten.

### Manual staging upgrade and acceptance

1. Wait for Odoo.sh `19_upgrade` to build the pushed commit successfully. Use the
   development/staging build, never production. Keep a restorable database backup.
2. In Apps, manually upgrade **TAJ Navirec Fleet Integration** to `19.0.1.3.2`
   first, then **TAJ Transport Operations** to `19.0.1.4.1`. This creates the new
   Navirec tracking-request column before legacy Fleet records are migrated.
   Do not uninstall/reinstall.
   The legacy migration runs as part of the Transport module upgrade transaction.
   If it stops on ambiguous data, review the reported legacy record and correct
   Fleet classification/matching; do not delete history to make it pass.
3. In Fleet, verify Truck/Trailer categories and model/vehicle classification.
   Check that legacy trailers have the correct plate, company, description and
   active status, and that existing assignment trailer references point to them.
4. Open an unassigned Draft trip (for example `TRIP/2026/00001` if still unassigned).
   In Resources, open Assign Initial Resources. Check the proposed Sales truck,
   select a Fleet trailer, driver and optional co-driver, save and reopen.
5. Verify current resources, one active history row, its effective time and author,
   and unchanged Sales truck snapshot, Sales line and invoice values. Category
   mismatches, same driver/co-driver, empty and duplicate assignments must fail.
6. Verify Dispatcher read-only Fleet access and cross-company restrictions with
   two users/companies. A Fleet-authorized user can maintain permitted trailers.
7. Try changing the company of an assigned truck and trailer, including a vehicle
   with closed history: changes must fail without altering Fleet or history.
8. Check Navirec matching/audit and a report preview. Unmapped trailers without
   an opt-in should be excluded. Enable Navirec Tracking Required for a trailer
   with a unique remote device plate, run matching and verify its UUID/status.
   Duplicate plate matches must fail; truck mappings and telemetry should persist.

Regression tests cover these assignment rules, a nonempty migration with colliding
legacy/Fleet IDs, archived trailers, reuse, history preservation and idempotence,
and Navirec automatic candidate/report selection. They were added for Odoo runtime
execution; local validation is limited to Python/XML/manifest/ACL/view checks.
Runtime tests and actual staging upgrade/UAT have **not** been executed in this session.


### Review fixes in 19.0.1.4.1

The corrected placeholder model applies when the legacy migration first runs.
If staging already completed the `19.0.1.4.0` migration, the mapping is not rerun
and existing Fleet models are not silently replaced. Inspect migrated trailers
against their source archive; a Fleet administrator can correct a previously
inferred model while keeping the Trailer category and assignment IDs intact.
Regression coverage adds assigned-company protection (open/closed, truck/trailer,
bulk/no-op and unassigned transfers), opt-in trailer matching with duplicate-plate
rejection, report selection, and unknown-model migration instead of real-model guessing.
