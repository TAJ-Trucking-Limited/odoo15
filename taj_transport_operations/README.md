# TAJ Transport Operations (Odoo 19)

## Phase 2 / Batch 1: trip foundation

This module adds operational trip identities without changing any existing
Sales, Purchase, Accounting, Navirec or invoicing workflows.

- Each confirmed **product/service sales order line** can have **one** trip.
- The relationship is enforced by a **database unique constraint**.
- Trips start in **Draft** and may be created without a truck. Truck and
  driver are required before **Mark Ready**.
- **Transport Operations > Trips > New** opens a trip form. Select a confirmed
  Sales Order by number, then one of its untripped product/service lines. The
  line selector shows the product, truck (or No truck), container and line
  reference to distinguish repeated products.
- **Dispatch Lines > Lines Without Trip / Lines Without Truck** offers
  **Create Trip** per untripped line and **Open Trip** for lines with a trip.
  Create Trip opens an unsaved form with that order and line preselected;
  nothing is created until Save. The same **Create Trip** / **Open Trip**
  buttons are available on product/service lines inside a Sales Order for
  transport users. Create Trip requires a confirmed order; sections and notes
  have no trip buttons. The Trips and Trip Files smart buttons remain available.
- Trip reference is generated using `TRIP/<year>/<sequence>` on Save.
- Form creation requires a route template linked to that line's product.
  One active same-company match is preselected; multiple matches require a
  choice; missing matches must be configured before saving. Changing the
  source order clears its line, and changing the line refreshes source cargo,
  route and locations. The order and line cannot change after creation.
- Legacy ORM callers that provide only a sales line may still omit a template.
  Legacy batch/route-chooser APIs remain for integrations and preparation
  scripts; Sales Order lines use the same unsaved trip form as Dispatch Lines.
- Sales Order **Trips** smart button opens all linked trips.
- Standalone **Transport Operations > Trips** menu lists and creates trips.
- The original truck is snapshotted when the trip is created, and never
  synchronized back to the sales line. Customer and commercial details are
  read-only links to their source records.
- Access requires the Dispatcher or Transport Manager group (under the
  Odoo 19 Transport Operations privilege). Dispatchers get read-only access to
  Sales orders and lines so the dispatch worklists work without Sales rights.
  Users in Sales must be assigned the transport group separately. No group is
  granted automatic Sales edit permissions.
- Trip visibility is limited to allowed companies. No one is granted
  deletion rights on trips in this batch.
- No migration, backfill, or bulk creation of historical trips.

## Trip Files (19.0.1.13.0)

Confirming a sales order generates one **Trip File** per product/service line,
including lines without a truck or a route template. Sections and notes are
excluded, and quantity does not multiply files. Each file receives a generated
`TF/<year>/<sequence>` reference, the sales order and source sales line, and a
copy of that line's Container Number. Bill of Lading starts blank and can be
entered later. The matching trip is linked automatically when it is created.

Open files from **Transport Operations > Trip Files** or the sales order's
**Trip Files** smart button. Sales users can edit Bill of Lading for the orders
they can access; transport users can work with files in their allowed
companies. The Trip link is visible to transport users. Files cannot be
created or deleted manually through these roles, and their generated identity
and source links cannot be edited.

Reconfirmation reuses files and preserves their references and Bill of Lading.
Cancellation retains files. New lines added to a confirmed order receive files;
duplicating an order creates fresh files only when the copy is confirmed.
Container Number is a snapshot: later changes on the source line do not replace
it. Sales lines referenced by a file cannot be deleted. Installing the feature
does not bulk-create historical files; creating a trip for an older order
creates the missing file for that line only. Confirmation does not create trips.

## Phase 2 / Batch 2: Commercial and Cargo Details

Trips now show two clear tabs in the form, **Commercial** and **Cargo**.

**Read-only Sales source (updates reflect the linked Sales records):**
- Customer and customer email, sales order and sales line, sales unit rate, currency.
- Customer reference, source document and payment terms.
- Sales line description, container number, shipment reference (SRN), file
  name, consignee, weight, size, sales quantity and unit of measure.
- The truck at trip creation stays a historical **snapshot**, not a live
  related field. The original truck is never written back to Sales.

The Sales Order and trip show the selected customer's email as a read-only
value. Changes to that customer's email are reflected on both records.

**Dispatcher-maintained operational details (all optional):**
- Transporter (selected from the name/email catalog maintained by the
  Transport Manager).
- Transport / booking reference.
- Cargo type and actual cargo description.
- Special handling instructions.

