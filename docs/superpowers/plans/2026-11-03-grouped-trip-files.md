# Grouped File Numbers implementation plan

**Goal:** A File Number contains multiple Sales Order lines and their operational trips, with audited shipment entry that cannot change Unit Price.

**Architecture:** Keep `taj.transport.trip.file` and its existing file numbers. A file belongs to one Sales Order and contains dedicated shipment rows linked uniquely to Sales Order lines. New confirmations create one file per order; historical files retain their numbers and original lines. Related shipment fields read live Sales data. A narrow server method writes only the approved shipment fields and preserves Unit Price; a separately assigned Data Entry group authorizes this route without unlocking Sales Orders.

**Constraints:** Preserve the confirmed-order edit lock, company/document access, operational trip identity, existing file numbers and original snapshots. No prices in file tables. No user-forgeable context bypasses. Standard invoiced/delivered product restrictions remain; changing a product with an existing trip is refused. Quantity edits retain Unit Price and allow native totals to recalculate.

1. Replace per-line generation with grouped files and unique linked shipment rows. Test confirmation, multiple trips, added lines, copies, idempotence and legacy records.
2. Add Data Entry permissions and audited, allow-listed source writes. Test live synchronization, fixed Unit Price, forbidden fields, company boundaries and a still-locked Sales Order.
3. Replace the list/form/search with Trips / File Number, optional columns, customer/Sales/trip links and Chatter. Keep operational trips under Transport Trips. Verify combined views and field order.
4. Migrate legacy source IDs, Bill of Lading and container snapshots before stored fields recompute; create linked rows afterward. Test preservation and rerun safety, then run the relevant Odoo regression suites on the disposable local database and request independent review.

Validation runtime: `/Users/hayanjabr/Desktop/taj-odoo19-testenv/venv/bin/python3.11 -m odoo`, source directory `/Users/hayanjabr/Desktop/taj-odoo19-testenv/odoo-19.0.post20261016`, disposable database `codex_taj_sale_columns_20261103`.
