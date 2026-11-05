#!/usr/bin/env bash
# Paste the entire file into the Odoo.sh terminal, or run: bash taj_transport_audit.sh
# Queries run in a separate PostgreSQL read-only transaction and are rolled back.
bash -s -- "$@" <<'TAJ_TRANSPORT_SCRIPT'
set -euo pipefail
umask 077

if ! command -v odoo-bin >/dev/null 2>&1; then
    echo 'Run this script in the Odoo.sh terminal, where odoo-bin is available.' >&2
    exit 1
fi
report_file="${1:-$HOME/taj_transport_audit_$(date -u +%Y%m%d_%H%M%S).txt}"
shell_args=(shell --no-http --log-level=error --shell-interface=python)
if [[ -n "${PGDATABASE:-}" ]]; then
    shell_args+=(-d "$PGDATABASE")
fi

odoo-bin "${shell_args[@]}" <<'TAJ_AUDIT_PY' | tee "$report_file"
import json
import re
from datetime import datetime, timezone


def enable_read_only(cr):
    cr.execute("SET TRANSACTION READ ONLY")
    cr.execute("SHOW transaction_read_only")
    if cr.fetchone()[0] != "on":
        raise RuntimeError("Could not enable the read-only transaction")
    cr.execute("SET LOCAL statement_timeout = '20s'")
    cr.execute("SET LOCAL lock_timeout = '3s'")


def coverage(rows, names):
    return {
        name: {"set": sum(row.get(name) not in (None, False, "") for row in rows),
               "sample": len(rows)}
        for name in names
    }


def public_product_name(value):
    if isinstance(value, dict):
        value = value.get("en_US") or next(iter(value.values()), "")
    return str(value or "")[:120]


