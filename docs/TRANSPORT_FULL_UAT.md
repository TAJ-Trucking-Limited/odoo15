# TAJ Transport Operations: full manual UAT

Target: `19_upgrade`, commit `d34fd2b`, Transport Operations `19.0.1.11.0`.
Report integration target: Navirec Fleet Integration `19.0.1.4.0` or a later
compatible release. Prepared from the current code; the cases below are **not
executed or passed** merely because they appear in this guide.

This is a browser-based acceptance run for Transport Operations and its trip
columns in Client Tracking Reports. It does not require an Odoo test suite,
shell commands, or automated scripts. Full Navirec API, VAT and accounting
module acceptance are separate; their boundaries with trips are checked here.

## 1. Prepare the staging run

For fixtures that match these cases, use the
[ready-to-use UAT data pack](TRANSPORT_UAT_DATA.md). Its setup script has a
read-only preview followed by an explicit create mode. Loading data does not
perform or pass the manual UAT cases.

1. Use the staging database, not Production. Confirm the Odoo.sh build includes
   the target commit or a later commit containing it.
2. Confirm a staging backup is available. Keep existing operational data out of
   this exercise; prefix new master records and sales references with
   `UAT-<date>-`.
3. In Apps, manually upgrade **TAJ Transport Operations** to `19.0.1.11.0`.
   Upgrade **TAJ Navirec Fleet Integration** to `19.0.1.4.0` for the report cases
   if needed. Refresh the browser after the upgrades. Do not reinstall modules.
4. Use one normal **Dispatcher** account and one normal **Transport Manager**
   account, with Sales access to the test orders. Use separate browser profiles
   or a private window. Administrator/superuser access alone cannot establish
   that Dispatcher restrictions work.
5. Use an administrator/Fleet user to prepare vehicles and a Fleet Manager for
   tracking subscriptions. Transport roles themselves grant Fleet read access,
   not general Fleet editing rights. Record any extra roles on the test users.
6. Record your user timezone. Enter event times in the timezone shown in the
   browser; do not mix browser-local timestamps with UTC shell output.
7. For report delivery, use only a tester-owned mailbox or the staging mail
   capture. Create an inactive UAT subscription; do not enable real client
   schedules or use real client recipients for these cases.

Record each case as **PASS**, **FAIL**, **BLOCKED**, or **N/A with a reason**.
A missing permission, fixture, or second company means Blocked/N/A; it is not
a pass. Save the trip reference, user, screenshot and error text for failures.
When rejection is expected, check both the error and that the saved record
remains unchanged after refreshing.

### Test data

| Item | Setup |
|---|---|
| Customers | Two new contacts: `UAT Customer A` and `UAT Customer B`. |
| Drivers | Three new driver Contacts: `UAT Driver 1`, `2`, and `3`. |
| Truck A | Active Fleet vehicle, module's **Truck** category, default Fleet Driver 1. |
| Truck B | Active Fleet vehicle, **Truck** category, default Fleet Driver 2. |
| Truck C | Active Fleet vehicle, **Truck** category, no default driver initially. Use for auxiliary cases. |
| Trailer A/B | Two active Fleet vehicles in the module's **Trailer** category. |
| Company | Put resources in the test order's company, or leave company empty where sharing is intended. |
| Transporters | Keep the existing transporter named exactly `TAJ`; create `UAT Carrier` with an optional email. Avoid creating a second `TAJ`. |
| Catalogs | One new cargo type and one checkpoint type with the UAT prefix. |
| Route A | `UAT Route A`, correct company, origin and destination, three checkpoint rows below. |
| Route B | A second UAT route with different checkpoints, for switching/clearing cases. |
| Product | A service product with a simple route name such as `UAT Origin - UAT Destination`; use a suitable order-based invoicing policy for draft-invoice checks. |
| POD | A small PDF or image and a second replacement file. |

Route A checkpoint rows:

| Sequence | Name | Offset |
|---|---|---|
| 10 | UAT Loading | 0 hours |
| 20 | UAT Border | 12 hours / 0.5 days |
| 30 | UAT Delivery | 24 hours / 1 day |

