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
- Each **new** trip from Create Trips needs a route template linked to that
  line's product (`product_tmpl_id` on the template). Exactly one active
  same-company match is auto-selected; several matches require a per-line
  choice; none yields a clear error before any trip is created. Existing trips
  are opened unchanged. Standalone / API trip creation may still omit a
  template, and unlinked legacy templates remain selectable on the trip form.
- If no product line has a vehicle, the **Create Trips** dialog shows a yellow
  warning. The operator can select transport lines manually or cancel and assign
  a vehicle on the order. This warning does not block Draft trip creation.
- The **Trip** button on each confirmed product line opens an existing trip or
  creates exactly one when explicitly clicked (unique product-linked template,
  or a chooser when several match).
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
- Transporter (selected from the name/email catalog maintained by the
  Transport Manager).
- Transport / booking reference.
- Cargo type and actual cargo description.
- Special handling instructions.

**Sales order cargo defaults (19.0.1.12.0):** optional `cargo_type_id` and
`special_handling_instructions` on the sales order are visible to Sales users
(cargo catalog is read-only for them). Every newly created trip snapshots both
values from its sales order across the wizard, line Trip button and ORM create
paths; explicit create values win. Later order edits do not update existing
trips. An archived sales cargo type refuses creation with a clear error and
creates no partial batch.

Changes to dispatcher-maintained fields are tracked in the trip Chatter via
Odoo's standard `mail.thread` tracking (including the responsible user and
timestamp). Commercial values remain owned by Sales; the trip's new fields
do not write to Sales, Purchase, Accounting or invoice records.

**Cargo Type dropdown (19.0.1.6.3):** The Cargo tab, list and search view
now offer a configurable Cargo Type selection backed by a dedicated catalog
(`taj.transport.cargo.type`). The Transport Manager maintains options at
**Transport Operations > Configuration > Cargo Types**; Dispatcher users can only select
existing options. Suggested starting options are editable, not
TAJ-approved hard-coded classifications. The pre-existing `cargo_type`
Char field is retained and synchronized for historic data, integrations and
Chatter tracking. The 19.0.1.6.3 post-migration imports any distinct
non-empty legacy cargo type text, preserves the original text unchanged
and populates `cargo_type_id`. Archiving catalog entries does not erase
historic trips. Renaming in-use categories is intentionally blocked.

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

### Product-name route suggestions (19.0.1.6.4)

When a transport trip is created from a confirmed Sales Order line,
`Origin` and `Destination` are prefilled from the **product template name**
if it contains exactly one spaced route separator (` - `, ` – `, or
` — `). Example: `DRC kasumbalesa - TZ Dar (40ft, Normal, No
Special Requirements)` becomes `DRC kasumbalesa` / `TZ Dar`. Trailing
cargo specifications in parentheses (comma-separated or 20/40/45ft)
are excluded from the destination. Product codes, Sales line descriptions,
cargo attributes and Sales/Accounting records are not changed.

This applies to the Sales Order `Create Trips` wizard, individual Sales line
`Trip` action, direct Trip creation and the Trip form's Sales line onchange.
Any explicitly entered Origin/Destination wins; the fields remain editable
and can be corrected by dispatchers. Subsequent product renames never
silently change a trip's route.

Existing Draft trips are **not bulk changed on upgrade**. Where a location
is missing, `Route & Timing > Fill Route from Product` fills only missing
values. If the name has no clear route separator or contains multiple
candidate separators, it declines to guess and the operator enters the
route manually. The button is unavailable after the Draft state. This
feature is text-prefill only: Route Templates and Milestones belong to
Phase 3, and GPS detection remains Phase 4.

## Phase 2 / Batch 4 (Part 1): Initial Resource Assignments

Each trip now has a **Resources** tab and a button to **Assign Initial
Resources**, available only while the Draft trip has no active assignment.

- Resources use Odoo Fleet vehicles, Odoo Fleet-compatible driver/co-driver
  Contacts (`res.partner`), with both trucks and trailers stored in Fleet.
  Stable Truck and Trailer category XML IDs distinguish resource types. We do not create a second driver
  master or modify the driver's default assignment on the Fleet vehicle.
