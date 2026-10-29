#!/usr/bin/env bash
# Add the Going Cargo Excel row 14 route to the existing staging dataset.
# Preview: bash ~/src/user/scripts/taj_transport_add_report_route.sh
# Apply: TAJ_REPORT_APPLY=YES bash ~/src/user/scripts/taj_transport_add_report_route.sh
# Optional existing IDs: TAJ_REPORT_COMPANY_ID, TAJ_REPORT_PRODUCT_TMPL_ID,
# TAJ_REPORT_ROUTE_ID. Defaults match the previously prepared TAJ dataset.
set -euo pipefail
command -v odoo-bin >/dev/null || { echo 'Run this in the Odoo.sh shell.' >&2; exit 1; }
: "${PGDATABASE:?Run this in the upgrade staging database shell}"

odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
    --shell-interface=python --log-level=error <<'TAJ_REPORT_ROUTE_PY'
import json
import os
import re

from odoo import Command


SOURCE = "TAJ TRACKING REPORTS 31-08-2026 (1).xlsx | Transit - Going Cargo | row 14"
CHECKPOINTS = [
    ("Loading at Dar es Salaam", "Arrival at site, loading and departure: 12 August 2026."),
    ("Tunduma", "Arrival: 15 August 2026. Departure: 19 August 2026."),
    ("Nakonde", "Arrival: 19 August 2026. Departure: 21 August 2026."),
    ("Kasumbalesa Zambia", "Arrival: 22 August 2026. No departure date column in this report row."),
    ("Kasumbalesa DRC", "Arrival: 23 August 2026. No departure date column in this report row."),
    ("Whiskey", "Arrival: 28 August 2026. Departure: 29 August 2026."),
    ("Delivery at Lubumbashi", "Destination arrival and offloading: 29 August 2026."),
]