Create separate confirmed sales orders/references for these scenarios. Record
the actual generated trip reference beside each label; do not rename trip IDs.

| Scenario label | Purpose |
|---|---|
| MAIN | Customer A, one transport line with Truck A, quantity 3, ordinary test price/taxes/payment term. Full successful journey, replacement to Truck B, then completion. |
| PLAN | Template switches, regeneration and stale resource dialogs. Use Truck C if resources are needed; do not share MAIN's vehicles. |
| NO-VEHICLE | At least two product lines with no vehicle. Warning; no line can be selected. |
| MIXED | One product line with Truck C and one without a vehicle. Only the trucked line is offered. |
| CANCEL-D / CANCEL-R / CANCEL-P | Separate trips to cancel in Draft, Ready and In Progress. Use auxiliary resources sequentially. |
| REPORT-B | Customer B's trip, only when needed for report isolation. |

Before asserting a vehicle is Standby, make sure none of the other UAT trips
reserves it. Finish/cancel auxiliary trips before reusing their vehicles.

### A consistent timeline for MAIN

Capture one reference time **T0** in your local timezone. For an accelerated
UAT run use these actual times, all already in the past:

| Value | Time |
|---|---|
| Planned departure / arrival | T0 minus 2 hours / T0 plus 2 days |
| Initial assignment effective from | T0 minus 60 minutes |
| Trip actual departure | T0 minus 50 minutes |
| Loading arrival / departure | T0 minus 45 / minus 40 minutes |
| Border arrival / departure | T0 minus 30 / minus 25 minutes |
| Resource replacement | T0 minus 20 minutes |
| Delivery arrival / departure | T0 minus 10 / minus 5 minutes |
| Trip actual arrival | Leave empty and let Complete Trip fill it, or enter a valid later past time. |

For a *future-time rejection* always choose a time after the real current
time when executing the case, not after the older T0. Initial assignment,
replacement, departure and arrival must form a valid sequence.

**Empty-field counts are descriptive.** Optional fields and actual times that
have not happened may remain empty. Do not fill a Draft trip's Actual Arrival
just to make its counter zero: Start Trip correctly rejects an existing actual
arrival. Counters do not replace the Ready/Start/Complete validations.

## 2. Navigation, roles and configuration

| ID | Do this | Expected result |
|---|---|---|
| CFG-01 | Open Transport Operations as Dispatcher, then Manager. | Trips, Milestones and Vehicle Availability work; Milestones is next to Trips. Manager sees Configuration with Cargo Types, Route Templates, Checkpoint Types and Transporters. |
| CFG-02 | As Manager create a transporter with name and email; edit it; also create one with no email. Try leaving its name empty. | Name is required; email is optional. Values persist after reopening. |
| CFG-03 | As Dispatcher select a transporter and cargo type on a Draft trip. Try creating/editing catalog options. | Existing options are selectable; catalog maintenance is not granted to Dispatcher. |
| CFG-04 | Create a cargo type, use it on a UAT trip, then try renaming it. Archive it afterwards. | In-use rename is refused. Existing trip retains its classification; archived options are not offered for new selections. |
| CFG-05 | Create a checkpoint type and use it in a generated trip checkpoint; rename the catalog type. | Catalog label changes; the checkpoint's planned type-name snapshot remains historical. |
| CFG-06 | Create Route A and reorder its rows. Save and reopen. Try duplicate checkpoint names on the same template. | Order persists; duplicate checkpoint names on one route are refused. |
| CFG-07 | Enter 2 days, then 36 hours on a UAT template row, saving/reopening each time. | 2 days becomes 48 hours; 36 hours becomes 1.5 days. Both representations survive reload. Zero hours remains a valid departure-time offset. Restore the desired fixture offsets. |
| CFG-08 | Try a duplicate route name in the same company. With a second company, try the same name there. | Same-company duplicate is refused; the name may be reused in a different company. |