- From `19.0.1.11.1`, creating a trip automatically records its initial truck
  and driver when the original Sales truck is active, categorized as Truck,
  and has an active compatible Fleet driver. Both resources must belong to
  the trip company or be shared. This is an actual audited assignment, so the
  current truck and driver are immediately visible in Resources.
- If these defaults are incomplete or invalid, the trip remains Draft with
  manual initial assignment available. Existing Draft trips are not bulk
  changed: **Apply Fleet Defaults** applies the valid truck/driver pair to a
  Draft trip without assignment history. Assign Initial Resources remains an
  alternative for choosing resources manually. Existing assignments and Fleet
  drivers are never overwritten; use Replace Resources for subsequent changes,
  including adding a trailer to an automatic initial assignment.
- In `19.0.1.8.2`, the initial assignment wizard also suggests the selected
  truck's Fleet driver, including when opened with the initial Sales truck.
  Changing the truck refreshes the suggestion; the Dispatcher can override or
  clear it. Trucks without a Fleet driver leave the Driver field empty.
  The chosen driver is saved in assignment history, without updating Fleet.
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
- Draft, Ready and In Progress trips support replacements. The Batch 5
  lifecycle closes active history on terminal states, and prevents replacement
  after a trip is completed or cancelled.
- Server-side validation rejects invalid vehicle categories, inaccessible or
  wrong-company resources, same driver/co-driver, overlapping periods,
  replacement before the previous start, and direct history edits. Archived
  historical vehicles remain valid in closed periods; they cannot be selected
  for a new active period.
- Each replacement records the responsible user and is noted in trip Chatter.
  Trip status remains Draft. The original Sales truck snapshot, sales line
  and invoice values are not modified.

## Phase 2 / Batch 5: Trip Lifecycle

Trip status transitions now use **server-validated action buttons**, not
directly editable `state` values. Existing trips remain `Draft` on upgrade.
The intended linear lifecycle is:

- **Draft → Ready:** Requires a still-confirmed Sales Order, nonempty origin
  and destination, planned departure and arrival (strictly increasing), and
  exactly one active assignment with an **active Truck-category Fleet truck**
  and a primary driver. Trailer and co-driver are optional. Inactive or
  misclassified trucks are rejected: categorize legacy trucks as **Truck** in
  Fleet before attempting Ready. Fleet company and driver company must match
  the trip where those values are set.
- **Ready → In Progress:** Rechecks the planning/resource prerequisites.
  The current assignment must already be effective. The **Start Trip**
  action stamps `started_at`, and populates `actual_departure_at` with
  current time only if the dispatcher has not supplied it. A future or
  pre-assignment actual departure is refused.
- **In Progress → Completed:** The **Complete Trip** action requires an
  actual departure, stamps `completed_at`, and fills missing
  `actual_arrival_at` using the current time. Arrival must be later than
  departure, not in the future, and not before the current resource
  assignment began. The active assignment closes at the actual arrival,
  freeing resources without losing their immutable history.
- **Draft/Ready → Cancelled:** Dispatchers and managers may cancel with a
  mandatory explanation. **In Progress → Cancelled** additionally requires
  the Transport Manager role. Completed and Cancelled states are terminal:
  no reopen or cancellation after completion is implemented in this batch.

The `Lifecycle` tab displays system-owned timestamps, cancellation reason
and cancelling user. Trip Chatter tracks state changes and cancellation
reasons. Completed/cancelled trips cannot edit operational commercial,
cargo, route or timing fields. When a trip is finished, its `Current
Assignment` becomes empty, while the `Last Assigned Resources` panel and
full historical assignment rows remain available. Original Sales truck
snapshots and invoice preparation remain unchanged.

The `Last Assigned Resources` fields use a shared non-stored compute on
`assignment_ids`, rather than related fields traversing the non-searchable
`last_assignment_id`. This prevents the Odoo 19 inverse-dependency warnings
seen when a Fleet vehicle category is edited. No Fleet classification, Sales
truck or historical assignment is changed by this code update.

