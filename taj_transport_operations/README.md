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

## Scope intentionally deferred

Route planning and journey timings, trailer and driver/co-driver assignments,
resource assignment history, operational status transitions, incidents,
milestones, Navirec monitoring inside trips and financial analytics are
**not** implemented in Batch 2.

## Installation and testing

After the new `19_upgrade` commit is deployed to the Odoo.sh development
build, **manually upgrade** the installed TAJ Transport Operations module
in the Odoo Apps UI. Do not reinstall the module and do not upgrade or install
it automatically during Git deployment. The manifest version is
`19.0.1.1.0`.

Run Odoo tests on a **staging/development database**, not production, using
the test tag:

```sh
odoo-bin -d "$PGDATABASE" --stop-after-init -u taj_transport_operations \
  --test-enable --test-tags /taj_transport_operations
```

The test suite covers one-to-one uniqueness, source/truck snapshots, no-truck
lines, non-unit quantities, draft rejection, idempotent actions, wizard selection,
related Sales commercial/cargo fields, independent editable operational fields,
tracking configuration, form tabs and invoicing invariance.

For a manual smoke test, open an existing Draft trip; the new Commercial and
Cargo tabs should show its existing Sales data while its optional dispatcher
fields remain blank. Set a transporter, booking reference, cargo type and
special handling instructions; save and reopen. The values should persist and
the Chatter should show field-change tracking. Confirm the originating Sales
Order, sales line and existing invoice remain unchanged.

## Sales line eligibility

The wizard suggests lines with a truck; this is a *suggestion*, not proof that
the product is a transport service. The operator must review selected lines.
Pricing/fee lines should be removed from the selection. A dedicated transport
product classifier may be introduced after real product-category review.

## Production safety

Batch 1 introduced the trip table. Batch 2 adds optional columns and updates
existing views; it does not modify or create any existing sales, purchase or
invoice records and does not backfill historical trips. Any installation,
upgrade or future data migration should be approved and tested on development
or staging before production.