**Sales line cargo defaults (19.0.1.13.2):**
optional Cargo Type, Cargo Description and Special Handling Instructions on
each sales order product/service line are visible in the order line list and
line form to Sales users (cargo catalog is read-only for them). Every newly
created trip snapshots these three values from its own source line across
the trip form, Dispatch Lines and ORM create paths; explicit create values win.
Different lines on the same order can carry different cargo. Later line edits
do not update existing trips. Cargo Description fills the trip's Operational Cargo Description; a
blank line description leaves the trip description empty. An archived sales
cargo type refuses creation with a clear error and creates no partial batch.
On upgrade, former order-level cargo values are copied to empty product-line
fields; sections/notes and existing trip snapshots are preserved. New lines
start with empty optional cargo fields.

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

This fallback applies to direct Trip creation and the Trip form's source-line
selection. Non-empty selected-template locations take priority on Save; blank
template locations use entered locations or the product-name fallback. The
fields remain editable on Draft trips. Subsequent product renames never
silently change a saved trip's route.

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
  when the original Sales truck is active, categorized as Truck, and belongs
  to the trip company or is shared. A Fleet driver is included only when
  active and valid; otherwise the assignment is truck-only. This is an actual
  audited assignment, so the current truck is immediately visible in Resources.
- If these defaults are incomplete or invalid, the trip remains Draft with
  manual initial assignment available. Existing Draft trips are not bulk
  changed: **Apply Fleet Defaults** applies a valid truck to a Draft trip
  without assignment history. **Assign Initial Resources** remains available
  only while no assignment history exists. Once assigned (including truck-only),
  use **Replace Resources** to add/change resources, including adding a
  driver. Existing assignments and Fleet drivers are never overwritten;
  changing the Fleet driver does not automatically update saved trips.
- After a truck-only assignment, add the driver through **Replace Resources**
  before marking Ready (Ready requires both a valid truck and a driver).
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

Selecting milestones shows **Open Trip**, **Arrival / Departure**, and
**Record Activity** above the list. Open Trip and Record Activity accept
multiple milestones belonging to the same trip. Arrival / Departure requires
one milestone: it opens Arrival before arrival is recorded, then Departure.
Finished milestones and closed trips retain their workflow restrictions.
Opening a wizard does not record an event; submit it to save the update.

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

## Trip activity and inactivity alerts (19.0.1.17.0)

**Record Activity** on each open trip row or trip form opens a text dialog.
Submit records the description, author and server submission time in the
read-only **Activity History** tab and refreshes the originating view.
The history uses `trip_activity_ids`; Odoo scheduled activities retain their
native `activity_ids` relation (registry compatibility fix in 19.0.1.17.1).
Empty text, cancelled dialogs and rejected operations do not count as activity.
Confirmed milestone arrivals and departures also create activity rows linked
to their audit events. Their historical event time remains visible separately.
Editing other trip fields, chatter, checkpoint skips and timestamp corrections
do not reset the activity clock. Activity history cannot be edited or deleted.

**Transport Operations > Configuration > Settings** configures inactivity
alerts for the active company: enablement, a recipient email and positive
inactivity hours (default **24**). Alerts are disabled until configured and
explicitly enabled. Only **In Progress** trips are monitored. The clock starts
at trip start, or the latest activity submission if later. Draft, Ready,
Completed and Cancelled trips do not trigger alerts.

A scheduled check runs every **15 minutes** and queues one digest per company
with its overdue trips, Sales Orders, customers, trucks, last activity/start
times (UTC), inactivity hours and links. If inactivity continues, that trip is
included again after the configured interval since its previous queued alert.
Fresh activity starts a new inactivity interval. Recording activity and alert
selection serialize on each trip so duplicate checks cannot queue the same
trip twice for one reminder interval. Alert timestamps mean **queued**;
delivery follows Odoo's outgoing mail queue and configured email server.

The upgrade imports previous arrival/departure audit events into activity
history with their original recording times and authors, and derives the last
activity from that history. It does not invent an update at upgrade time or
activate alerts for a company.

## Vehicle Availability assignment (19.0.1.16.0)

**Assign to Trip** opens the initial resource dialog from an active Truck row.
The truck is preselected, the dispatcher chooses a Draft trip without assignment
history, and the driver defaults from Fleet with manual override. Same-company
and shared trucks follow the normal assignment validations and audit history.
An unassigned Draft trip may use its own reserved Sales truck; trucks assigned
or reserved for another trip are refused. Confirmation locks and rechecks the
truck, so an open dialog cannot silently reuse a truck assigned in the meantime.
This shortcut uses the initial assignment workflow; resource changes on an
assigned trip continue through **Replace Resources**. Sales truck values and
trip-creation snapshots are not rewritten by operational assignment.