**Replacement integration:** Resource swaps remain available in
Draft, Ready and In Progress. Ready and In Progress swaps must keep a valid
truck and primary driver; in-progress swaps may not predate actual
departure. Neither completed nor cancelled trips accept resource changes.
The trip-row lock serializes lifecycle transitions and resource replacements.
Only dedicated private workflow methods write status or close an assignment;
direct ORM state/history editing remains blocked.

**Staging acceptance:** After upgrading the module, create or open a Draft
trip, assign a properly categorized truck and driver, fill the route and
planned times, and test Mark Ready → Start Trip → Complete Trip. Reopen the
record to verify timestamps, released current assignment, last-assigned
resource panel, and immutable history. Confirm valid resource replacements
in Ready/In Progress and rejection in terminal states. Test cancellation
with reason by both Dispatcher and Transport Manager users. Ensure invalid
transitions, missing prerequisites, invalid chronology and direct state
writes fail without side effects.

## Phase 3: Route Templates and Trip Milestones (19.0.1.7.0)

Phase 3 adds manual checkpoint planning and recording on top of the Phase 2
trip. It changes **no** Sales, Accounting, invoicing or Fleet behaviour, and it
never backfills existing trips.

**Route templates (Transport Manager):** `taj.transport.route` holds an
ordered, editable list of checkpoints (`taj.transport.route.line`) with a name,
an optional type, a planned offset in
hours and optional coordinates kept for a later phase. Templates are managed at
**Transport Operations > Configuration > Route Templates** and are read-only for
Dispatchers. A template name is unique per company; the same name in another
company is allowed. From `19.0.1.12.0` an optional **Sales Product** link
(`product_tmpl_id`) matches templates to sales lines for Create Trips / Trip
buttons; several active templates may share one product, and unlinked legacy
templates stay valid. On create with an explicit template, non-empty template
origin/destination take precedence over provided or product-name values; blank
template fields still use the product-name fallback. On a Draft route change,
non-empty template locations overwrite the trip; blank template fields and
clearing the template keep existing locations. The same Draft overwrite applies
when **Fill Empty Fields** assigns a different template (other wizard fields
remain fill-empty-only).

**Checkpoint types (Transport Manager):** `taj.transport.milestone.type` is a
small editable catalog at **Transport Operations > Configuration > Checkpoint
Types**. Seven starting suggestions ship as editable, not TAJ-approved data:
Arrival at Site, Loading, Departure, Border Crossing, Destination Arrival,
Offloading and Other Checkpoint. A checkpoint works without any type, and
renaming a type never rewrites the checkpoints that already used it (each
checkpoint keeps its own *Checkpoint Type at Planning* snapshot).

**Trip checkpoints (Dispatcher):** selecting a template on a trip copies its
checkpoint lines into `taj.transport.milestone` rows on the trip's **Milestones**
tab (also listed standalone at **Transport Operations > Milestones**), once,
in template order, in the same transaction as the trip. Repeating
the action only adds the lines that are still missing, and a partial unique
index on `(trip_id, template_line_id)` makes duplicated generation impossible
even for concurrent requests. The copy is a **snapshot**: editing the template
afterwards (including archiving it) never changes a trip that was already
generated. Manual checkpoints added for operational variances (a weighbridge, an
extra stop) are never removed by regeneration. Regenerating or switching the
template is only possible while nothing has been recorded yet; from the first
recorded event the checkpoint set is frozen and only a manager correction can
change recorded times.

**Recording workflow:** `pending -> arrived -> departed`, plus `skipped`.
Dispatchers record arrival and departure **by hand** through the checkpoint
form buttons. Departure completes the checkpoint; there is no separate
confirmation step. Each wizard asks for the time, defaults to now and refuses
future times. Recording is only allowed while the trip is Ready or In Progress;
completed trips accept manager corrections only, cancelled trips refuse
recording. A departure cannot precede its arrival, double recording is refused,
and departures count as done in the progress bar while `skipped` checkpoints
are excluded. Historical `completed` checkpoints retain their done status.
Open checkpoints never block Ready, Start or Complete; completing a trip with
open checkpoints posts an informational note in the Chatter.

