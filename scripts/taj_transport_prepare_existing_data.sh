#!/usr/bin/env bash
# Run in the Odoo.sh shell. Default: preview only.
# bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
# TAJ_PREP_APPLY=YES bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
# Optional: TAJ_PREP_COUNT (>=10), TAJ_PREP_COMPANY_ID, TAJ_PREP_PREFIX,
# TAJ_PREP_DATABASE (optional exact database guard; defaults to PGDATABASE).
set -euo pipefail

command -v odoo-bin >/dev/null || { echo 'Run this in the Odoo.sh shell.' >&2; exit 1; }
: "${PGDATABASE:?PGDATABASE must identify the staging database}"

odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
    --shell-interface=python --log-level=error <<'TAJ_EXISTING_DATA_PY'
import json
import os
import re
import time
from collections import Counter
from datetime import timedelta

from odoo import Command, fields


def prepare(shell_env):
    expected = os.environ.get("TAJ_PREP_DATABASE") or os.environ.get("PGDATABASE", "")
    if shell_env.cr.dbname != expected or not expected.startswith("taj-limited-19-upgrade-"):
        raise RuntimeError("Wrong database. TAJ_PREP_DATABASE must identify the exact upgrade staging database.")
    neutralized = shell_env["ir.config_parameter"].sudo().get_param("database.is_neutralized")
    if str(neutralized).lower() not in ("true", "1"):
        raise RuntimeError("This script requires a neutralized staging database.")
    apply = os.environ.get("TAJ_PREP_APPLY") == "YES"
    count = int(os.environ.get("TAJ_PREP_COUNT", "10"))
    if not 10 <= count <= 100:
        raise RuntimeError("TAJ_PREP_COUNT must be between 10 and 100 sales orders.")
    prefix = os.environ.get("TAJ_PREP_PREFIX", "REAL-UAT-" + fields.Date.today().strftime("%Y%m%d"))
    if not re.fullmatch(r"REAL-UAT-[A-Z0-9-]+", prefix):
        raise RuntimeError("TAJ_PREP_PREFIX must start REAL-UAT- and contain A-Z, digits and hyphens.")
    company_id = int(os.environ.get("TAJ_PREP_COMPANY_ID", "0"))
    companies = shell_env["res.company"].sudo()
    company = companies.browse(company_id).exists() if company_id else companies.search([
        ("name", "=", "TAJ Trucking Limited"),
    ])
    if len(company) != 1:
        raise RuntimeError("Set TAJ_PREP_COMPANY_ID to one existing company ID.")
    context = dict(shell_env.context, allowed_company_ids=company.ids,
                   active_test=True, tracking_disable=True, mail_notrack=True,
                   mail_create_nolog=True, mail_create_nosubscribe=True,
                   mail_auto_subscribe_no_notify=True, mail_notify_force_send=False)
    data = company.sudo().with_context(context).with_company(company).env
    module = data["ir.module.module"].search([("name", "=", "taj_transport_operations")])
    version = tuple(int(n) for n in (module.latest_version or "0").split("."))
    if module.state != "installed" or version < (19, 0, 1, 13, 0):
        raise RuntimeError("Install/upgrade TAJ Transport Operations to at least 19.0.1.13.0 first.")
    if data["sale.order"].search_count([("client_order_ref", "=like", prefix + "-%")]):
        raise RuntimeError("This prefix already exists. No existing preparation is changed; choose a new prefix.")

    # Only read existing master data. Source lines supply actual customers,
    # prices, currencies, taxes and shipment details; no invented cargo facts.
    routes = data["taj.transport.route"].search([
        ("active", "=", True), ("company_id", "=", company.id),
        ("product_tmpl_id", "!=", False),
    ])
    routes_by_product = {}
    for route in routes:
        routes_by_product.setdefault(route.product_tmpl_id.id, []).append(route)
    unique_routes = {
        product_id: matches[0] for product_id, matches in routes_by_product.items()
        if len(matches) == 1
        and (matches[0].origin_location or "").strip()
        and (matches[0].destination_location or "").strip()
        and matches[0].line_ids
    }
    if not unique_routes:
        raise RuntimeError(
            "No suitable existing route: need one active template for a product, "
            "with origin, destination and checkpoints. Link/configure an existing "
            "template first; this script will not create or change templates."
        )
    source_lines = data["sale.order.line"].search([
        ("company_id", "=", company.id), ("order_id.state", "in", ["sale", "done"]),
        ("display_type", "in", [False, "product"]), ("is_downpayment", "=", False),
        ("product_id.product_tmpl_id", "in", list(unique_routes)),
        ("product_id.type", "=", "service"), ("product_id.active", "=", True),
        ("product_id.sale_ok", "=", True), ("product_uom_qty", ">", 0),
        ("price_unit", ">", 0), ("container_num", "!=", False),
        ("order_id.partner_id.active", "=", True),
        ("order_id.client_order_ref", "not ilike", "REAL-UAT-"),
        ("order_id.client_order_ref", "not ilike", "UAT-"),
    ], order="id desc", limit=5000)
    sources = []
    seen = set()
    skipped = Counter()
    for line in source_lines:
        order, product = line.order_id, line.product_id
        if (product.name or "").upper().startswith("UAT-") or (order.partner_id.name or "").upper().startswith("UAT-"):
            skipped["previous UAT master data"] += 1
            continue
        if "service_tracking" in product._fields and product.service_tracking != "no":
            skipped["service would generate projects/tasks"] += 1
            continue
        if "service_to_purchase" in product._fields and product.service_to_purchase:
            skipped["service would generate purchase orders"] += 1
            continue
        if any(tax and not tax.active for tax in line.tax_ids):
            skipped["archived sales tax"] += 1
            continue
        if order.cargo_type_id and not order.cargo_type_id.active:
            skipped["archived cargo type"] += 1
            continue
        if not (line.container_num or "").strip():
            continue
        if order.partner_id.company_id and order.partner_id.company_id != company:
            continue
        if not order.pricelist_id.active:
            skipped["archived pricelist"] += 1
            continue
        # Prefer variety without repeatedly cloning the same customer/product.
        key = (order.partner_id.id, product.id)
        if key not in seen:
            seen.add(key)
            sources.append(line)
    if not sources:
        raise RuntimeError(
            "No suitable real confirmed service lines with container numbers and "
            "matching templates. Skipped: " + json.dumps(skipped)
        )

    category = data.ref("taj_transport_operations.fleet_category_truck")
    vehicles = data["fleet.vehicle"].search([
        ("active", "=", True), ("category_id", "=", category.id),
        ("company_id", "in", [False, company.id]),
        ("driver_id", "!=", False), ("driver_id.active", "=", True),
        ("license_plate", "!=", False),
        ("driver_id.company_id", "in", [False, company.id]),
    ], order="license_plate, id")

    def occupied_resources():
        # Check across companies, including Draft reservations. Do not move
        # resources off an existing trip just to satisfy this preparation.
        active = data["taj.transport.trip"].sudo().search([
            ("state", "in", ["draft", "ready", "in_progress"]),
        ])
        assignments = active.mapped("current_assignment_id")
        reserved = active.filtered(lambda trip: not trip.assignment_ids).mapped("initial_vehicle_id")
        return (
            set((assignments.vehicle_id | assignments.trailer_id | reserved).ids),
            set((assignments.driver_id | assignments.co_driver_id | reserved.driver_id).ids),
        )

    busy_vehicles, busy_drivers = occupied_resources()
    trucks = []
    selected_drivers = set()
    for vehicle in vehicles:
        if vehicle.id in busy_vehicles or vehicle.driver_id.id in busy_drivers | selected_drivers:
            continue
        if (vehicle.license_plate or "").upper().startswith("UAT-"):
            continue
        trucks.append(vehicle)
        selected_drivers.add(vehicle.driver_id.id)
        if len(trucks) == count:
            break
    if len(trucks) < count:
        raise RuntimeError(
            "Need %s available existing trucks with distinct active Fleet drivers; found %s. "
            "No trucks/drivers will be created or taken from active trips." % (count, len(trucks))
        )

    states = ("draft", "ready", "in_progress", "completed")
    plan = []
    for index in range(count):
        source = sources[index % len(sources)]
        route = unique_routes[source.product_id.product_tmpl_id.id]
        truck = trucks[index]
        plan.append((source, route, truck, states[index % len(states)]))
    preview = [
        {"number": index, "target_state": state, "source_order": source.order_id.name,
         "source_line_id": source.id, "customer": source.order_id.partner_id.display_name,
         "product": source.product_id.display_name, "quantity": source.product_uom_qty,
         "unit_price": source.price_unit, "currency": source.order_id.currency_id.name,
         "taxes": source.tax_ids.mapped("display_name"), "container": source.container_num,
         "cargo_type": source.order_id.cargo_type_id.display_name or None,
         "cargo_description": source.order_id.cargo_description if "cargo_description" in source.order_id._fields else None,
         "special_handling": source.order_id.special_handling_instructions or None,
         "route": route.name, "origin": route.origin_location,
         "destination": route.destination_location, "truck": truck.license_plate,
         "driver": truck.driver_id.display_name}
        for index, (source, route, truck, state) in enumerate(plan, 1)
    ]
    print(json.dumps({"database": expected, "company": company.display_name,
                      "prefix": prefix, "mode": "APPLY" if apply else "PREVIEW ONLY",
                      "states": dict(Counter(row[3] for row in plan)), "orders": preview},
                     indent=2, ensure_ascii=False))
    print("Real master data and historical prices/container details are reused in NEW UAT transactions.")
    print("Schedules and lifecycle/checkpoint events are generated UAT examples, not real shipment history.")
    print("Bill of Lading stays empty. Missing optional cargo descriptions/handling notes stay empty.")
    if not apply:
        print("No records created. Review, then rerun with TAJ_PREP_APPLY=YES.")
        return None

    data.cr.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", ("taj-real-uat-preparation",))
    if not data.cr.fetchone()[0]:
        raise RuntimeError("Another preparation is running. Try again when it finishes.")
    if data["sale.order"].search_count([("client_order_ref", "=like", prefix + "-%")]):
        raise RuntimeError("This prefix was created by another run; stopping.")

    master_models = ("res.partner", "res.users", "product.template", "product.product",
                     "fleet.vehicle", "taj.transport.route", "taj.transport.route.line",
                     "taj.transport.cargo.type", "taj.transport.transporter")
    master_counts = {
        model: data[model].with_context(active_test=False).search_count([])
        for model in master_models
    }
    source_stamps = {line.id: (line.write_date, line.order_id.write_date) for line in sources}

    results = []
    for index, (source, route, truck, target) in enumerate(plan, 1):
        busy_vehicles, busy_drivers = occupied_resources()
        if truck.id in busy_vehicles or truck.driver_id.id in busy_drivers:
            raise RuntimeError("A selected truck/driver became occupied. Rerun the preview.")
        copied_line = source.copy_data({"vehicle_id": truck.id})[0]
        order = source.order_id.copy({
            "client_order_ref": "%s-%02d" % (prefix, index),
            "origin": "%s; copied from %s line %s" % (prefix, source.order_id.name, source.id),
            "date_order": fields.Datetime.now(),
            "order_line": [Command.create(copied_line)],
        })
        order.action_confirm()  # Trip Files are generated by the normal hook.
        line = order.order_line.filtered(lambda row: row.product_id and row.display_type in (False, "product"))
        if len(line) != 1:
            raise RuntimeError("Expected exactly one copied product line; rolling back.")
        wizard = data["taj.transport.trip.create.wizard"].create({
            "sale_order_id": order.id,
            "selection_ids": [Command.create({"sale_line_id": line.id, "route_id": route.id})],
        })
        wizard.action_create_trips()
        trip = line.transport_trip_ids
        if len(trip) != 1 or not trip.current_assignment_id:
            raise RuntimeError("Expected one trip with its normal Fleet assignment.")
        now = fields.Datetime.now()
        trip.write({
            "planned_departure_at": now + timedelta(hours=1) if target in ("draft", "ready") else now - timedelta(hours=1),
            "planned_arrival_at": now + timedelta(days=2),
            "route_notes": "UAT preparation: " + prefix,
        })
        if target != "draft":
            trip.action_mark_ready()
        if target in ("in_progress", "completed"):
            trip.action_start_trip()
            # Completed trips finish all checkpoints. In Progress trips finish
            # only the first of at least two, leaving remaining milestones.
            checkpoints = trip.milestone_ids.sorted(lambda row: (row.sequence, row.id))
            selected = checkpoints if target == "completed" else (checkpoints[:1] if len(checkpoints) > 1 else checkpoints[:0])
            for checkpoint in selected:
                for action in ("arrival", "departure"):
                    data["taj.transport.milestone.action.wizard"].create({
                        "milestone_id": checkpoint.id, "action_type": action,
                        "event_at": fields.Datetime.now(), "note": "UAT event: " + prefix,
                    }).action_confirm()
        if target == "completed":
            # Odoo timestamps have second resolution and arrival must be
            # strictly later than departure; never bypass lifecycle guards.
            if fields.Datetime.now() <= trip.actual_departure_at:
                time.sleep(1.1)
            trip.action_complete_trip()
        data.flush_all()
        trip_file = data["taj.transport.trip.file"].search([("sale_line_id", "=", line.id)])
        if (trip.state != target or len(trip_file) != 1 or trip_file.trip_id != trip
                or trip_file.sale_order_id != order or trip_file.container_number != line.container_num
                or trip_file.bill_of_lading or order.invoice_ids):
            raise RuntimeError("Prepared records failed consistency checks; rolling back the entire run.")
        if trip.origin_location != route.origin_location or trip.destination_location != route.destination_location:
            raise RuntimeError("Trip locations do not match the selected template.")
        if (order.partner_id != source.order_id.partner_id or line.product_id != source.product_id
                or line.vehicle_id != truck or line.container_num != source.container_num
                or line.product_uom_qty != source.product_uom_qty
                or line.price_unit != source.price_unit
                or set(line.tax_ids.ids) != set(source.tax_ids.ids)
                or order.currency_id != source.order_id.currency_id):
            raise RuntimeError("Copied commercial/shipment data does not match its real source.")
        results.append({"order": order.name, "order_id": order.id,
                        "customer": order.partner_id.display_name,
                        "trip": trip.name, "trip_id": trip.id, "state": trip.state,
                        "file": trip_file.name, "file_id": trip_file.id,
                        "truck": truck.license_plate, "route": route.name,
                        "container": trip_file.container_number,
                        "milestones_done": trip.milestone_done_count,
                        "milestones_open": trip.milestone_open_count})
    for model, before in master_counts.items():
        if data[model].with_context(active_test=False).search_count([]) != before:
            raise RuntimeError("A workflow changed master record counts for %s; rolling back." % model)
    for source in sources:
        source.invalidate_recordset(["write_date"])
        source.order_id.invalidate_recordset(["write_date"])
        if (source.write_date, source.order_id.write_date) != source_stamps[source.id]:
            raise RuntimeError("An original sales record changed; rolling back.")
    return results


try:
    result = prepare(env)
    if result is None:
        env.cr.rollback()
    else:
        env.cr.commit()
        print("SAVED %s sales orders, trips and Trip Files. Save this manifest:" % len(result))
        print(json.dumps(result, indent=2, ensure_ascii=False))
except BaseException:
    env.cr.rollback()
    print("FAILED: no preparation records from this run were committed.")
    raise
TAJ_EXISTING_DATA_PY