def add_route(shell_env):
    database = shell_env.cr.dbname
    if database != os.environ.get("PGDATABASE") or not database.startswith("taj-limited-19-upgrade-"):
        raise RuntimeError("Run only in the TAJ 19_upgrade staging database.")
    if str(shell_env["ir.config_parameter"].sudo().get_param("database.is_neutralized")).lower() not in ("true", "1"):
        raise RuntimeError("A neutralized staging database is required.")
    company = shell_env["res.company"].sudo().browse(int(os.environ.get("TAJ_REPORT_COMPANY_ID", "1"))).exists()
    if not company:
        raise RuntimeError("Select an existing company with TAJ_REPORT_COMPANY_ID.")
    data = company.sudo().with_context(allowed_company_ids=company.ids, lang="en_US").with_company(company).env
    product = data["product.template"].browse(int(os.environ.get("TAJ_REPORT_PRODUCT_TMPL_ID", "3174"))).exists()
    if (not product or not product.active or not product.sale_ok
            or (product.company_id and product.company_id != company)
            or not re.search(r"\bdar\b.*\blubumbashi\b", product.name, re.I)):
        raise RuntimeError("TAJ_REPORT_PRODUCT_TMPL_ID must identify the existing Dar to Lubumbashi sales product.")

    domain = [("active", "=", True), ("company_id", "=", company.id), ("product_tmpl_id", "=", product.id)]
    route_id = int(os.environ.get("TAJ_REPORT_ROUTE_ID", "0"))
    routes = data["taj.transport.route"].search(domain + ([("id", "=", route_id)] if route_id else []))
    if len(routes) > 1 or (route_id and not routes):
        raise RuntimeError("Select one matching active template with TAJ_REPORT_ROUTE_ID.")
    route = routes
    lines = route.line_ids.sorted(lambda line: (line.sequence, line.id))
    expected_names = [name for name, _ in CHECKPOINTS]
    if not lines:
        existing = {}
    elif len(lines) == 2 and lines[0].name.startswith("Loading at ") and lines[1].name.startswith("Delivery at "):
        existing = {expected_names[0]: lines[0], expected_names[-1]: lines[1]}
    elif len(lines) == len(CHECKPOINTS) and set(lines.mapped("name")) == set(expected_names):
        existing = {line.name: line for line in lines}
    else:
        raise RuntimeError("Template already has other checkpoints. No checkpoints were removed or overwritten.")

    route_values = {
        "name": "DAR - LUBUMBASHI (Tracking report row 14)",
        "origin_location": "Dar es Salaam", "destination_location": "Lubumbashi",
        "notes": SOURCE + "\nEndpoint and border sequence from the report. "
                 "Dates in checkpoint notes are historical actuals, not planned offsets or new trip events.",
    }
    print(json.dumps({"database": database, "company": company.name,
                      "product_template_id": product.id, "product": product.display_name,
                      "existing_route_id": route.id or None, "source": SOURCE,
                      "template": route_values,
                      "checkpoints": [{"sequence": (index + 1) * 10, "name": name,
                                       "historical_reference": note} for index, (name, note) in enumerate(CHECKPOINTS)],
                      "existing_trip_snapshots": "preserved"}, indent=2, ensure_ascii=False))
    if os.environ.get("TAJ_REPORT_APPLY") != "YES":
        print("PREVIEW ONLY. To save, rerun with TAJ_REPORT_APPLY=YES on the same command line.")
        return None

    data.cr.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", ("taj-report-route-%s-%s" % (company.id, product.id),))
    if not data.cr.fetchone()[0]:
        raise RuntimeError("Another report route import is running.")
    if route:
        data.cr.execute("SELECT id FROM taj_transport_route WHERE id = %s FOR UPDATE", (route.id,))
        before = [(line.id, line.sequence, line.name, line.notes) for line in lines]
        route.invalidate_recordset()
        lines.invalidate_recordset()
        if not route.active or route.company_id != company or route.product_tmpl_id != product:
            raise RuntimeError("Template company, product or active status changed; rerun the preview.")
        if before != [(line.id, line.sequence, line.name, line.notes)
                      for line in route.line_ids.sorted(lambda line: (line.sequence, line.id))]:
            raise RuntimeError("Template checkpoints changed during preparation; rerun the preview.")
    elif data["taj.transport.route"].search_count(domain):
        raise RuntimeError("A matching template was created during preparation; rerun the preview.")

    trips = data["taj.transport.trip"].search([("route_id", "=", route.id)]) if route else data["taj.transport.trip"]

    def snapshots():
        milestones = trips.milestone_ids.sorted("id")
        milestones.invalidate_recordset()
        return [(m.id, m.name, m.sequence, m.state, m.actual_arrival_at,
                 m.actual_departure_at, m.template_line_id.id) for m in milestones]

    before_snapshots = snapshots()
    commands = []
    for index, (name, note) in enumerate(CHECKPOINTS, 1):
        values = {"name": name, "sequence": index * 10,
                  "notes": "Historical report example only. " + note}
        line = existing.get(name)
        if line:
            changes = {field: value for field, value in values.items() if line[field] != value}
            if changes:
                commands.append(Command.update(line.id, changes))
        else:
            commands.append(Command.create(values))
    if route:
        changes = {field: value for field, value in route_values.items() if route[field] != value}
        if commands:
            changes["line_ids"] = commands
        if changes:
            route.write(changes)
    else:
        route = data["taj.transport.route"].create(dict(
            route_values, product_tmpl_id=product.id, company_id=company.id, line_ids=commands,
        ))
    data.flush_all()
    if route.line_ids.sorted(lambda line: (line.sequence, line.id)).mapped("name") != expected_names:
        raise RuntimeError("Route checkpoint verification failed; rolling back.")
    if snapshots() != before_snapshots:
        raise RuntimeError("Existing trip milestones changed unexpectedly; rolling back.")
    return {"route_id": route.id, "route": route.name, "product_template_id": product.id,
            "checkpoints": route.line_ids.sorted(lambda line: (line.sequence, line.id)).mapped("name"),
            "existing_trips_preserved": trips.ids, "new_trips_inherit_full_route": True}


try:
    result = add_route(env)
    if result is None:
        env.cr.rollback()
    else:
        env.cr.commit()
        print("SAVED report route template and seven checkpoints:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
except BaseException:
    env.cr.rollback()
    print("FAILED: no report route changes from this run were committed.")
    raise
TAJ_REPORT_ROUTE_PY