## 3. Sales order and trip creation

| ID | Do this | Expected result |
|---|---|---|
| CRT-01 | Open a quotation before confirmation. Confirm it and inspect trips before pressing any trip button. | Header Create Trips is unavailable before confirmation. Confirmation alone does not create trips. |
| CRT-02 | On NO-VEHICLE click Create Trips. | Dialog opens with the yellow **No truck assigned** warning. No lines are suggested or selectable. |
| CRT-03 | Try to create a trip for a NO-VEHICLE line. | The line is not offered in the dialog, and a direct/API create is refused with "Assign a truck on sales line ... before creating a transport trip." |
| CRT-04 | On MIXED click Create Trips. | No warning. Truck-assigned lines without trips are suggested; the truckless line is not offered. |
| CRT-05 | Create MAIN from its sales line, quantity 3. Check source links, company and original truck. | Exactly one Draft trip for that line, not three. Generated reference, order, customer, company and Truck A snapshot are correct. |
| CRT-06 | Press the sales line Trip button repeatedly. Re-select that same line in Create Trips. | Existing trip is opened/reused; no second trip for the line. Already-linked lines are not suggested as new trips. |
| CRT-07 | Create two trips from two different product lines in one order; include a section/note line in the order. | One trip per selected product line; section/note lines cannot become trips. Sales order Trips count and smart button match. |
| CRT-08 | Test the sales-line Trip action, the header wizard and standalone Trips > New using different unused confirmed lines. | All paths produce linked Draft trips and default to the existing transporter named `TAJ`. |
| CRT-09 | On a new Draft trip choose UAT Carrier instead of TAJ, then save and reopen. | Explicit transporter selection remains; no background reset to TAJ. Existing trips are not changed by creating a transporter. |
| CRT-10 | Use a simple route-named product and a separate ambiguous/non-route-named product. On a Draft trip try Fill Route from Product with one location already filled. | Simple product suggests origin/destination; ambiguous name is not guessed. Existing manual locations are preserved. |

Cancel auxiliary creation-test trips when done, using their UAT reason.

## 4. Commercial, cargo and timing data

| ID | Do this | Expected result |
|---|---|---|
| DAT-01 | Compare MAIN's customer reference, source, rate, currency, terms, cargo quantity/unit, container and cargo metadata with its sales records. | Values match and are read-only on the trip. The original truck is also read-only. |
| DAT-02 | Fill transporter/reference, cargo type, description and special handling. Save and reopen. | Entries persist; changes that use tracking appear in Chatter with the correct user. |
| DAT-03 | Edit a test source Sales field, then refresh the linked trip. | Related Sales data reflects the source. Trip operational edits do not write back to Sales. |
| DAT-04 | Save planned arrival earlier than planned departure; repeat with actual arrival earlier than actual departure. | Invalid chronology is refused; failed save does not persist partial values. |
| DAT-05 | Set equal planned times on a separate Draft trip, then try Mark Ready after filling resources. | Ready is refused: planned arrival must be strictly later than departure. Correct the times to continue. |

## 5. Route templates and checkpoint planning

Use PLAN for destructive planning variations; keep MAIN on Route A.

| ID | Do this | Expected result |
|---|---|---|
| RTE-01 | Select Route A on a Draft trip and save, or select it in Create Trips. | Exactly three checkpoints appear in route order. Generated From identifies Route A. Empty locations may be suggested; existing manual locations are preserved. |
| RTE-02 | Set planned departure and compare each checkpoint's planned arrival. Change departure and recheck. | Planned arrival is departure plus the checkpoint's offset; changing departure updates the planned times. Zero offset means exactly departure time. |
| RTE-03 | Edit Route A's name/offsets or add a row after the trip has copied it. Refresh the trip without regenerating. | Existing checkpoint snapshots are unchanged. Use a separate template copy if you must preserve MAIN's fixture. |
| RTE-04 | While PLAN is Draft and untouched, add one manual checkpoint, then Regenerate with a reason after a template edit. | Generated checkpoints are refreshed from the template; the manual checkpoint is retained. No duplicates. |
| RTE-05 | On untouched Draft PLAN, switch Route A to B, then clear the template. | Generated rows follow the selected template; clearing removes generated rows/marker and preserves any manual checkpoint. |
| RTE-06 | Archive a UAT template already used by a trip. Inspect the existing trip and try selecting the template on a different trip. | Existing checkpoints remain; archived template is not offered for a new selection. |
| RTE-07 | Mark PLAN Ready and attempt to change/clear its route template or regenerate. | Operational planning is protected; a refusal may appear on save. No old checkpoints/history are removed. A manual checkpoint can still be added on Ready/In Progress trips. |
| RTE-08 | After recorded checkpoint progress, try any available template-changing action. | Recorded events are preserved; template replacement/clearing must not erase operational history. |

