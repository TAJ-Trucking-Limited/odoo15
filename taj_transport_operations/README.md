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

## Scope intentionally deferred

Full cargo/commercial editable workflow, transport route planning, trailer,
driver/co-driver assignment periods, assignment change history, operational
status transitions, incidents, milestones, Navirec integration and financial
analytics are **not** implemented in batch 1.

## Installation and testing

Install manually from the Odoo Apps UI (the add-on should first be present
on the server's custom-addons path). No auto_install and no automatic upgrade.

Run Odoo tests on a **staging/development database**, not production, using
the test tag:

```sh
odoo-bin -d "$PGDATABASE" --stop-after-init -u taj_transport_operations \
  --test-enable --test-tags /taj_transport_operations
```

The test suite covers one-to-one uniqueness, source/truck snapshots, no-truck
lines, non-unit quantities, draft rejection, idempotent actions, wizard selection
and invoicing invariance.

## Sales line eligibility

The wizard suggests lines with a truck; this is a *suggestion*, not proof that
the product is a transport service. The operator must review selected lines.
Pricing/fee lines should be removed from the selection. A dedicated transport
product classifier may be introduced after real product-category review.

## Production safety

The module introduces new tables and views, and does not modify or create
transport trips for existing orders on installation. Any installation or data
migration should be approved and tested on staging first.
