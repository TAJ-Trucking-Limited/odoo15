"""Read-only Phase 2 integrity check for an Odoo.sh development/staging DB.

Execute after manually upgrading TAJ Transport Operations on Staging:

    odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
        < ~/src/user/scripts/taj_transport_phase2_staging_gate.py

Run through the Odoo shell, which supplies the environment variable env.
This performs no write, create, unlink, install, upgrade, commit or API request.
The Odoo TransactionCase suite separately exercises lifecycle actions.
"""
from collections import Counter

from odoo import fields


def check_transport_integrity(environment):
    Trip = environment["taj.transport.trip"].sudo()
    Assignment = environment["taj.transport.assignment"].sudo()
    category_truck = environment.ref("taj_transport_operations.fleet_category_truck")
    category_trailer = environment.ref("taj_transport_operations.fleet_category_trailer")
    now = fields.Datetime.now()
    failures, warnings = [], []

    def record(collection, kind, trip, message):
        collection.append((kind, trip.id if trip else None, message))

    trips = Trip.search([], order="id")
    assignments = Assignment.search([], order="id")
    print("=== TAJ Phase 2 Staging Integrity Gate ===")
    print("Trips: %s; Resource assignments: %s" % (len(trips), len(assignments)))
    counts = Counter(trips.mapped("state"))
    for state in ("draft", "ready", "in_progress", "completed", "cancelled"):
        print("  %-12s %s" % (state, counts[state]))

    history_by_trip = {}
    for assignment in assignments:
        history_by_trip.setdefault(assignment.trip_id.id, []).append(assignment)

    lines_seen = {}
    for trip in trips:
        line = trip.sale_line_id
        order = trip.sale_order_id
        if not line or not order or line.order_id != order:
            record(failures, "sale_link", trip, "Trip source does not match Sales Order.")
        elif trip.company_id != order.company_id:
            record(failures, "sale_company", trip, "Trip and Sales company mismatch.")
        if line and line.id in lines_seen:
            record(failures, "duplicate_sale_line", trip, "Two trips share one Sales line.")
        if line:
            lines_seen[line.id] = trip.id

        history = sorted(
            history_by_trip.get(trip.id, []),
            key=lambda item: (item.effective_from, item.id),
        )
        open_rows = [item for item in history if not item.effective_to]
        if len(open_rows) > 1:
            record(failures, "multiple_open", trip, "More than one active resource row.")

        for assignment in history:
            if assignment.effective_to and assignment.effective_to < assignment.effective_from:
                record(failures, "negative_period", trip, "Assignment ends before it starts.")
            for kind, vehicle, category in (
                ("truck", assignment.vehicle_id, category_truck),
                ("trailer", assignment.trailer_id, category_trailer),
            ):
                if vehicle and vehicle.category_id != category:
                    record(failures, "resource_category", trip, "%s category mismatch." % kind)
                if vehicle and vehicle.company_id and vehicle.company_id != trip.company_id:
                    record(failures, "resource_company", trip, "%s company mismatch." % kind)
        for previous, following in zip(history, history[1:]):
            if not previous.effective_to:
                record(failures, "history_order", trip, "Open row precedes another assignment.")
            elif previous.effective_to > following.effective_from:
                record(failures, "overlap", trip, "Resource history periods overlap.")

        if trip.state in ("ready", "in_progress"):
            if len(open_rows) != 1:
                record(failures, "missing_active", trip, "Active trip needs one current resource assignment.")
            else:
                current = open_rows[0]
                if not current.vehicle_id or not current.driver_id:
                    record(failures, "required_resources", trip, "Missing primary truck or driver.")
                if current.vehicle_id and not current.vehicle_id.active:
                    record(failures, "archived_truck", trip, "Current truck is archived.")
                if current.driver_id and not current.driver_id.active:
                    record(failures, "archived_driver", trip, "Primary driver is archived.")
                if current.effective_from > now and trip.state == "in_progress":
                    record(failures, "future_assignment", trip, "Started trip has future assignment.")
            if not (trip.origin_location or "").strip() or not (trip.destination_location or "").strip():
                record(failures, "missing_route", trip, "Trip route is incomplete.")
            if not trip.planned_departure_at or not trip.planned_arrival_at:
                record(failures, "missing_schedule", trip, "Planned departure/arrival missing.")
            if order and order.state not in ("sale", "done"):
                record(warnings, "source_order_state", trip, "Source order is no longer confirmed.")
        if trip.state in ("completed", "cancelled") and open_rows:
            record(failures, "terminal_open", trip, "Terminal trip still has an active assignment.")
        if trip.state == "completed":
            if not trip.completed_at or not trip.actual_departure_at or not trip.actual_arrival_at:
                record(failures, "completion_audit", trip, "Missing actual/completion timestamps.")
            elif trip.actual_departure_at >= trip.actual_arrival_at:
                record(failures, "arrival_chronology", trip, "Actual arrival is not after departure.")
        if trip.state == "cancelled" and (
            not trip.cancelled_at or not trip.cancelled_by_id
            or not (trip.cancellation_reason or "").strip()
        ):
            record(failures, "cancellation_audit", trip, "Missing cancellation author/time/reason.")
        if trip.initial_vehicle_id and trip.initial_vehicle_id.category_id != category_truck:
            record(warnings, "legacy_fleet_category", trip, "Original Sales truck is not categorized Truck.")
        if (trip.cargo_type or "").strip() and not trip.cargo_type_id:
            record(warnings, "cargo_type_unmapped", trip,
                   "Legacy cargo type has no dropdown category; review post-migration.")
        if (trip.cargo_type_id and trip.cargo_type
                and trip.cargo_type.strip().casefold()
                != trip.cargo_type_id.name.strip().casefold()):
            record(warnings, "cargo_type_mismatch", trip,
                   "Legacy cargo type text differs from selected category.")

    if lines_seen:
        invoice_lines = environment["account.move.line"].sudo().search([
            ("route_id", "in", list(lines_seen)),
            ("move_id.move_type", "=", "out_invoice"),
            ("display_type", "=", "product"),
        ])
        print("Customer invoice lines matching trip Sales lines: %s" % len(invoice_lines))
        for invoice_line in invoice_lines:
            source = invoice_line.route_id
            trip = Trip.browse(lines_seen[source.id])
            # These can legitimately differ if Sales was edited after invoicing:
            # warn instead of assuming Transport mutated Accounting.
            if invoice_line.vehicle_id != source.vehicle_id:
                record(warnings, "invoice_vehicle_drift", trip, "Invoice/Sales truck differs.")
            if invoice_line.container_num != source.container_num:
                record(warnings, "invoice_container_drift", trip, "Invoice/Sales container differs.")

    if not trips:
        record(warnings, "no_trip_data", None, "Create a test trip on Staging for UAT.")
    print("=== RESULT: %s ERROR(S); %s WARNING(S) ===" % (len(failures), len(warnings)))
    for kind, trip_id, details in failures[:25]:
        print("FAIL %-26s trip=%s: %s" % (kind, trip_id, details))
    for kind, trip_id, details in warnings[:25]:
        print("WARN %-26s trip=%s: %s" % (kind, trip_id, details))
    if len(failures) > 25:
        print("... %s additional errors not shown." % (len(failures) - 25))
    if len(warnings) > 25:
        print("... %s additional warnings not shown." % (len(warnings) - 25))
    if failures:
        raise RuntimeError("%s Phase 2 transport data-integrity errors" % len(failures))
    print("PASS: No inconsistent Trip/Assignment/Sales relations found.")
    return True


if "env" not in globals():
    raise RuntimeError("Use Odoo 19 shell; the env variable is required.")
check_transport_integrity(env)