## Dispatch worklists (19.0.1.14.0)

Under **Transport Operations > Dispatch Lines**, two read-only worklists list the
confirmed sales order lines a dispatcher still has to act on. Both open one row
per sales **line** (trips live on lines, not orders) and default to grouping by
Sales Order.

- **Lines Without Trip** — confirmed product/service lines with no trip.
  Rows whose line has no truck are highlighted in red.
- **Lines Without Truck** — confirmed product/service lines with no truck.
  Draft trips can be created here; operational trucks are assigned from
  Vehicle Availability or the trip resource dialog before Mark Ready.

Search filters **No Trip**, **Has Trip**, **No Truck** and **Has Truck** combine
with the menu domain, and rows can be grouped by Sales Order or Customer.
Cancelled trips still count as a trip record, because a line may only ever have
one trip.

**Dispatch cutoff (19.0.1.15.0).** Transport Operations > Configuration >
Settings sets a **Dispatch Cutoff Date**. Both worklists then hide every sales
order whose **Order Date** is earlier than that date, whatever search filters
are applied. Leave the field empty to show every confirmed order. The cutoff
only affects these two worklists: trips, Sales documents, the Create Trips
wizard and reporting are unchanged. The cutoff is database-wide, not
per company.

Both worklist menus run a server action that rebuilds the window action on every
click (19.0.1.17.8). A change therefore takes effect on the next click, without
a reload or an upgrade. Do not point those menus back at the window action: the
web client caches `/web/action/load` responses on disk, which keeps serving the
cutoff the action was first loaded with until the module is upgraded or the
browser site data is cleared.

## Sales Order transport schedule (19.0.1.17.6)

Sales Orders have optional **Planned Departure** and **Planned Arrival**
datetime fields next to the Client Reference. They are optional and may be
left empty. A planned arrival earlier than the planned departure is refused on
the Sales Order.

Every trip created from the order inherits both values. This covers the Sales
line **Create Trip** button, the **Create Trips** wizard and direct ORM/API
creation. An explicit trip value wins, including an explicit blank, and each
trip keeps its creation snapshot: later changes on the Sales Order do not move
existing trips, and the trip never writes back to Sales. Trips created from an
order without a schedule keep both fields empty; the existing Ready check still
requires a planned departure and arrival before a trip can start.

## Scope intentionally deferred

Resource overlap/availability enforcement **across different trips**,
incidents, Navirec geofencing/monitoring inside trips, automated state
transitions (including GPS- or event-driven checkpoint detection), reopening
closed trips and financial analytics are **not** implemented. Route templates
and manual trip milestones were delivered in Phase 3; the `gps` event source
value exists in the audit model as a reserved placeholder only.

## Installation and testing

Odoo.sh upgrades the installed TAJ Transport Operations module when a deployed
commit increases its manifest version. For installations outside Odoo.sh,
upgrade the module through Apps or the Odoo CLI. Do not reinstall it.
The manifest version is `19.0.1.17.9`
(automatic Trip Files, product-linked route templates for Sales trip creation,
sales cargo snapshots, Sales Order planned schedule, historical truck grouping,
the dispatch worklists and their cutoff date; automatic truck assignment from
Sales line without requiring a Fleet driver;
Navirec compatibility:
`fleet_navirec` version `19.0.1.4.1`). The `19.0.1.8.0` upgrade migrates saved
trip transporter selections from Contacts into the dedicated catalog.

**Milestone trip shortcut (19.0.1.15.1):** every row in the global milestone
lists has an **Open Trip** button that opens its linked trip form, including
Draft and Completed / Skipped milestones.

**Milestone save navigation (19.0.1.13.2):** saving Arrival, Departure, Skip
or a timestamp correction closes the dialog and refreshes the view that
opened it. The global milestone list keeps its filters and grouping; a
wizard opened from the trip stays on that trip rather than navigating anew.

To prepare transactions using existing master data in a neutralized
`19_upgrade` staging database, run the preview in the Odoo.sh shell:

```bash
bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
```

After reviewing the selected customers, source sales lines, route
templates, trucks and drivers, create the records with:

```bash
TAJ_PREP_APPLY=YES bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
```

