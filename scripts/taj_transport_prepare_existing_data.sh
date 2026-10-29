#!/usr/bin/env bash
# Run in the Odoo.sh shell. Default: preview only.
# bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
# TAJ_PREP_APPLY=YES bash ~/src/user/scripts/taj_transport_prepare_existing_data.sh
# Optional: TAJ_PREP_COUNT (>=10), TAJ_PREP_COMPANY_ID, TAJ_PREP_PREFIX,
# TAJ_PREP_DATABASE (exact database guard; defaults to PGDATABASE),
# TAJ_PREP_DRIVER_IDS (comma-separated existing person Contact IDs),
# TAJ_PREP_CREATE_UAT_DRIVERS=YES (create clearly labeled test drivers if needed).
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
    create_uat_drivers = os.environ.get("TAJ_PREP_CREATE_UAT_DRIVERS") == "YES"
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
                   mail_auto_subscribe_no_notify=True, mail_notify_force_send=False,
                   skip_procurement=True)
    data = company.sudo().with_context(context).with_company(company).env
    module = data["ir.module.module"].search([("name", "=", "taj_transport_operations")])
    version = tuple(int(n) for n in (module.latest_version or "0").split("."))
    if module.state != "installed" or version < (19, 0, 1, 13, 0):
        raise RuntimeError("Install/upgrade TAJ Transport Operations to at least 19.0.1.13.0 first.")
    if data["sale.order"].search_count([("client_order_ref", "=like", prefix + "-%")]):
        raise RuntimeError("This prefix already exists. No existing preparation is changed; choose a new prefix.")

    # Source lines supply actual customers, prices, currencies, taxes and
    # shipment details. Missing templates use the existing product-route parser.
    routes = data["taj.transport.route"].search([
        ("active", "=", True), ("company_id", "=", company.id),
        ("product_tmpl_id", "!=", False),
    ])
    routes_by_product = {}
    for route in routes:
        routes_by_product.setdefault(route.product_tmpl_id.id, []).append(route)
    route_plans = {}
    # ponytail: Inspect the latest 5,000 source lines; raise this limit if the
    # inventory shows suitable route products exist only in older orders.
    source_lines = data["sale.order.line"].search([
        ("company_id", "=", company.id),
        ("order_id.state", "in", ["draft", "sent", "sale", "done"]),
        ("display_type", "in", [False, "product"]), ("product_id", "!=", False),
    ], order="id desc", limit=5000)
    sources = []
    seen = set()
    skipped = Counter()
    for line in source_lines:
        order, product = line.order_id, line.product_id
        if ((product.name or "").upper().startswith("UAT-")
                or (order.partner_id.name or "").upper().startswith("UAT-")
                or (order.client_order_ref or "").upper().startswith(("UAT-", "REAL-UAT-"))):
            skipped["previous UAT master data"] += 1
            continue
        if line.is_downpayment or line.is_expense:
            skipped["down payment or expense line"] += 1
            continue
        if not product.active or not product.sale_ok:
            skipped["archived or non-saleable product"] += 1
            continue
        if product.company_id and product.company_id != company:
            skipped["product belongs to another company"] += 1
            continue
        if line.product_uom_qty <= 0 or line.price_unit < 0:
            skipped["non-positive quantity or negative price"] += 1
            continue
        if not order.partner_id.active:
            skipped["archived customer"] += 1
            continue
        if (product.type == "service" and "service_tracking" in product._fields
                and product.service_tracking != "no"):
            skipped["service would generate projects/tasks"] += 1
            continue
        if product.type == "service" and "service_to_purchase" in product._fields and product.service_to_purchase:
            skipped["service would generate purchase orders"] += 1
            continue
        if any(tax and not tax.active for tax in line.tax_ids):
            skipped["archived sales tax"] += 1
            continue
        if order.cargo_type_id and not order.cargo_type_id.active:
            skipped["archived cargo type"] += 1
            continue
        if order.partner_id.company_id and order.partner_id.company_id != company:
            skipped["customer belongs to another company"] += 1
            continue
        if not order.pricelist_id.active:
            skipped["archived pricelist"] += 1
            continue
        template_id = product.product_tmpl_id.id
        if template_id not in route_plans:
            matches = routes_by_product.get(template_id, [])
            if len(matches) > 1:
                skipped["multiple active templates for product"] += 1
                continue
            if matches:
                route = matches[0]
                if not ((route.origin_location or "").strip()
                        and (route.destination_location or "").strip() and route.line_ids):
                    skipped["existing template missing locations/checkpoints"] += 1
                    continue
                route_plans[template_id] = {
                    "record": route, "name": route.name,
                    "origin": route.origin_location, "destination": route.destination_location,
                }
            else:
                origin, destination = data["taj.transport.trip"]._route_from_sale_line_product(line)
                if not origin or not destination or origin.casefold() == destination.casefold():
                    skipped["product name has no clear origin/destination pair"] += 1
                    continue
                route_plans[template_id] = {
                    "record": None, "name": "%s-ROUTE-%s" % (prefix, template_id),
                    "origin": origin, "destination": destination,
                }
        # Prefer variety without repeatedly cloning the same customer/product.
        key = (order.partner_id.id, product.id)
        if key not in seen:
            seen.add(key)
            sources.append(line)
    print("SOURCE INVENTORY " + json.dumps({
        "company_id": company.id, "company": company.name,
        "scanned_product_lines": len(source_lines), "eligible_customer_product_pairs": len(sources),
        "product_types": dict(Counter(line.product_id.type for line in source_lines)),
        "lines_with_container": sum(bool((line.container_num or "").strip()) for line in source_lines),
        "skipped": dict(skipped),
    }, ensure_ascii=False))
    if not sources:
        print("SOURCE EXAMPLES " + json.dumps([
            {"line_id": line.id, "order": line.order_id.name, "state": line.order_id.state,
             "product": line.product_id.product_tmpl_id.name, "type": line.product_id.type,
             "quantity": line.product_uom_qty, "price": line.price_unit,
             "container": line.container_num or None}
            for line in source_lines[:12]
        ], ensure_ascii=False))
        print("COMPANY INVENTORY " + json.dumps([
            {"id": c.id, "company": c.name,
             "orders": data["sale.order"].search_count([("company_id", "=", c.id)])}
            for c in companies.search([])
        ], ensure_ascii=False))
        raise RuntimeError(
            "No suitable source lines. See SOURCE INVENTORY, SOURCE EXAMPLES and COMPANY INVENTORY above. "
            "Source quotations and product lines are accepted; Container Number is optional."
        )

    category = data.ref("taj_transport_operations.fleet_category_truck")
    vehicles = data["fleet.vehicle"].search([
        ("active", "=", True),
        ("company_id", "in", [False, company.id]),
        ("license_plate", "!=", False),
    ], order="license_plate, id")

    Partners = data["res.partner"]
    driver_domain = [("active", "=", True), ("is_company", "=", False),
                     ("company_id", "in", [False, company.id])]
    explicit_driver_ids = os.environ.get("TAJ_PREP_DRIVER_IDS", "").strip()
    if explicit_driver_ids and create_uat_drivers:
        raise RuntimeError("Choose explicit existing driver IDs or UAT driver creation, not both.")
    if explicit_driver_ids:
        requested_ids = {int(value.strip()) for value in explicit_driver_ids.split(",")}
        driver_pool = Partners.search(driver_domain + [("id", "in", sorted(requested_ids))])
        if set(driver_pool.ids) != requested_ids:
            raise RuntimeError("TAJ_PREP_DRIVER_IDS must contain existing active person contacts in this company or shared.")
    else:
        driver_pool = vehicles.driver_id
        driver_pool |= data["fleet.vehicle.assignation.log"].search([
            ("vehicle_id", "in", vehicles.ids),
        ]).driver_id
        driver_pool |= Partners.search(driver_domain + [
            "|", ("function", "ilike", "driver"), ("category_id.name", "ilike", "driver"),
        ])
        if "hr.employee" in data.registry:
            employees = data["hr.employee"].search([
                ("active", "=", True), ("company_id", "=", company.id),
                "|", ("job_title", "ilike", "driver"), ("job_id.name", "ilike", "driver"),
            ])
            # Read existing work contacts only: never create contacts for HR.
            driver_pool |= employees.work_contact_id
        driver_pool &= Partners.search(driver_domain + [("id", "in", driver_pool.ids)])
    driver_pool = driver_pool.filtered(
        lambda person: not (person.name or "").upper().startswith(("UAT-", "REAL-UAT-"))
    ).sorted("id")

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
    drivers_by_vehicle = {}
    new_driver_values = {}
    fleet_before = {}
    sales_vehicle_ids = set(source_lines.vehicle_id.ids)
    trailer_history_ids = set(data["taj.transport.assignment"].sudo().search([
        ("trailer_id", "in", vehicles.ids),
    ]).trailer_id.ids)
    # Preserve configured trucks/drivers first. Only uncategorized vehicles
    # already referenced by Sales may be newly categorized as Trucks.
    for vehicle in sorted(vehicles, key=lambda v: (not bool(v.driver_id), v.category_id != category, v.id)):
        if vehicle.id in busy_vehicles or vehicle.id in trailer_history_ids:
            continue
        if (vehicle.license_plate or "").upper().startswith("UAT-"):
            continue
        if vehicle.category_id and vehicle.category_id != category:
            continue
        if not vehicle.category_id and (
                vehicle.id not in sales_vehicle_ids
                or (vehicle.model_id.category_id and vehicle.model_id.category_id != category)):
            continue
        if vehicle.driver_id:
            driver = vehicle.driver_id
            if driver not in driver_pool or driver.id in busy_drivers | selected_drivers:
                continue
        else:
            driver = driver_pool.filtered(lambda person: person.id not in busy_drivers | selected_drivers)[:1]
            if not driver and not create_uat_drivers:
                continue
            if not driver:
                number = len(new_driver_values) + 1
                new_driver_values[vehicle.id] = {
                    "name": "%s UAT Driver %02d" % (prefix, number),
                    "ref": "%s-DRIVER-%02d" % (prefix, number),
                    "function": "UAT Driver (test only)", "company_id": company.id,
                    "is_company": False, "type": "contact", "active": True,
                    "comment": "Synthetic driver for staging UAT only. " + prefix,
                }
        trucks.append(vehicle)
        if driver:
            selected_drivers.add(driver.id)
        drivers_by_vehicle[vehicle.id] = driver
        fleet_before[vehicle.id] = {"category_id": vehicle.category_id.id, "driver_id": vehicle.driver_id.id}
        if len(trucks) == count:
            break
    if len(trucks) < count:
        fleet = data["fleet.vehicle"].search([
            ("active", "=", True), ("company_id", "in", [False, company.id]),
        ])
        print("FLEET INVENTORY " + json.dumps({
            "active_vehicles": len(fleet), "truck_category_id": category.id,
            "correctly_categorized_trucks": len(fleet.filtered(lambda v: v.category_id == category)),
            "recognized_existing_driver_contacts": len(driver_pool),
            "planned_new_uat_drivers": len(new_driver_values),
            "sales_referenced_vehicles": len(sales_vehicle_ids),
            "available_trucks_with_distinct_drivers": len(trucks),
            "examples": [{"truck": v.license_plate, "category": v.category_id.display_name or None,
                          "driver": v.driver_id.display_name or None} for v in fleet[:12]],
            "driver_examples": [{"id": p.id, "name": p.display_name} for p in driver_pool[:12]],
        }, ensure_ascii=False))
        raise RuntimeError(
            "Need %s available existing Sales trucks and distinct active driver contacts; found %s pairings. "
            "Driver contacts are read from Fleet/history, Driver jobs/tags and HR Driver work contacts. "
            "If driver roles are not labeled, set TAJ_PREP_DRIVER_IDS to actual existing Contact IDs. "
            "Or set TAJ_PREP_CREATE_UAT_DRIVERS=YES to allow clearly labeled test contacts. "
            "Occupied resources and other vehicle categories are never taken over." % (count, len(trucks))
        )

    if new_driver_values and Partners.with_context(active_test=False).search_count([
            ("name", "in", [values["name"] for values in new_driver_values.values()])]):
        raise RuntimeError("UAT driver names already exist for this prefix; choose a new TAJ_PREP_PREFIX.")

    fleet_changes = [
        {"truck_id": truck.id, "truck": truck.license_plate,
         "before": fleet_before[truck.id], "category_id": category.id, "category": category.name,
         "driver_id": drivers_by_vehicle[truck.id].id,
         "driver": drivers_by_vehicle[truck.id].display_name if drivers_by_vehicle[truck.id]
                   else new_driver_values[truck.id]["name"],
         "create_uat_driver": truck.id in new_driver_values,
         "pairing": "existing Fleet driver" if truck.driver_id else "UAT pairing"}
        for truck in trucks
    ]

    states = ("draft", "ready", "in_progress", "completed")
    plan = []
    for index in range(count):
        source = sources[index % len(sources)]
        route = route_plans[source.product_id.product_tmpl_id.id]
        truck = trucks[index]
        plan.append((source, route, truck, states[index % len(states)]))
    preview = [
        {"number": index, "target_state": state, "source_order": source.order_id.name,
         "source_order_id": source.order_id.id, "source_line_id": source.id,
         "customer": source.order_id.partner_id.display_name, "customer_id": source.order_id.partner_id.id,
         "product": source.product_id.display_name, "product_id": source.product_id.id,
         "quantity": source.product_uom_qty, "uom": source.product_uom_id.display_name,
         "unit_price": source.price_unit, "currency": source.order_id.currency_id.name,
         "discount": source.discount, "sales_description": source.name,
         "taxes": source.tax_ids.mapped("display_name"), "container": source.container_num,
         "shipment_reference": source.srn or None, "consignee": source.consignee or None,
         "size": source.size or None, "weight": source.weight or None,
         "file_name": source.file_name or None,
         "payment_terms": source.order_id.payment_term_id.display_name or None,
         "pricelist": source.order_id.pricelist_id.display_name,
         "cargo_type": source.order_id.cargo_type_id.display_name or None,
         "cargo_description": source.order_id.cargo_description if "cargo_description" in source.order_id._fields else None,
         "special_handling": source.order_id.special_handling_instructions or None,
         "route": route["name"], "create_route": route["record"] is None,
         "origin": route["origin"], "destination": route["destination"], "truck": truck.license_plate,
         "truck_id": truck.id,
         "driver": drivers_by_vehicle[truck.id].display_name if drivers_by_vehicle[truck.id]
                   else new_driver_values[truck.id]["name"],
         "driver_id": drivers_by_vehicle[truck.id].id}
        for index, (source, route, truck, state) in enumerate(plan, 1)
    ]
    print(json.dumps({"database": expected, "company": company.display_name,
                      "prefix": prefix, "mode": "APPLY" if apply else "PREVIEW ONLY",
                      "states": dict(Counter(row[3] for row in plan)),
                      "fleet_configuration": fleet_changes,
                      "new_uat_driver_contacts": list(new_driver_values.values()), "orders": preview},
                     indent=2, ensure_ascii=False))
    print("Real master data and historical prices/container details are reused in NEW UAT transactions.")
    print("Missing route templates will be created only for selected products, using their existing route names.")
    print("New templates have two endpoint checkpoints: Loading at origin and Delivery at destination.")
    print("Schedules and lifecycle/checkpoint events are generated UAT examples, not real shipment history.")
    print("Bill of Lading stays empty. Missing optional cargo descriptions/handling notes stay empty.")
    print("Blank source containers stay blank. Inventory procurement is disabled for these UAT orders.")
    print("Selected existing Sales vehicles may be categorized as Trucks and paired with drivers for UAT.")
    print("New clearly labeled UAT driver contacts planned:", len(new_driver_values))
    print("These proposed pairings are UAT setup, not verified real-world truck-driver assignments.")
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

    created_drivers = []
    for truck_id, values in new_driver_values.items():
        if Partners.with_context(active_test=False).search_count([("name", "=", values["name"])]):
            raise RuntimeError("A planned UAT driver name was created by another run; rerun with a new prefix.")
        driver = Partners.create(values)
        drivers_by_vehicle[truck_id] = driver
        created_drivers.append({"id": driver.id, "name": driver.name, "reference": driver.ref})
    for row in fleet_changes + preview:
        row["driver_id"] = drivers_by_vehicle[row["truck_id"]].id

    selected_fleet = data["fleet.vehicle"].browse([truck.id for truck in trucks])
    selected_fleet.flush_recordset(["category_id", "driver_id", "active", "company_id", "model_id"])
    data.cr.execute("SELECT id FROM fleet_vehicle WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (selected_fleet.ids,))
    selected_fleet.invalidate_recordset(["category_id", "driver_id", "active", "company_id", "model_id"])
    busy_vehicles, busy_drivers = occupied_resources()
    for truck in trucks:
        before, driver = fleet_before[truck.id], drivers_by_vehicle[truck.id]
        driver.invalidate_recordset(["active", "company_id", "is_company"])
        truck.model_id.invalidate_recordset(["category_id"])
        if (not truck.active or (truck.company_id and truck.company_id != company)
                or truck.category_id.id != before["category_id"] or truck.driver_id.id != before["driver_id"]
                or not driver.active or driver.is_company or (driver.company_id and driver.company_id != company)
                or (not truck.category_id and truck.model_id.category_id and truck.model_id.category_id != category)
                or truck.id in busy_vehicles or driver.id in busy_drivers):
            raise RuntimeError("Fleet configuration or availability changed; rerun the preview.")
        changes = {}
        if truck.category_id != category:
            changes["category_id"] = category.id
        if truck.driver_id != driver:
            changes["driver_id"] = driver.id
        if changes:
            truck.write(changes)
        if truck.category_id != category or truck.driver_id != driver:
            raise RuntimeError("Fleet setup did not retain the proposed category/driver; rolling back.")

    created_routes = []
    selected_products = {source.product_id.product_tmpl_id.id for source, _, _, _ in plan}
    for template_id in sorted(selected_products):
        route_plan = route_plans[template_id]
        if route_plan["record"] is not None:
            continue
        if data["taj.transport.route"].search_count([
                ("active", "=", True), ("company_id", "=", company.id),
                ("product_tmpl_id", "=", template_id)]):
            raise RuntimeError("A route was linked to a selected product during preparation; rerun the preview.")
        route = data["taj.transport.route"].create({
            "name": route_plan["name"], "company_id": company.id,
            "product_tmpl_id": template_id,
            "origin_location": route_plan["origin"],
            "destination_location": route_plan["destination"],
            "notes": "UAT endpoint template derived from existing product %s. %s" % (template_id, prefix),
            "line_ids": [
                Command.create({"name": "Loading at " + route_plan["origin"], "sequence": 10}),
                Command.create({"name": "Delivery at " + route_plan["destination"], "sequence": 20}),
            ],
        })
        route_plan["record"] = route
        created_routes.append({"id": route.id, "name": route.name,
                               "product_template_id": template_id,
                               "origin": route.origin_location, "destination": route.destination_location,
                               "checkpoints": route.line_ids.mapped("name")})

    results = []
    for index, (source, route_plan, truck, target) in enumerate(plan, 1):
        route = route_plan["record"]
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
        planned_departure = now + timedelta(hours=1) if target in ("draft", "ready") else now - timedelta(hours=1)
        duration_hours = max(48.0, max(trip.milestone_ids.mapped("planned_offset_hours"), default=0.0))
        trip.write({
            "planned_departure_at": planned_departure,
            "planned_arrival_at": planned_departure + timedelta(hours=duration_hours),
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
        if "picking_ids" in order._fields and order.picking_ids:
            raise RuntimeError("Unexpected inventory transfer created; rolling back.")
        if (trip.cargo_type_id != order.cargo_type_id
                or trip.special_handling_instructions != order.special_handling_instructions
                or ("cargo_description" in order._fields and trip.cargo_description != order.cargo_description)):
            raise RuntimeError("Trip cargo snapshots do not match the new sales order.")
        results.append({**preview[index - 1], "order": order.name, "order_id": order.id,
                        "sales_line_id": line.id, "customer_reference": order.client_order_ref,
                        "company": company.display_name, "total": order.amount_total,
                        "trip": trip.name, "trip_id": trip.id, "state": trip.state,
                        "file": trip_file.name, "file_id": trip_file.id,
                        "bill_of_lading": trip_file.bill_of_lading or None,
                        "route_id": route.id,
                        "planned_departure": trip.planned_departure_at,
                        "planned_arrival": trip.planned_arrival_at,
                        "actual_departure": trip.actual_departure_at or None,
                        "actual_arrival": trip.actual_arrival_at or None,
                        "milestones_done": trip.milestone_done_count,
                        "milestones_open": trip.milestone_open_count,
                        "milestones": [{"id": m.id, "name": m.name, "state": m.state,
                                        "arrival": m.actual_arrival_at or None,
                                        "departure": m.actual_departure_at or None}
                                       for m in trip.milestone_ids.sorted(lambda m: (m.sequence, m.id))]})
    for model, before in master_counts.items():
        expected_new = len(created_routes) * {"taj.transport.route": 1, "taj.transport.route.line": 2}.get(model, 0)
        if model == "res.partner":
            expected_new += len(created_drivers)
        if data[model].with_context(active_test=False).search_count([]) != before + expected_new:
            raise RuntimeError("A workflow changed master record counts for %s; rolling back." % model)
    for source in sources:
        source.invalidate_recordset(["write_date"])
        source.order_id.invalidate_recordset(["write_date"])
        if (source.write_date, source.order_id.write_date) != source_stamps[source.id]:
            raise RuntimeError("An original sales record changed; rolling back.")
    return {"database": expected, "prefix": prefix, "fleet_configuration": fleet_changes,
            "created_driver_contacts": created_drivers, "created_routes": created_routes, "orders": results}


try:
    result = prepare(env)
    if result is None:
        env.cr.rollback()
    else:
        env.cr.commit()
        print("SAVED %s sales orders, trips and Trip Files; %s route templates; %s UAT drivers. Save this manifest:"
              % (len(result["orders"]), len(result["created_routes"]), len(result["created_driver_contacts"])))
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
except BaseException:
    env.cr.rollback()
    print("FAILED: no preparation records from this run were committed.")
    raise
TAJ_EXISTING_DATA_PY