## 6. Initial resources and replacements

| ID | Do this | Expected result |
|---|---|---|
| RES-01 | Open MAIN > Resources > Assign Initial Resources. | Original Truck A is suggested; Driver 1 comes from Truck A's Fleet driver. No assignment existed just from creating the Draft trip. |
| RES-02 | Change the truck in the dialog to B, then C. Override the driver manually before saving a suitable assignment. | B suggests Driver 2; C leaves the driver empty. Manual override is accepted. Choosing a driver does not change Fleet's default driver. |
| RES-03 | For MAIN assign Truck A, Trailer A, Driver 1, optional different co-driver, with the example effective time. | One active history row; current resources match. History shows effective time, reason and responsible user. The original Sales truck stays A. |
| RES-04 | Try an entirely empty assignment and the same person as driver and co-driver on an auxiliary trip. Inspect category/company selection. | Invalid resources are refused. Truck/trailer fields offer appropriate active Fleet categories. Wrong-company or archived resources are not valid new assignments. |
| RES-05 | Open Replace Resources and submit without changing resources; then try a blank reason, future effective time, and a time at/before the current period start. | Each attempt is refused and leaves exactly the same active row. |
| RES-06 | On MAIN In Progress, replace Truck A/Trailer A with Truck B/Trailer B and a chosen driver, effective at the example time, with a reason. | Old period closes and one new period opens at the same time. Other retained resources are preserved. Trip stays In Progress. Fleet defaults and Sales truck remain unchanged. |
| RES-07 | On Ready/In Progress try removing the truck or primary driver; try replacement before actual departure. | Refused. A valid truck and primary driver must remain; replacement cannot precede the trip's departure. |
| RES-08 | On PLAN open replacement dialogs in two browser sessions. Save a real change in session A, then submit session B's older dialog. | Session B is refused as stale. Exactly one active assignment remains, with no extra row from the rejected dialog. |
| RES-09 | Inspect history as Dispatcher and Manager. Try editing/deleting rows through the normal UI. | Historical periods cannot be directly changed/deleted. Replacement creates history instead of rewriting it. |
| RES-10 | Change a Fleet default driver on a UAT truck after an assignment, then refresh the trip. | Existing assignment/driver history does not silently change. Restore the Fleet fixture if another case needs it. |

## 7. Ready and Start validations

| ID | Do this | Expected result |
|---|---|---|
| LIF-01 | Attempt Mark Ready on an incomplete Draft trip. Fill missing origin, destination, planned times, truck and driver one at a time. | Refused until all required route/schedule/resource conditions hold. Trailer and co-driver remain optional. |
| LIF-02 | Mark complete MAIN planning Ready. | State becomes Ready; Ready timestamp appears. Resource assignment remains open; vehicles are Assigned. |
| LIF-03 | On an auxiliary trip use a future initial assignment, mark Ready, then try Start. Separately try a future actual departure, a departure before the assignment, or an already-filled actual arrival. | Start rejects each invalid case. Restore valid values or cancel that auxiliary trip. |
| LIF-04 | On MAIN enter the example actual departure and Start Trip. On another suitable test trip, Start with actual departure blank. | Both become In Progress; entered departure is preserved, blank departure is filled with current time, and Started timestamp is recorded. |
| LIF-05 | Refresh or revisit an older browser tab and attempt an obsolete Ready/Start transition. | No duplicate transition, reset timestamp or extra assignment. Stale actions may be hidden or refused. |