def run_audit(odoo_env):
    from psycopg2 import sql
    from odoo import release

    # No ORM record reads or business methods: sample only whitelisted SQL columns.
    field_pattern = re.compile(
        r"trip|transport|shipment|cargo|container|consignee|weight|size|srn|"
        r"vehicle|truck|trailer|driver|route|origin|destination|pickup|loading|"
        r"offload|milestone|arrival|departure|incident|assignment|delivery|"
        r"partner|customer|carrier|order_id|invoice|state|status|date|price|"
        r"currency|payment_term|quantity|product_uom|reference|company|analytic",
        re.I,
    )
    model_pattern = re.compile(
        r"transport|shipment|(?:^|[._ ])trip|milestone|geofence|assignment|incident|trailer|"
        r"رحل|نقل|مقطور",
        re.I,
    )
    baseline = [
        "sale.order", "sale.order.line", "purchase.order", "purchase.order.line",
        "account.move", "account.move.line", "fleet.vehicle", "stock.picking",
        "report.trip.profit", "fleet.navirec.report.subscription",
    ]
    errors = []

    def emit(label, value):
        print(label + " " + json.dumps(value, ensure_ascii=False, default=str), flush=True)

    with odoo_env.registry.cursor() as cr:
        enable_read_only(cr)

        def query(statement, params=None):
            cr.execute(statement, params)
            return cr.dictfetchall()

        def section(label, callback):
            try:
                with cr.savepoint(flush=False):
                    callback()
            except Exception as exc:
                # Do not include database exception text or arbitrary record values.
                errors.append({"section": label, "error_type": type(exc).__name__})
                emit("SECTION_ERROR", errors[-1])

        def columns(table):
            return {
                row["column_name"] for row in query(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (table,),
                )
            }

        def records(model_name, wanted, limit=10, order_ids=None):
            model = odoo_env[model_name]
            present = columns(model._table)
            selected = [name for name in wanted if name in present]
            if "id" not in selected:
                return []
            condition = sql.SQL("")
            params = []
            if order_ids is not None:
                if "order_id" not in present or not order_ids:
                    return []
                condition = sql.SQL("WHERE order_id = ANY(%s)")
                params.append(order_ids)
            params.append(limit)
            statement = sql.SQL("SELECT {} FROM {} {} ORDER BY id DESC LIMIT %s").format(
                sql.SQL(", ").join(sql.Identifier(name) for name in selected),
                sql.Identifier(model._table), condition,
            )
            return query(statement, params)

        print("TAJ_TRANSPORT_AUDIT_START", flush=True)
        emit("ENVIRONMENT", {
            "odoo_version": release.version,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "sample_policy": "Latest 20 sales orders, up to 1000 lines; bounded ID-based samples.",
            "privacy": "Partner/driver/user IDs only. No contacts, tokens, notes or document contents.",
        })

        def installed_modules():
            rows = query("SELECT name, state, latest_version FROM ir_module_module "
                         "WHERE state = 'installed' ORDER BY name")
            relevant = re.compile(r"fleet|sale|purchase|account|stock|delivery|transport|trip|"
                                  r"navirec|move_invoice_line|send_report|studio", re.I)
            emit("INSTALLED_MODULES", [r for r in rows if relevant.search(r["name"])])

        section("modules", installed_modules)
        discovered = sorted(
            name for name, model in odoo_env.registry.models.items()
            if not model._abstract and not model._transient
            and model_pattern.search(name + " " + str(model._description))
        )
        emit("DISCOVERED_OPERATIONAL_MODELS", {
            "models": discovered,
            "note": "Name matches are candidates, not proof of an operational trip workflow.",
        })
        model_names = list(dict.fromkeys(baseline + discovered))[:30]

        def model_schema(name):
            if name not in odoo_env.registry.models:
                emit("MODEL_MISSING", name)
                return
            model = odoo_env[name]
            fields = []
            for key, field in sorted(model._fields.items()):
                if not (key.startswith("x_") or field_pattern.search(key + " " + str(field.string))):
                    continue
                item = {"field": key, "label": str(field.string), "type": field.type,
                        "relation": getattr(field, "comodel_name", None),
                        "required": bool(field.required), "stored": bool(field.store),
                        "computed": bool(field.compute), "related": field.related or None}
                if field.type == "selection":
                    choices = field.selection
                    item["choices"] = choices if isinstance(choices, (list, tuple)) else "dynamic"
                fields.append(item)
            emit("MODEL_SCHEMA", {"model": name, "table": model._table, "fields": fields})

        for name in model_names:
            section("schema:" + name, lambda name=name: model_schema(name))

        def sale_samples():
            if "sale.order" not in odoo_env.registry.models or "sale.order.line" not in odoo_env.registry.models:
                emit("SALES_SAMPLE", "Sales models not available")
                return
            orders = records("sale.order", [
                "id", "name", "state", "date_order", "company_id", "partner_id",
                "user_id", "currency_id", "payment_term_id", "invoice_status",
                "commitment_date",
            ], limit=20)
            ids = [row["id"] for row in orders]
            wanted = ["id", "order_id", "display_type", "product_id", "vehicle_id",
                      "product_uom_qty", "product_uom_id", "product_uom", "qty_delivered",
                      "qty_invoiced", "price_unit", "price_subtotal", "currency_id",
                      "analytic_account_id", "weight", "size", "container_num",
                      "file_name", "srn", "consignee"]
            lines = records("sale.order.line", wanted, limit=1000, order_ids=ids)
            products = {}
            product_ids = sorted({row["product_id"] for row in lines if row.get("product_id")})
            if product_ids:
                rows = query("SELECT p.id, t.name FROM product_product p "
                             "JOIN product_template t ON t.id = p.product_tmpl_id "
                             "WHERE p.id = ANY(%s)", (product_ids,))
                products = {row["id"]: public_product_name(row["name"]) for row in rows}
            per_order = []
            for order in orders:
                order_lines = [row for row in lines if row.get("order_id") == order["id"]
                               and row.get("display_type") in (None, False, "", "product")]
                vehicle_ids = sorted({row["vehicle_id"] for row in order_lines if row.get("vehicle_id")})
                per_order.append(dict(order, sampled_product_lines=len(order_lines),
                                      sampled_vehicle_ids=vehicle_ids))
            emit("SALES_ORDER_SAMPLE", per_order)
            emit("SAMPLE_LIMITS", {"orders": len(orders), "lines": len(lines),
                                   "lines_may_be_truncated": len(lines) == 1000})
            data_lines = [row for row in lines if row.get("display_type") in (None, False, "", "product")]
            present = columns(odoo_env["sale.order.line"]._table)
            cargo_fields = [key for key in ("vehicle_id", "weight", "size", "container_num",
                                            "file_name", "srn", "consignee") if key in present]
            emit("SALES_FIELD_COVERAGE", coverage(data_lines, cargo_fields))
            public_fields = [key for key in wanted if key not in cargo_fields or key == "vehicle_id"]
            for row in data_lines[:24]:
                safe = {key: row[key] for key in public_fields if key in row}
                safe["product_label"] = products.get(row.get("product_id"), "")
                emit("SALES_LINE_SAMPLE", safe)

        section("sales_samples", sale_samples)

        def fleet_summary():
            if "fleet.vehicle" not in odoo_env.registry.models:
                emit("FLEET_SUMMARY", "Fleet model not available")
                return
            table = odoo_env["fleet.vehicle"]._table
            present = columns(table)
            selected = [key for key in ("id", "active", "company_id", "driver_id", "trailer_id",
                                        "navirec_uuid", "navirec_last_state_time") if key in present]
            rows = records("fleet.vehicle", selected, limit=1000)
            emit("FLEET_SUMMARY", {
                "sampled_vehicles": len(rows), "sample_may_be_truncated": len(rows) == 1000,
                "coverage": coverage(rows, [key for key in selected if key not in ("id", "active")]),
                "note": "A vehicle's current driver is not evidence of per-trip assignment history.",
            })

        section("fleet_summary", fleet_summary)

        def access_rules():
            rows = query(
                "SELECT m.model, a.group_id, a.perm_read, a.perm_write, a.perm_create, a.perm_unlink "
                "FROM ir_model_access a JOIN ir_model m ON m.id = a.model_id "
                "WHERE a.active AND m.model = ANY(%s) ORDER BY m.model, a.group_id LIMIT 150",
                (model_names,),
            )
            group_ids = sorted({r["group_id"] for r in rows if r["group_id"]})
            group_refs = query("SELECT res_id, module || '.' || name AS xml_id FROM ir_model_data "
                               "WHERE model = 'res.groups' AND res_id = ANY(%s)", (group_ids,))
            emit("MODEL_ACCESS_RIGHTS", rows)
            emit("GROUP_TECHNICAL_REFERENCES", group_refs)
            emit("ACCESS_LIMIT", "Model ACLs only; record rules, actual memberships and operational approval policy are not evaluated.")

        section("access_rules", access_rules)
        emit("QUESTIONS_REQUIRING_BUSINESS_CONFIRMATION", [
            "What triggers a trip: a quotation, a confirmed sale or an operations request?",
            "Does one trip mean one truck execution or a multi-truck customer job?",
            "Is the return leg a new trip? Can a trip serve multiple customers?",
            "Are multiple pickup/drop-off points supported, and what are mandatory checkpoints?",
            "Who assigns/replaces truck, trailer and driver, and when?",
            "What proves delivery, and who may close or reopen a trip?",
            "Which pricing unit applies, and when should invoices be generated?",
        ])
        emit("AUDIT_RESULT", {"read_only": True, "errors": errors,
                              "complete": not errors, "business_workflow_confirmed": False})
        cr.rollback()
        print("TAJ_TRANSPORT_AUDIT_END", flush=True)


if "env" in globals():
    run_audit(env)
TAJ_AUDIT_PY

printf '\nReport saved to: %s\n' "$report_file"
TAJ_TRANSPORT_SCRIPT
