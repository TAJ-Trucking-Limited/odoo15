# Transport Operations — User Flows

Operator guide for **TAJ Transport Operations**. Happy path from sales order to completed trip, plus Transport Manager configuration.

Trips do **not** change Sales amounts, invoices, or Fleet master data. Commercial pricing stays on the Sales Order.

---

## Roles and menus

| Role | Who | What they do |
|------|-----|--------------|
| **Dispatcher** | Day-to-day operations | Create/fill trips, assign & replace resources, Mark Ready → Start → Complete, record checkpoint arrival/departure, upload POD |
| **Transport Manager** | Everything Dispatcher can, plus | Maintain catalogs (Cargo Types, Transporters, Route Templates, Checkpoint Types), skip checkpoints, correct recorded times, cancel an **In Progress** trip |

Users need the **Transport Operations** privilege (Dispatcher or Transport Manager). Sales users must be given that group separately to use trip buttons.

### Main menus

**Transport Operations**

- **Trips** — list and open trips  
- **Milestones** — all checkpoints, grouped by trip  
- **Vehicle Availability** — Fleet vehicles and Assigned / Busy / Standby  
- **Configuration** (Manager) — Cargo Types, Route Templates, Checkpoint Types, Transporters  

---

## Happy path (one trip)

```
Confirmed Sales Order
        │
        ▼
   Create Trip(s)          →  Draft
        │
        ▼
 Fill Commercial / Cargo / Route & Timing
 Check truck + driver (usually auto-assigned from Fleet)
 Optional: trailer / co-driver via Replace Resources
 Optional: route template → checkpoints on save
        │
        ▼
   Mark Ready              →  Ready
        │
        ▼
   Start Trip              →  In Progress
        │
        ▼
 Record checkpoint Arrival → Departure (as the trip runs)
        │
        ▼
   Complete Trip           →  Completed
        │
        ▼
 Upload Proof of Delivery (anytime, including after complete)
```

### 1. Create the trip from Sales

1. Open a **confirmed** Sales Order with product/service lines.
2. Click **Create Trips**.
3. Review the suggested lines (truck-assigned lines are pre-suggested).  
   Remove fee/non-transport lines. Add other transport lines if needed.
4. Optional: choose a **Route Template**. Each new trip gets its checkpoints (see step 4).
5. Click **Create / Open Trips**.

Each selected line gets **one** trip (`TRIP/<year>/<sequence>`), starting in **Draft**. A line that already has a trip reuses it.

If the Sales truck is an active **Truck** with an active Fleet driver, and both belong to the trip's company or are shared, the trip is created with that truck and driver already assigned.

**Alternatives**

- On a line: **Trip** — opens the existing trip or creates one for that line.  
- On the order: **Trips** smart button — all linked trips.  
- **Transport Operations > Trips** — create/open trips directly (still needs a confirmed sales line).

If no line has a vehicle, the wizard shows a yellow warning. You can still create Draft trips or cancel and assign a vehicle on the order first.

### 2. Fill the Draft trip

Open the trip (**Transport Operations > Trips** or the Sales **Trips** button).

Tab labels show how many empty fields remain (e.g. `Cargo (3)`). The count includes read-only Sales fields and optional fields (trailer, co-driver, POD), so a non-zero count is normal. Use header **Fill Empty Fields** to fill only missing editable fields in one dialog.

| Tab | What to do |
|-----|------------|
| **Commercial** | Sales fields are read-only. Set **Transporter** (defaults to `TAJ` when that catalog entry exists) and optional booking/transport reference. |
| **Cargo** | Sales cargo is read-only. Set **Cargo Type**, actual description, special handling. |
| **Route & Timing** | Set **Origin**, **Destination**, planned departure/arrival (arrival after departure). Optional route notes. If origin/destination are empty, **Fill Route from Product** can prefill from the product name (Draft only); choosing a **Route Template** also fills them from the template. |
| **Resources** | See step 3. |
| **Milestones** | Optional; see step 4. |
| **Proof of Delivery** | Optional; usually after delivery. |
| **Lifecycle** | System timestamps — filled by the action buttons. |

### 3. Assign resources

On **Resources**, check **Current Assignment** first. If the Sales truck met the Fleet condition in step 1, it and its driver are already there. If that is right, there is nothing to do here unless you need a trailer or co-driver (see **Replace later**).

If Current Assignment is empty (Draft only):

1. **Apply Fleet Defaults** — shown when the trip has a Sales truck. Use it after a Fleet user fixes the truck or its driver.  
   Or open **Assign Initial Resources** and choose truck / driver / optional trailer / co-driver.
2. Enter effective time (and optional location, reason, notes). Confirm **Assign Resources**. A future effective time blocks **Start Trip** until that time is reached.

You need at least one resource; Ready later requires an **active Truck** and a **primary driver**. Trailer and co-driver are optional.

**Replace later:** **Replace Resources** (Draft, Ready, or In Progress). Enter a new set, an effective time after the current assignment started and not in the future, and a reason. Change at least one resource. Ready and In Progress trips must keep a truck and primary driver. Use this to add a trailer or co-driver to an automatic assignment. History rows are never edited or deleted — the old period closes and a new one opens.

### 4. Optional — checkpoints from a route template

1. Choose a **Route Template** (maintained by the Manager) in the **Create Trips** dialog, or on **Route & Timing** while the trip is Draft.
2. Save. The template's checkpoints are copied to **Milestones** as a snapshot; later template edits do not change this trip.  
   **Generate Checkpoints** appears only if the template produced none (for example, it had no checkpoints when chosen).