Run active-trip Navirec cases in section 13 before completing MAIN, or use a
separate newly prepared In Progress report trip later.

## 8. Arrival, Departure, corrections and audit

| ID | Do this | Expected result |
|---|---|---|
| MST-01 | Open a Draft checkpoint and attempt Arrival. Inspect an active trip's checkpoint buttons. | Recording in Draft is refused. Active workflow uses Arrival and Departure; no separate Confirm/Cancel Confirmation or confirmation-required setting. |
| MST-02 | On MAIN Ready/In Progress, record Loading Arrival with an optional note. | Checkpoint becomes Arrived; arrival/user are stored. It remains open and does not count as done. |
| MST-03 | Record Loading Departure, then Border and Delivery in order using the example timeline. | Each Departure completes that checkpoint. Done count/progress increases; all three departed checkpoints give 100% if no other applicable checkpoints were added. |
| MST-04 | On appropriate live checkpoints try a future timestamp, duplicate Arrival/Departure, Departure without Arrival, Departure before Arrival, and a later checkpoint time before an earlier checkpoint's recorded events. | Each invalid/duplicate event is unavailable or refused, with previous values/history unchanged. |
| MST-05 | Add an extra untouched checkpoint while Ready/In Progress. As Manager skip it with a reason; try blank reason and try skipping a checkpoint with recorded events. | Valid skip is audited and excluded from progress denominator. Missing reason and skipping recorded events are refused. |
| MST-06 | As Dispatcher inspect Edit/Skip actions; as Manager edit a recorded time with a reason. | Dispatcher has no manager actions. Valid manager correction stores old/new values, actor and reason in history/Chatter. Blank reason, future time and invalid chronology are refused. |
| MST-07 | On a separate checkpoint as Manager clear Departure; try clearing Arrival while Departure exists. | Clearing Departure reopens as Arrived and reduces done/progress. Arrival cannot be cleared while Departure remains. Restore valid values if continuing the journey. |
| MST-08 | Inspect checkpoint form, history, notes and trip Chatter. | Operational times/state are read-only outside actions; audit entries remain visible. Notes can be edited on live trips; history cannot be deleted. |
| MST-09 | Open the Milestones menu and trip Milestones smart button; search by Customer and Sales Order. | Grouped by Trip by default. Correct customer/order shown read-only and searchable. |

## 9. Empty-field counters

| ID | Do this | Expected result |
|---|---|---|
| CNT-01 | Open every trip tab and manually count its empty visible data fields. Count each field once within that tab. | Header number matches. Optional and read-only fields count; hidden technical fields do not. |
| CNT-02 | Fill an empty booking reference or cargo description; leave the input so its form value updates, but do not save yet. Clear it and then discard the edits. | Counter decreases, increases on clearing and returns to saved value after discard. Reload also agrees. |
| CNT-03 | Use spaces only in an empty text field. Check a numeric field with value 0. | Spaces remain empty; zero is filled. |
| CNT-04 | On a UAT trip fill both POD file and notes; then clear notes temporarily. | Proof of Delivery shows **(0)** with both filled, **(1)** when notes empty. Zero must not disappear. Restore/save or discard deliberately. |
| CNT-05 | Compare Resources before and after completion; compare Lifecycle across states. | Only the visible current/last resource group counts. Future lifecycle timestamps and hidden cancellation fields do not count. A Draft Lifecycle tab can legitimately show (0). |
| CNT-06 | Compare a trip with an empty milestone/history list with one containing records. | Empty list counts as one field, regardless of number of potential rows/cells. Blank cells inside existing rows do not add to the tab count. |

## 10. Fill Empty Fields wizard

Use a saved trip with some blank and some populated values. Keep Actual Arrival
blank before starting the trip; the wizard does not make future actual events
mandatory. A different trip can be used for each negative case.