**Audit trail:** every movement appends an immutable
`taj.transport.milestone.event` row (event type, acting user, business
timestamp, recorded timestamp, note) shown in the checkpoint's *Checkpoint
History*. Timestamp corrections are Transport Manager only, require a reason and
record the before/after values; a correction also re-validates the chronology of
the checkpoint and its neighbours. Checkpoint operational fields can only be
changed through these workflows: the milestone model accepts direct writes to
**Notes** only, and no user can create or delete checkpoints or their history
rows by hand (the messages explain the supported path instead).

**Permissions and company isolation:** Dispatchers read templates and
checkpoint types, and work with the checkpoints of their own company's trips;
Transport Managers maintain templates, types, skips and corrections. Record
rules keep templates, checkpoints and history inside the user's allowed
companies. Generation and audit writes run with elevated rights **after**
explicit access checks, so the ACL-free create/delete rules cannot be abused by
a direct RPC call.

**Upgrade:** the manifest moves to `19.0.1.7.0`. The change is purely additive
(new tables, columns, views, menus and data), so there is no post-migration
script: existing trips keep their data, receive no checkpoints, and keep
working exactly as before. See `docs/TRANSPORT_PHASE3_UAT.md` for the manual
acceptance script and the runtime test command.

## Configuration and Transporters (19.0.1.8.0)

**Transport Operations > Configuration** groups Cargo Types, Route Templates,
Checkpoint Types and Transporters. Transport Managers maintain these catalogs;
Dispatchers select their values on trips.

The Transporters catalog (`taj.transport.transporter`) has a required **Name**
and optional **Email**. The trip's Transporter dropdown selects from this catalog.
Transport Managers can add, edit and delete unused transporters. A transporter
referenced by a trip cannot be deleted.

On upgrade, the migration copies Contacts selected as trip transporters into
the catalog and reconnects every affected trip, including closed trips. Trips
that shared a Contact share one catalog entry; different Contacts remain
separate even when they have the same name. Contacts themselves are unchanged.
Only selected Contacts are copied. Fresh installations start with an empty
Transporters catalog.

**Default transporter (19.0.1.8.1):** New trips preselect the transporter named
`TAJ` (case-insensitive). This applies to the Trip form, Sales line action and
Create Trips wizard. Dispatchers can select another transporter or clear the
field; explicit selections are preserved. If no `TAJ` transporter exists, the
field stays empty. Existing trips are not changed.

## Arrival and departure workflow (19.0.1.9.0)

The checkpoint workflow uses **Arrival → Departure**. Recording Departure
completes every checkpoint, including Loading and Offloading. Confirm,
Cancel Confirmation and Requires Business Confirmation are removed from the
trip, milestone, template and Add Checkpoint screens.

The upgrade moves checkpoints still Awaiting Confirmation to Departed when a
departure is already recorded, or Arrived when only an arrival exists. It
never invents a departure time or changes an audit event. Historically
completed confirmations keep their status and are shown as read-only history.
The legacy columns and event types remain available to preserve that history.

Manager timestamp edits use the same arrival/departure rules. Clearing a
departure reopens the checkpoint; an arrival cannot be cleared while a
departure remains. Editing a historical checkpoint preserves its confirmation
evidence and derives its status from the corrected movement times.

## Route checkpoint offsets in days and hours (19.0.1.9.1)

Each route-template checkpoint shows editable Planned Offset (Days) and
Planned Offset (Hours). Entering days multiplies by 24 to update hours;
entering hours divides by 24 to update days. Fractional days are supported:
2 days equals 48 hours, and 36 hours equals 1.5 days.

Hours remain the stored duration used to generate trip checkpoints. Days
are another way to edit that duration, so existing offsets need no migration
and previously generated trip checkpoints keep their snapshots.