3. Add one-off stops with **Add Checkpoint** if needed (any state before Completed / Cancelled).

Planning changes (Draft only):

- Changing the template replaces the generated checkpoints; clearing it removes them. Manually added checkpoints stay.  
- **Regenerate Checkpoints** rebuilds the generated checkpoints from the current template, picking up template edits.  
- After **Mark Ready** the template is locked. Use **Add Checkpoint** for route changes.

Recording (Ready / In Progress only):

- Row **Arrival** → enter time (defaults to now; no future times). The button also shows on Draft trips, but recording only works once the trip is Ready.  
- Row **Departure** → completes the checkpoint.  
- Times must follow the checkpoint order.  
- Open checkpoints do **not** block Ready, Start, or Complete. **Complete Trip** lists them in the chatter.

Managers can **Skip Checkpoint** or **Edit** (correct) recorded times with a reason. Skip is on the checkpoint's own form (open it from **Milestones**), for Pending checkpoints only. Edit also works after Complete, but not on a Cancelled trip.

### 5. Mark Ready → Start → Complete

Use the header buttons (state is not free-typed).

| Action | From | Needs |
|--------|------|--------|
| **Mark Ready** | Draft | Confirmed Sales Order; origin & destination; planned departure & arrival (arrival after departure); active assignment with Truck + primary driver |
| **Start Trip** | Ready | Same planning/resource checks; assignment already effective; actual arrival empty. Stamps started time; fills actual departure if empty |
| **Complete Trip** | In Progress | Actual departure set; arrival after departure, not in the future. Stamps completed time; fills actual arrival with now if empty; closes the active resource assignment |

After **Completed**, Current Assignment is empty; **Last Assigned Resources** and full history remain. Operational commercial/cargo/route fields lock; POD can still be updated.

### 6. Proof of Delivery

On **Proof of Delivery**, upload the document/image and optional delivery notes. Allowed after completion. Never blocks Complete.

### Cancel (side path)

- **Draft** or **Ready**: Dispatcher or Manager — **Cancel Trip** + mandatory reason.  
- **In Progress**: **Transport Manager** only.  
- **Completed** / **Cancelled**: terminal — no reopen in this module.

---

## Supporting flows (short)

### Vehicle Availability

**Transport Operations > Vehicle Availability**

Read-only list of all Fleet vehicles:

- **Assigned** — reserved on a Draft/Ready trip (a Draft trip with no assignment reserves its Sales truck)  
- **Busy** — on an In Progress trip  
- **Standby** — free  

Busy wins if both apply. Does **not** block double-booking; it is visibility only. Use **Active Trips** / **Trips** on a vehicle to open related trips.

### Milestones menu

**Transport Operations > Milestones** — same checkpoints as the trip tab, default grouped by trip. Shows linked Sales Order and Customer for search/filter.

### Fill Empty Fields

Header **Fill Empty Fields**:

- Edits only missing Commercial / Cargo / Route & Timing / POD fields (Route Template in Draft only).  
- Does not overwrite existing values.  
- Resources / Milestones / Sales gaps point you to the normal wizards or Sales Order.  
- Closed trips: only missing POD fields.

---

## Transport Manager — configuration

All under **Transport Operations > Configuration**. Dispatchers select these values on trips; they do not maintain the catalogs.

### Cargo Types

Create/edit/archive cargo categories used on the trip **Cargo** tab.

- Archiving keeps historic trips.  
- Renaming a type that is already in use is blocked.

### Transporters

Name (required) + optional email. Used on the trip **Transporter** field.

- Cannot delete a transporter still linked to a trip.  
- New trips prefer a transporter named **TAJ** (case-insensitive) when it exists.

### Route Templates

Ordered list of checkpoints for a corridor (name unique per company).

For each checkpoint line: name, optional type, planned offset in **days** and/or **hours** (hours are stored; days are another way to edit the same duration), optional coordinates (for a later phase).

Dispatchers pick the template on a trip; generation copies a snapshot onto that trip.

### Checkpoint Types

Labels such as Arrival at Site, Loading, Border Crossing, etc. (starter list is editable).

A checkpoint can have no type. Renaming a type does not rewrite checkpoints that already stored their own “type at planning” snapshot.

### Manager-only trip actions

| Action | When |
|--------|------|
| **Skip Checkpoint** | Pending checkpoint that should not be recorded, on a Ready / In Progress trip (from the checkpoint form) |
| **Edit** on a milestone | Correct arrival/departure times (reason required; chronology re-checked). Also after Complete; not on Cancelled trips |
| **Cancel Trip** while **In Progress** | Stop an active trip with a reason |

Fleet create/edit (trucks, trailers, categories) stays with **Fleet Officer / Administrator**. Transport roles get read access to vehicles for assignment; they do not get Fleet edit rights from this module.

---

## Lifecycle at a glance

| State | Meaning |
|-------|---------|
| **Draft** | Planning: fill route, cargo, check resources, optional checkpoints |
| **Ready** | Cleared to run; resources reserved (Assigned); route template locked |
| **In Progress** | Trip started; record checkpoints; resources Busy |
| **Completed** | Done; resources released; POD optional |
| **Cancelled** | Stopped with reason; resources released |

```
Draft ──► Ready ──► In Progress ──► Completed
  │         │            │
  └─────────┴────────────┴──► Cancelled
```

---

For acceptance testing scripts, see `docs/TRANSPORT_FULL_UAT.md` and related UAT docs.