| ID | Do this | Expected result |
|---|---|---|
| WIZ-01 | Open Fill Empty Fields on a saved active trip. Compare fields with its tabs. | Only missing editable trip fields appear, grouped as Commercial, Cargo, Route & Timing and POD. Populated values are absent. Missing read-only fields are listed separately. |
| WIZ-02 | Fill several visible fields; watch the dialog, then Save. | Inputs stay visible while typing. Supplied values persist on the trip and counters update after reopening. Unfilled entries are left blank; no existing data is cleared. |
| WIZ-03 | Enter values, then Cancel instead of saving. | Trip remains unchanged; temporary dialog entries do not become trip data. |
| WIZ-04 | Submit invalid dates or another value the normal trip form would reject. | The same validation applies and no partial trip update is saved. |
| WIZ-05 | On a Draft trip with blank route/locations select a template. Also try with one original trip location already populated. | Only offered blank locations receive suggestions. Existing locations never get overwritten. Saving a valid template generates checkpoints through the existing workflow. |
| WIZ-06 | Inspect the empty Sales field list, enter an editable trip value, then Save & Open Sales Order. | Trip entry is saved first. Linked order opens. Sales fields are edited only there with normal Sales permissions; refreshing the trip shows the source change. |
| WIZ-07 | When missing resources are listed, use Save & Assign Resources or Save & Replace Resources; when no checkpoints exist, use Save & Add Checkpoint. | Trip entries save first; the existing controlled wizard opens. Assignment/checkpoint validations and audit history still apply. |
| WIZ-08 | Open the wizard in session A with an empty reference. In B fill/save that reference directly. In A enter a different reference and Save. | A is refused as stale; B's value is preserved. Reopening A removes the now-filled field. |
| WIZ-09 | Open A with missing operational fields. In B complete/cancel that trip. Submit an operational value from A. | Rejected; closed-trip protection cannot be bypassed by the older dialog. |
| WIZ-10 | On a Completed/Cancelled trip open a fresh wizard. Add missing POD file/notes, then reopen. | Only missing POD is editable; operational fields stay locked. Populated POD values are not offered for replacement in this fill-only wizard. Normal POD tab can still edit/replace them. |
| WIZ-11 | Fill all allowed editable fields on an auxiliary trip for which that makes sense and reopen the wizard. | Clear no-editable-empty-fields message; no populated inputs offered. Remaining read-only gaps may still be listed. |

## 11. Completion, cancellation and delivery documents

| ID | Do this | Expected result |
|---|---|---|
| END-01 | On MAIN try an actual arrival equal to departure, in the future, or before the current assignment. | Save or completion rejects the invalid timeline; no resource release or partial completion. Restore valid values. |
| END-02 | Complete MAIN with actual arrival blank. | State Completed; actual arrival and Completed timestamp recorded. Final assignment closes. Last resources/history remain visible; original Sales truck is still A. |
| END-03 | On another trip complete with a valid supplied actual arrival; leave one checkpoint open and POD empty. | Supplied arrival is preserved. Open checkpoints and missing POD do not block completion; open checkpoints are reported in Chatter. |
| END-04 | After completion try operational field edits, resource replacement and new checkpoint Arrival/Departure. | Refused/readonly. No reopening or cancellation of the completed trip. Manager may still perform a valid reasoned correction of an existing checkpoint timestamp, respecting the trip end. |
| END-05 | Upload POD, add notes, save/reopen/download; replace the file and edit notes after completion. | Correct filename/content persist and download. POD and notes remain optional; document maintenance does not unlock operational fields. |
| END-06 | Cancel separate Draft and Ready trips as Dispatcher, first with blank reason, then with a UAT reason. | Blank reason refused; valid cancellation records reason/user/time, closes any active assignment and releases reservations. Sales order/invoices remain intact. |
| END-07 | On an In Progress trip compare Dispatcher and Manager cancellation. | Dispatcher cannot cancel it; Manager can with reason. No new operational events or checkpoint corrections on Cancelled trips. No reopen. POD remains editable. |