## Proof of Delivery (19.0.1.9.2)

Trips have a **Proof of Delivery** tab with one optional document/image upload
and optional Delivery Notes. The file keeps its original filename and is
stored as an attachment on the trip, using the trip's access permissions.
Transport users can upload or replace the file and edit notes after completion
so delivery paperwork can be recorded when it arrives. Filename and note
changes are tracked in Chatter. POD is optional and never blocks completion.

## Milestone navigation and Sales references (19.0.1.9.3)

The Milestones menu follows Trips directly and opens grouped by Truck, then
Trip. The trip's Milestones smart button uses the same grouping. Finished
milestones use the truck assigned at their recorded departure time; skipped
milestones use the truck assigned at the skip event time. At an exact truck
replacement time, the replacement truck takes precedence. Correcting a
departure time updates the grouping from assignment history without editing
that history. Missing historical assignments appear under **Unassigned**.
Unfinished milestones follow the assigned truck, retain the last truck after
trip closure, and use the original Sales truck before any assignment exists.
A driver-only assignment stays Unassigned. Archived trucks remain visible in
historical groups. All three milestone menus allow Trip and Truck grouping.
Milestone lists and forms show the linked Sales Order and Customer as
read-only references, and both are available in the milestone search.

## Vehicle Availability (19.0.1.10.0)

**Vehicle Availability** replaces the Trailers menu. It lists all Fleet vehicles
visible to the user, including trucks and trailers. Archived vehicles are hidden
by default and available through the Archived filter. Fleet remains the place
to create and maintain vehicles; the availability list and form are read-only.

Availability is calculated automatically:

- **Assigned:** a Draft trip reserves its original Sales truck immediately.
  Once initial resources are recorded, only the explicitly selected truck and
  trailer are reserved. Current assignments on Draft or Ready trips are Assigned.
- **Busy:** a current resource assignment belongs to an In Progress trip.
- **Standby:** no active reservation or assignment remains.

Busy takes priority over Assigned when the same vehicle has several trips.
Replacement releases the previous resources; completion and cancellation release
the trip's resources. Another active trip can keep the vehicle Assigned or Busy.
Future-dated initial assignments reserve resources as Assigned.

Active Trips lists only trips readable in the user's selected companies and
opens them through the Trips button. Shared vehicles still report their actual
availability when a reservation belongs to another company, without revealing
that company's trip details. The optional Fleet Driver column is the vehicle's
default driver; trip-specific driver overrides remain on each trip.

The status supports filters and grouping. It does not enforce double-booking
prevention across trips. Fleet status, categories, drivers, Sales snapshots,
assignment history and Navirec data are unchanged.

## Empty-field counters on trip tabs (19.0.1.10.2)

Trip tab labels show the number of empty visible fields, such as `Cargo (3)`.
The count updates from the form's current values, including unsaved changes.
From version 19.0.1.11.0, tabs with no empty fields show `(0)` too.
Optional and read-only data fields count too. Hidden fields and fields inside
hidden groups are excluded. Numeric zero and unchecked booleans count as filled;
whitespace-only text counts as empty. Each field counts once per tab.

An empty Milestones or Assignment History list counts as one empty field;
individual cells inside those lists are excluded. The Resources tab follows
the visible current/last resource group. Lifecycle timestamps count only in
states where they are applicable: Ready for Ready/In Progress/Completed,
Started for In Progress/Completed, Completed for Completed, and Cancelled for
Cancelled. Cancellation user/reason fields count only when visible.

The custom form renderer/compiler is registered only for the trip form;
other Odoo forms and stored trip data are unchanged.

## Fill Empty Fields wizard (19.0.1.11.0)

The trip header's **Fill Empty Fields** button opens only missing editable
Commercial, Cargo, Route & Timing and Proof of Delivery fields, grouped by tab.
Fields stay visible while filling the dialog. Entries may be left blank; Save
fills the supplied values using the regular trip write, validation and Chatter
tracking. Existing values are not cleared or overwritten, except that choosing
a different Draft route template replaces each origin/destination when the
template has a nonempty value (checkpoint generation uses the usual rules).
Route templates are offered only while Draft. Closed trips offer only
missing Proof of Delivery fields, respecting the operational lock.