The default is 10 confirmed orders with one line, trip and automatically linked
Trip File each: 3 Draft, 3 Ready, 2 In Progress and 2 Completed trips. Existing
master data is reused; schedules and checkpoint events are UAT examples. The
script needs 10 free existing vehicles and distinct existing driver contacts. When no
active template is linked to a selected product, it derives origin/destination
using the existing product-name parser and creates a template with Loading
and Delivery endpoint checkpoints. The preview shows these proposed templates;
unclear product names, ambiguous links and incomplete existing templates are
skipped. Other missing required data stops the run. A used reference
prefix is refused; any failure before commit rolls back the preparation.
Use `TAJ_PREP_COUNT` for 10–100 orders and `TAJ_PREP_PREFIX` for a fresh dataset.
The final manifest lists created routes, order/trip/file references, source
records, commercial and shipment data, cargo snapshots and milestone events.
No customers, products or trucks are created. Drivers are reused unless UAT
driver creation is explicitly enabled below. Missing optional cargo
or shipment details are left blank, and Bill of Lading remains empty.
Sources may be existing quotations or confirmed orders, with product or
service lines; Container Number is optional and is never invented. Inventory
procurement is disabled on these UAT orders. The script prints source and
Fleet diagnostics when existing data cannot support the requested preparation.
The preview also shows proposed Fleet setup. Uncategorised vehicles already
referenced by Sales may be categorised as Truck, and empty Fleet Driver fields
may be filled from existing person contacts identified through Fleet/history,
Driver contact jobs/tags or HR Driver work contacts. Existing Fleet drivers,
other vehicle categories and active-trip allocations are preserved. New
pairings are UAT setup, not verified operational assignments. The final manifest
includes original category/driver IDs; Fleet setup rolls back with the rest
of the run on failure. No vehicle models or existing contact roles are changed.
If existing driver contacts are not labelled, pass their actual Contact IDs
with `TAJ_PREP_DRIVER_IDS=101,102,...` (replace these examples with real IDs).

When the staging copy has no driver contacts, explicitly enable creation of
clearly labelled synthetic UAT drivers for the shortfall. Preview first:

```bash
TAJ_PREP_CREATE_UAT_DRIVERS=YES bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
```

Then create the reviewed dataset with both options:

```bash
TAJ_PREP_CREATE_UAT_DRIVERS=YES TAJ_PREP_APPLY=YES bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
```

The new contacts are named `<prefix> UAT Driver 01`, etc., with a test-only job
title and comment. They are created only during apply, with no login, email
or HR employee, and are listed with their real Contact IDs in the manifest.
The default 10-trip dataset creates 10 test drivers when no existing drivers
are available. Contact creation and Fleet setup roll back with the full run.

To add the fuller DAR–LUBUMBASHI route from Excel **Transit - Going Cargo,
row 14**, preview and apply the independent add-on script:

```bash
bash ~/src/user/scripts/taj_transport_add_report_route.sh
TAJ_REPORT_APPLY=YES bash ~/src/user/scripts/taj_transport_add_report_route.sh
```

It uses the prepared dataset's existing product template 3174 and expands its
two endpoint checkpoints into Dar loading site, Tunduma, Nakonde, Kasumbalesa
Zambia, Kasumbalesa DRC, Whiskey and Lubumbashi delivery. Existing endpoint
record IDs are retained. Historical August 2026 dates remain reference notes.
Sample planned arrival offsets are cumulative from the report's departure:
0, 3, 7, 10, 11, 16 and 17 days (0, 72, 168, 240, 264, 384 and 408 hours).
These are inferred from this one reported trip, with day-level precision.
Absolute August dates are not inserted as actual events on new trips. The
dataset generator places planned trip arrival at or after the last checkpoint.
Existing trip milestone snapshots are checked and preserved; new trips get
all seven checkpoints. Repeating the add-on does not duplicate checkpoints.
For another dataset, select existing records with `TAJ_REPORT_COMPANY_ID`,
`TAJ_REPORT_PRODUCT_TMPL_ID` and, if needed, `TAJ_REPORT_ROUTE_ID`.

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

Initialization adopts an existing case-insensitive **Truck** or **Trailer**
category, including the plural **Trucks** / **Trailers** spellings, without
renaming it; it creates the category only when absent. If both spellings already
exist, the category that actually contains vehicles is adopted and the stable
XML ID is rebound to it. Categories with vehicles split across both spellings
stop initialization for review instead of guessing. XML IDs
`fleet_category_truck` and `fleet_category_trailer` remain stable even when
category names change.
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