## 12. Vehicle Availability

Use vehicles with no other reservations when checking a Standby transition.

| ID | Do this | Expected result |
|---|---|---|
| AVL-01 | Open Vehicle Availability, search trucks/trailers and another visible Fleet category. Use status/category grouping and Archived filter. | View shows allowed Fleet vehicles, not only trailers. It is read-only; vehicles are maintained in Fleet. Search, filters/grouping and archived visibility work. |
| AVL-02 | With Truck A initially free, create a Draft trip from a Sales line with Truck A, without assigning resources. | Truck A immediately becomes Assigned and the Draft trip appears in accessible Active Trips. No resource history is fabricated. |
| AVL-03 | In that Draft trip assign a different free truck B and trailer. | Original A reservation is released; actual B/trailer become Assigned. Ready remains Assigned; Start becomes Busy. |
| AVL-04 | Replace an In Progress truck/trailer, then complete/cancel the trip. | Previous vehicles become Standby if unused elsewhere; replacements are Busy, then released when the trip ends. |
| AVL-05 | On dedicated auxiliary vehicles only, leave one Draft reservation and one In Progress assignment, then close the running trip. | Busy takes precedence. Vehicle falls to Assigned while the Draft reservation remains, and to Standby only after its final reservation ends. Clean up both auxiliary trips. |
| AVL-06 | Follow Active Trips/Trips links. With a shared-company vehicle, compare a user who cannot access its other company's trip. | Linked details contain only readable trips. Shared vehicle occupancy is still reflected in status; hidden-company occupancy must not falsely show Standby. |

**Known gap: no prevention of double booking across trips.** The current version
can allow the same vehicle on multiple In Progress trips. Availability does not
enforce uniqueness. If preventing that is a business acceptance requirement,
record it as an unresolved requirement; do not mark the requirement passed.
Any deliberate conflict test must use dedicated UAT trips/vehicles and end with
Manager cancellation of the extra trips, preserving the chosen active one.

## 13. Navirec Client Tracking Report trip integration

Perform the active cases while MAIN or a fresh report trip is In Progress.
Unmapped UAT vehicles are sufficient to check trip columns: their telemetry may
correctly say Not Mapped/Not available. This does not establish live GPS accuracy.

| ID | Do this | Expected result |
|---|---|---|
| RPT-01 | As Fleet Manager create an inactive subscription for Customer A, explicitly select its UAT vehicle, and inspect Report Fields. | Trip, Customer and Sales Order options exist and are off by default. Other required report settings remain valid. |
| RPT-02 | Enable the three fields. Preview and Download XLSX while the matching vehicle has one In Progress Customer A trip. | Correct trip reference, Customer A and sales order appear in both. Existing telemetry/status/GPS fields remain present as configured. |
| RPT-03 | Check an unassigned vehicle, a Draft/Ready trip and then a Completed/Cancelled trip. Also select a vehicle whose only active trip belongs to Customer B. | New trip/customer/order columns are blank when no matching In Progress trip exists. No Customer B identity/order leaks into A's columns. Vehicle selection itself remains explicit; this feature does not automatically filter telemetry rows by trip customer. |
| RPT-04 | Replace the truck on an In Progress trip and preview with both vehicles selected. | Current truck supplies the trip data; the former truck does not retain an obsolete trip association unless another matching active assignment exists. A tracked assigned trailer can also resolve its trip. |
| RPT-05 | If deliberately testing conflicts, use only dedicated UAT records: assign a tracked vehicle to multiple In Progress trips, including one for Customer A, within the selected companies. | Enabled new columns show **Multiple active trips**, not an arbitrarily selected trip or another client's identifiers. Cancel the duplicate UAT trip(s) afterwards. |
| RPT-06 | With only tester recipients/staging mail capture, optionally use Send Now. Separately exercise one scheduled send if your staging mail setup permits it. | Preview, email body and XLSX agree on column definitions and relevant trip data. Delivery status/error is truthful. If delivery cannot safely be exercised, mark this case Blocked/N/A rather than Passed. |