Missing read-only Sales fields are listed with **Save & Open Sales Order**.
Resources and Milestones list their missing fields with links to the existing
assignment/replacement and Add Checkpoint wizards where applicable. Those
navigation buttons first save supplied trip values. Sales data, resource history
and lifecycle timestamps are never edited through this wizard. Lifecycle gaps
are listed only for applicable states.

Saving locks and re-reads the trip. If a supplied field was filled by another
user, or is no longer editable, the save is rejected and the user reopens the
wizard. The wizard uses the caller's trip access and allowed-company rules;
it does not elevate permissions. POD files and their filenames can be supplied
here, including after completion/cancellation.

## Scope intentionally deferred

Resource overlap/availability enforcement **across different trips**,
incidents, Navirec geofencing/monitoring inside trips, automated state
transitions (including GPS- or event-driven checkpoint detection), reopening
closed trips and financial analytics are **not** implemented. Route templates
and manual trip milestones were delivered in Phase 3; the `gps` event source
value exists in the audit model as a reserved placeholder only.

## Installation and testing

After the new `19_upgrade` commit is deployed to the Odoo.sh development
build, **manually upgrade** the installed TAJ Transport Operations module
in the Odoo Apps UI. Do not reinstall the module and do not upgrade or install
it automatically during Git deployment. The manifest version is `19.0.1.12.0`
(product-linked route templates for Sales trip creation, sales cargo
snapshots; Phase 3 route templates/milestones retained; Navirec compatibility:
`fleet_navirec` version `19.0.1.4.1`). The `19.0.1.8.0` upgrade migrates saved
trip transporter selections from Contacts into the dedicated catalog.

Check the source checkout without Odoo using:

```sh
python3 scripts/taj_transport_phase2_static_gate.py
python3 scripts/taj_transport_phase3_static_gate.py
```

After the **manual Odoo Apps upgrade** on Odoo.sh development/staging, run
the read-only database integrity gate:

```sh
odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
  < ~/src/user/scripts/taj_transport_phase2_staging_gate.py
```

Run the full Odoo TransactionCase test suite only on an **isolated copy**
of development/staging (not Production or the shared staging DB), since
the test command includes a module upgrade on the disposable clone:

```sh
odoo-bin -d "$PGDATABASE" --no-http --max-cron-threads=0 \
  --stop-after-init -u taj_transport_operations \
  --test-enable --test-tags /taj_transport_operations
```

The complete Thursday/Friday role, Sales/Accounting, replacement and
lifecycle acceptance matrix is in
`docs/TRANSPORT_PHASE2_THURSDAY_FRIDAY_UAT.md`.

The test suite covers one-to-one uniqueness, source/truck snapshots, no-truck
lines, non-unit quantities, initial Draft creation, idempotent actions,
wizard selection, lifecycle transitions and roles,
related Sales commercial/cargo fields, independent editable operational fields,
tracking configuration, route/timing chronology, initial resources, form
tabs, resource replacements, stale/concurrent wizard protection, timeline
validation, failed-transaction rollback, assignment uniqueness and
immutability, company separation, terminal resource release, cancellation
reason/audit fields, real draft customer invoices, VAT, payment terms,
multi-line invoice creation, cross-company record rules and invoicing invariance.

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
assigned. Create trailers in **Fleet** with an
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

Do **not** manually edit assignment history or change the Sales truck to
simulate resource reassignment. Instead, test replacements and status
transitions through their dedicated buttons on the trip.

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

The Vehicle Availability menu shows Fleet vehicles and calculated trip availability.
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
   first (if not previously done), then **TAJ Transport Operations** to
   `19.0.1.6.4`. Installing the Navirec schema first ensures that the
   tracking-request field exists before any legacy Fleet migration.
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