## 14. Access, company boundaries and source-data regression

| ID | Do this | Expected result |
|---|---|---|
| SEC-01 | Compare normal Dispatcher and Manager accounts for catalogs, milestone corrections/skips, In Progress cancellation and wizard access. | Dispatcher has normal trip operations and Fill Empty Fields; configuration maintenance and manager actions remain restricted. |
| SEC-02 | With a second company, create that company's UAT trip/template/resources. Select only company A as a user restricted to A. Try lists, searches and a copied direct trip URL. | B's trip/template/checkpoint data cannot be read or edited. Wrong-company templates/resources cannot be assigned to A's trip. |
| SEC-03 | On that restricted account, try the fill wizard and a copied POD attachment link for B's trip. | Access denied; the wizard and attachment do not bypass trip/company permissions. |
| SEC-04 | Use a normal internal user without Transport roles. Compare with a Dispatcher who has no Fleet editing role. | No transport privilege is granted accidentally. Dispatcher can inspect/select Fleet resources, not maintain Fleet master records just because of Transport access. |
| SRC-01 | Before MAIN replacement/completion, capture its Sales line truck, customer, quantities, rate, taxes and terms; create a draft invoice using the normal Sales workflow. Compare after replacement/completion/cancellation cases. | No trip operation changes Sales/invoice amounts, taxes, terms, customer or original truck. No cancellation/refund/journal entry is created by cancelling a trip. |
| SRC-02 | On a separate uninvoiced UAT order, complete a trip after replacing its original truck, then create a draft invoice. | Invoice still follows the Sales source and original Sales truck, not the replacement operational truck. No need to post an invoice for this check. |
| SRC-03 | Inspect a few pre-upgrade trips and audit histories. If historical confirmations exist, inspect one before and after a permitted manager timestamp correction. | Original source links, transporters, periods and historical evidence remain. No surprise checkpoint backfill or invented movement timestamps; historical confirmation evidence remains read-only. |

## 15. Record results and close the run

Copy a row for each failed/blocked case; retain a status for every case ID in
your working copy. Attach the screenshots/error text to your UAT notes.

| Case ID | Status | Order / Trip / vehicle | User / company | Actual result / evidence | Retest result |
|---|---|---|---|---|---|
| | Not run | | | | |

For a failure, record the shortest reproduction:

1. Case ID, module version/build, user role, company and timezone.
2. Order/trip/vehicle references and the starting trip state.
3. Exact clicks and entered values, especially timestamps.
4. Expected result versus actual result, complete error text and screenshot.
5. After refreshing: whether any values, assignments or source documents changed.

Close the UAT run as follows:

- Cancel only the remaining active UAT trips, with a clear UAT cleanup reason;
  a Manager must cancel In Progress trips. Leave Completed trips as audit evidence.
- Confirm no auxiliary UAT trip still reserves a vehicle. Remaining legitimate
  active trips must still show the correct Busy/Assigned status.
- Disable UAT report subscriptions/schedules and remove test recipients. Archive
  disposable test master records where appropriate. Keep trip/assignment history.
- Sign off each workflow only after its successful and rejection cases pass.
  A complete run includes role/company checks and source-data preservation.
- Treat data leakage, overwritten concurrent changes, lost history, duplicate
  trips/active assignment rows within one trip, and changed Sales/invoice data as
  release blockers. A normal validation message in a rejection case is a pass;
  an unhandled server traceback, broken form or unexplained partial save is a fail.
- List known gaps separately, particularly cross-trip double booking, no automatic
  GPS checkpoint events and no reopening of completed/cancelled trips. Decide
  explicitly whether any gap blocks your business sign-off.

Suggested execution order: setup/configuration → creation/planning → initial
resources → counters/wizard → Ready/Start → milestones/replacement → active
report checks → completion/cancellation/POD → role/company/source regression →
cleanup and sign-off. Use auxiliary trips for cases that would disrupt MAIN.
