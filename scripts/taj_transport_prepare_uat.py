"""Prepare new staging-only fixtures for manual UAT; never execute UAT actions.

Preview (no writes):
    odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
        < ~/src/user/scripts/taj_transport_prepare_uat.py

Create after reviewing the preview:
    TAJ_UAT_APPLY=YES odoo-bin shell -d "$PGDATABASE" --no-http --max-cron-threads=0 \
        < ~/src/user/scripts/taj_transport_prepare_uat.py

Optional environment settings: TAJ_UAT_PREFIX, TAJ_UAT_TAX_ID,
TAJ_UAT_SECOND_COMPANY_ID. Run in a fresh Odoo shell. Existing records/settings
are not reset; a used prefix is refused. Any error rolls back the entire seed.
"""

import json
import os
import re
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import Command, fields


EXPECTED_DATABASE = "taj-limited-19-upgrade-38909886"
COMPANY_NAME = "TAJ Trucking Limited"
UAT_TIMEZONE = "Africa/Dar_es_Salaam"
PREFIX = os.environ.get(
    "TAJ_UAT_PREFIX", "UAT-%s-01" % fields.Date.today().strftime("%Y%m%d")
)
APPLY = os.environ.get("TAJ_UAT_APPLY") == "YES"
TAX_ID = int(os.environ.get("TAJ_UAT_TAX_ID", "0"))
SECOND_COMPANY_ID = int(os.environ.get("TAJ_UAT_SECOND_COMPANY_ID", "0"))


def version_tuple(value):
    return tuple(int(part) for part in (value or "0").split("."))


def prepare(environment):
    if environment.cr.dbname != EXPECTED_DATABASE:
        raise RuntimeError("Stopped: this script targets only " + EXPECTED_DATABASE)
    if not re.fullmatch(r"UAT-[A-Z0-9-]+", PREFIX):
        raise RuntimeError(
            "TAJ_UAT_PREFIX must start UAT- and use A-Z, digits and hyphens."
        )

    company = environment["res.company"].sudo().search([("name", "=", COMPANY_NAME)])
    if len(company) != 1:
        raise RuntimeError("Expected exactly one company named " + COMPANY_NAME)
    foreign = environment["res.company"].sudo().browse(SECOND_COMPANY_ID).exists()
    if SECOND_COMPANY_ID and (not foreign or foreign == company):
        raise RuntimeError(
            "Choose an existing, different second company, or leave it unset."
        )

    context = dict(
        environment.context,
        allowed_company_ids=(company | foreign).ids,
        active_test=True,
        tz=UAT_TIMEZONE,
        lang="en_US",
        tracking_disable=True,
        mail_notrack=True,
        mail_create_nolog=True,
        mail_create_nosubscribe=True,
        mail_auto_subscribe_no_notify=True,
        no_reset_password=True,
        send_email=False,
    )
    seed = company.sudo().with_context(context).with_company(company).env
    for name, required in (
        ("taj_transport_operations", "19.0.1.11.0"),
        ("fleet_navirec", "19.0.1.4.0"),
    ):
        module = seed["ir.module.module"].search([("name", "=", name)])
        # Odoo's latest_version stores the installed DB version; the confusingly
        # named installed_version is computed from the source manifest.
        if module.state != "installed" or version_tuple(
            module.latest_version
        ) < version_tuple(required):
            raise RuntimeError(
                "Manually upgrade %s to %s before seeding." % (name, required)
            )

    # Refuse a second run even after the tester has changed/archived fixtures.
    collisions = []
    for model, field, pattern in (
        ("res.partner", "name", PREFIX + " %"),
        ("res.users", "login", PREFIX.lower() + ".%"),
        ("fleet.vehicle", "license_plate", PREFIX + "-%"),
        ("fleet.vehicle.model.brand", "name", PREFIX + " %"),
        ("fleet.vehicle.model", "name", PREFIX + " %"),
        ("fleet.vehicle.model.category", "name", PREFIX + " %"),
        ("product.template", "name", PREFIX + " %"),
        ("product.pricelist", "name", PREFIX + " %"),
        ("account.payment.term", "name", PREFIX + " %"),
        ("taj.transport.transporter", "name", PREFIX + " %"),
        ("taj.transport.cargo.type", "name", PREFIX + " %"),
        ("taj.transport.milestone.type", "name", PREFIX + " %"),
        ("taj.transport.route", "name", PREFIX + " %"),
        ("sale.order", "client_order_ref", PREFIX + "-%"),
        ("fleet.navirec.report.subscription", "name", PREFIX + " %"),
    ):
        if (
            seed[model]
            .with_context(active_test=False)
            .search_count([(field, "=like", pattern)])
        ):
            collisions.append(model)
    if collisions:
        raise RuntimeError(
            "This prefix is already used (%s). Existing UAT work is untouched. "
            "Use a new TAJ_UAT_PREFIX for a fresh run." % ", ".join(collisions)
        )

    truck_category = seed.ref("taj_transport_operations.fleet_category_truck")
    trailer_category = seed.ref("taj_transport_operations.fleet_category_trailer")
    taj = seed["taj.transport.transporter"].search([("name", "=ilike", "TAJ")])
    if len(taj) != 1:
        raise RuntimeError(
            "The default-transporter case needs exactly one transporter named TAJ."
        )

    Account = seed["account.account"]
    income = Account.search(
        [
            ("company_ids", "in", company.ids),
            ("account_type", "=", "income"),
        ],
        order="code, id",
        limit=1,
    )
    receivable = Account.search(
        [
            ("company_ids", "in", company.ids),
            ("account_type", "=", "asset_receivable"),
        ],
        order="code, id",
        limit=1,
    )
    if (
        not income
        or not receivable
        or not seed["account.journal"].search_count(
            [
                ("company_id", "=", company.id),
                ("type", "=", "sale"),
            ]
        )
    ):
        raise RuntimeError(
            "The company needs existing income/receivable accounts and a sales journal."
        )

    tax_domain = [
        ("company_id", "=", company.id),
        ("type_tax_use", "=", "sale"),
        ("amount_type", "=", "percent"),
        ("price_include", "=", False),
        ("amount", ">", 0),
        ("amount", "<=", 100),
    ]
    if company.account_fiscal_country_id:
        tax_domain.append(("country_id", "=", company.account_fiscal_country_id.id))
    if TAX_ID:
        tax_domain.append(("id", "=", TAX_ID))
    tax = seed["account.tax"].search(tax_domain, order="id", limit=1)
    if TAX_ID and not tax:
        raise RuntimeError(
            "TAJ_UAT_TAX_ID is not an eligible tax-excluded percentage sales tax."
        )

    group_ids = {
        name: seed.ref(xml_id).id
        for name, xml_id in {
            "internal": "base.group_user",
            "sales": "sales_team.group_sale_salesman_all_leads",
            "dispatcher": "taj_transport_operations.group_transport_user",
            "manager": "taj_transport_operations.group_transport_manager",
        }.items()
    }
    print("\nUAT DATA PLAN")
    print("Database:", environment.cr.dbname, "| Company:", company.name)
    print("Prefix:", PREFIX, "| User/report timezone:", UAT_TIMEZONE)
    print(
        "Currency:", company.currency_id.name, "| Income account:", income.display_name
    )
    print(
        "Existing VAT:",
        "%s [ID %s, %s%%]" % (tax.display_name, tax.id, tax.amount)
        if tax
        else "NONE (0%; VAT-specific checks remain uncovered)",
    )
    print("Existing default transporter:", taj.name, "[ID %s]" % taj.id)
    print("Create: 2 customers, 3 driver Contacts, 2 test users, 10 Fleet vehicles,")
    print("3 products, 2 routes, UAT catalogs, 12 quotations and 2 inactive reports.")
    print(
        "Trips, assignments, invoices, GPS mappings, passwords and outgoing email: none."
    )
    print(
        "Second-company fixtures:",
        foreign.name
        if foreign
        else "not requested; company-isolation checks need separate fixtures",
    )
    if not APPLY:
        print("PREVIEW ONLY. Run with TAJ_UAT_APPLY=YES to create this dataset.")
        return None

    environment.cr.execute(
        "SELECT pg_try_advisory_xact_lock(hashtext(%s))", ("taj-uat-" + PREFIX,)
    )
    if not environment.cr.fetchone()[0]:
        raise RuntimeError(
            "Another seed is running with this prefix. Try again after it finishes."
        )
    # A concurrent seed may have committed since the initial preflight.
    if (
        seed["res.partner"]
        .with_context(active_test=False)
        .search_count(
            [
                ("name", "=like", PREFIX + " %"),
            ]
        )
    ):
        raise RuntimeError(
            "Another seed has already created this prefix. No changes made."
        )

    manifest = {
        "database": environment.cr.dbname,
        "prefix": PREFIX,
        "company": {"id": company.id, "name": company.name},
        "timezone": UAT_TIMEZONE,
        "records": {},
        "quotations": [],
    }

    def create(model, label, values, model_env=None):
        record = (model_env or seed)[model].create(values)
        manifest["records"][label] = {
            "model": model,
            "id": record.id,
            "name": record.display_name,
        }
        return record

    def partner(label, is_customer=False, partner_company=company):
        return create(
            "res.partner",
            label,
            {
                "name": PREFIX + " " + label,
                "company_type": "company" if is_customer else "person",
                "company_id": partner_company.id,
                "country_id": partner_company.country_id.id or False,
                "email": False,
                "phone": False,
                "customer_rank": 1 if is_customer else 0,
            },
            seed["res.company"]
            .browse(partner_company.id)
            .with_company(partner_company)
            .env,
        )

    customers = {key: partner("Customer " + key, True) for key in ("A", "B")}
    drivers = {key: partner("Driver " + str(key)) for key in (1, 2, 3)}
    users = {}
    for role in ("dispatcher", "manager"):
        users[role] = create(
            "res.users",
            role.title() + " User",
            {
                "name": PREFIX + " " + role.title(),
                "login": PREFIX.lower() + "." + role,
                "email": False,
                "active": True,
                "tz": UAT_TIMEZONE,
                "company_id": company.id,
                "company_ids": [Command.set(company.ids)],
                "group_ids": [
                    Command.set(
                        [
                            group_ids["internal"],
                            group_ids["sales"],
                            group_ids[role],
                        ]
                    )
                ],
            },
        )
        if users[role].has_group("base.group_system") or users[role].has_group(
            "fleet.fleet_group_user"
        ):
            raise RuntimeError(
                "Unexpected Settings/Fleet privileges on a new UAT user; rolling back."
            )
        if role == "dispatcher" and users[role].has_group(
            "taj_transport_operations.group_transport_manager"
        ):
            raise RuntimeError(
                "The new Dispatcher unexpectedly has Manager rights; rolling back."
            )
        manifest["records"][role.title() + " User"]["login"] = users[role].login

    create(
        "taj.transport.transporter",
        "Carrier",
        {"name": PREFIX + " Carrier", "email": False},
    )
    create(
        "taj.transport.cargo.type", "Cargo Type", {"name": PREFIX + " General Cargo"}
    )
    types = {
        label: create(
            "taj.transport.milestone.type",
            "Type " + label,
            {"name": PREFIX + " " + label},
        )
        for label in ("Loading", "Border", "Delivery")
    }
    brand = create(
        "fleet.vehicle.model.brand", "Fleet Brand", {"name": PREFIX + " Fleet"}
    )
    other_category = create(
        "fleet.vehicle.model.category", "Other Category", {"name": PREFIX + " Other"}
    )
    models = {}
    for label, category in (
        ("Truck", truck_category),
        ("Trailer", trailer_category),
        ("Other", other_category),
    ):
        models[label] = create(
            "fleet.vehicle.model",
            label + " Model",
            {
                "name": PREFIX + " " + label,
                "brand_id": brand.id,
                "category_id": category.id,
                "vehicle_type": "car",
            },
        )

    vehicles = {}
    for label, model_kind, driver, active in (
        ("TRUCK-A", "Truck", drivers[1], True),
        ("TRUCK-B", "Truck", drivers[2], True),
        ("TRUCK-C", "Truck", None, True),
        ("TRUCK-D", "Truck", drivers[3], True),
        ("TRUCK-E", "Truck", drivers[1], True),
        ("TRUCK-F", "Truck", drivers[2], True),
        ("TRAILER-A", "Trailer", None, True),
        ("TRAILER-B", "Trailer", None, True),
        ("ARCHIVED-TRUCK", "Truck", None, False),
        ("OTHER-CAR", "Other", None, True),
    ):
        vehicles[label] = create(
            "fleet.vehicle",
            label,
            {
                "model_id": models[model_kind].id,
                "category_id": models[model_kind].category_id.id,
                "license_plate": PREFIX + "-" + label,
                "company_id": company.id,
                "driver_id": driver.id if driver else False,
                "manager_id": False,
                "active": active,
            },
        )

    routes = {}
    for key, checkpoints in (
        ("A", (("Loading", 0), ("Border", 12), ("Delivery", 24))),
        ("B", (("Loading", 0), ("Delivery", 6))),
    ):
        routes[key] = create(
            "taj.transport.route",
            "Route " + key,
            {
                "name": PREFIX + " Route " + key,
                "company_id": company.id,
                "origin_location": PREFIX + " Origin",
                "destination_location": PREFIX + " Destination",
                "line_ids": [
                    Command.create(
                        {
                            "sequence": index * 10,
                            "name": PREFIX + " " + label,
                            "milestone_type_id": types[label].id,
                            "planned_offset_hours": hours,
                        }
                    )
                    for index, (label, hours) in enumerate(checkpoints, 1)
                ],
            },
        )

    term = create(
        "account.payment.term",
        "30 Day Terms",
        {
            "name": PREFIX + " 30 Days",
            "company_id": company.id,
            "line_ids": [
                Command.create({"value": "percent", "value_amount": 100, "nb_days": 30})
            ],
        },
    )
    pricelist = create(
        "product.pricelist",
        "Pricelist",
        {
            "name": PREFIX + " Pricelist",
            "company_id": company.id,
            "currency_id": company.currency_id.id,
        },
    )
    products = {}
    for label, name in (
        ("Route Product", PREFIX + " Origin - " + PREFIX + " Destination"),
        ("Plain Product", PREFIX + " Freight Service"),
        ("Ambiguous Product", PREFIX + " Origin - Transit - Destination"),
    ):
        products[label] = create(
            "product.product",
            label,
            {
                "name": name,
                "type": "service",
                "service_tracking": "no",
                "invoice_policy": "order",
                "sale_ok": True,
                "purchase_ok": False,
                "list_price": 1000,
                "company_id": company.id,
                "property_account_income_id": income.id,
                "taxes_id": [Command.set(tax.ids)],
                "supplier_taxes_id": [Command.clear()],
            },
        )

    def line(product, vehicle=None, qty=1, price=1000, metadata=True):
        values = {
            "product_id": product.id,
            "name": product.name,
            "product_uom_id": product.uom_id.id,
            "product_uom_qty": qty,
            "price_unit": price,
            "discount": 0,
            "vehicle_id": vehicle.id if vehicle else False,
            "tax_ids": [Command.set(tax.ids)],
        }
        if metadata:
            values.update(
                container_num="UAT-CONT-001",
                file_name=PREFIX + " Cargo File",
                srn=PREFIX + "-SRN-001",
                consignee=PREFIX + " Consignee",
                size="40ft",
                weight="24000 kg",
            )
        return Command.create(values)

    def order(label, lines, customer=None, incomplete=False):
        buyer = customer if customer is not None else customers["A"]
        record = create(
            "sale.order",
            label + " Order",
            {
                "partner_id": buyer.id,
                "partner_invoice_id": buyer.id,
                "partner_shipping_id": buyer.id,
                "company_id": company.id,
                "user_id": users["dispatcher"].id,
                "client_order_ref": PREFIX + "-" + label,
                "origin": False if incomplete else PREFIX + " Source " + label,
                "pricelist_id": pricelist.id,
                "payment_term_id": False if incomplete else term.id,
                "fiscal_position_id": False,
                "vat_tax_id": tax.id or False,
                "require_signature": False,
                "require_payment": False,
                "order_line": lines,
            },
        )
        manifest["quotations"].append(
            {
                "scenario": label,
                "id": record.id,
                "order": record.name,
                "reference": record.client_order_ref,
                "state": record.state,
                "untaxed": record.amount_untaxed,
                "tax": record.amount_tax,
                "total": record.amount_total,
                "currency": record.currency_id.name,
                "lines": [
                    {
                        "id": row.id,
                        "description": row.name,
                        "display_type": row.display_type,
                        "quantity": row.product_uom_qty,
                        "unit_price": row.price_unit,
                        "vehicle": row.vehicle_id.license_plate or None,
                    }
                    for row in record.order_line
                ],
            }
        )
        return record

    route_product = products["Route Product"]
    plain = products["Plain Product"]
    order("MAIN", [line(route_product, vehicles["TRUCK-A"], qty=3)])
    order("PLAN", [line(plain, vehicles["TRUCK-C"])])
    order(
        "NO-VEHICLE",
        [line(plain), line(plain, price=1250, metadata=False)],
        incomplete=True,
    )
    order(
        "MIXED",
        [
            Command.create(
                {"display_type": "line_section", "name": PREFIX + " Transport"}
            ),
            line(route_product, vehicles["TRUCK-C"]),
            line(plain, price=1250),
            Command.create(
                {"display_type": "line_note", "name": "UAT note: not a trip line."}
            ),
        ],
    )
    order("FILL-EMPTY", [line(plain, metadata=False)], incomplete=True)
    order("AMBIGUOUS", [line(products["Ambiguous Product"], metadata=False)])
    order("QUOTE-ONLY", [line(plain)])
    for label in ("CANCEL-D", "CANCEL-R", "CANCEL-P"):
        order(label, [line(plain, vehicles["TRUCK-D"])])
    order("REPORT-A", [line(route_product, vehicles["TRUCK-E"])])
    order(
        "REPORT-B", [line(route_product, vehicles["TRUCK-F"])], customer=customers["B"]
    )

    for key in ("A", "B"):
        tracked = [vehicles["TRUCK-" + ("E" if key == "A" else "F")].id]
        if key == "A":
            tracked += [vehicles["TRUCK-A"].id, vehicles["TRUCK-B"].id]
        create(
            "fleet.navirec.report.subscription",
            "Report " + key,
            {
                "name": PREFIX + " Report " + key,
                "active": False,
                "partner_id": customers[key].id,
                "vehicle_ids": [Command.set(tracked)],
                "email_to": "uat-review@example.invalid",
                "email_cc": False,
                "timezone": UAT_TIMEZONE,
                "send_time_ids": [
                    Command.create({"time": "08:00"}),
                    Command.create({"time": "13:00"}),
                    Command.create({"time": "18:00"}),
                ],
            },
        )

    if foreign:
        foreign_env = seed["res.company"].browse(foreign.id).with_company(foreign).env
        buyer = partner("Foreign Customer", True, foreign)
        driver = partner("Foreign Driver", partner_company=foreign)
        vehicle = create(
            "fleet.vehicle",
            "FOREIGN-TRUCK",
            {
                "model_id": models["Truck"].id,
                "category_id": truck_category.id,
                "license_plate": PREFIX + "-FOREIGN-TRUCK",
                "company_id": foreign.id,
                "driver_id": driver.id,
                "manager_id": False,
            },
            foreign_env,
        )
        create(
            "taj.transport.route",
            "Foreign Route",
            {
                "name": PREFIX + " Foreign Route",
                "company_id": foreign.id,
                "origin_location": "Foreign UAT Origin",
                "destination_location": "Foreign UAT Destination",
                "line_ids": [
                    Command.create({"name": PREFIX + " Foreign Stop", "sequence": 10})
                ],
            },
            foreign_env,
        )
        product = create(
            "product.product",
            "Foreign Product",
            {
                "name": PREFIX + " Foreign Service",
                "company_id": foreign.id,
                "type": "service",
                "service_tracking": "no",
                "invoice_policy": "order",
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            },
            foreign_env,
        )
        foreign_list = create(
            "product.pricelist",
            "Foreign Pricelist",
            {
                "name": PREFIX + " Foreign Pricelist",
                "company_id": foreign.id,
                "currency_id": foreign.currency_id.id,
            },
            foreign_env,
        )
        create(
            "sale.order",
            "FOREIGN Order",
            {
                "partner_id": buyer.id,
                "partner_invoice_id": buyer.id,
                "partner_shipping_id": buyer.id,
                "company_id": foreign.id,
                "client_order_ref": PREFIX + "-FOREIGN",
                "pricelist_id": foreign_list.id,
                "vat_tax_id": False,
                "fiscal_position_id": False,
                "require_signature": False,
                "require_payment": False,
                "order_line": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "name": product.name,
                            "product_uom_id": product.uom_id.id,
                            "product_uom_qty": 1,
                            "price_unit": 1000,
                            "discount": 0,
                            "tax_ids": [Command.clear()],
                            "vehicle_id": vehicle.id,
                        }
                    )
                ],
            },
            foreign_env,
        )

    seed.flush_all()
    order_ids = [
        record["id"]
        for record in manifest["records"].values()
        if record["model"] == "sale.order"
    ]
    if seed["taj.transport.trip"].search_count([("sale_order_id", "in", order_ids)]):
        raise RuntimeError(
            "Unexpected trip creation during data preparation; rolling back."
        )
    orders = seed["sale.order"].browse(order_ids)
    if any(order.state != "draft" for order in orders):
        raise RuntimeError("Expected only draft quotations; rolling back.")
    if orders.invoice_ids:
        raise RuntimeError(
            "Unexpected invoicing during data preparation; rolling back."
        )
    t0 = fields.Datetime.now().replace(microsecond=0)
    moments = {
        "planned_departure": t0 - timedelta(hours=2),
        "planned_arrival": t0 + timedelta(days=2),
        "initial_assignment": t0 - timedelta(minutes=60),
        "actual_departure": t0 - timedelta(minutes=50),
        "loading_arrival": t0 - timedelta(minutes=45),
        "loading_departure": t0 - timedelta(minutes=40),
        "border_arrival": t0 - timedelta(minutes=30),
        "border_departure": t0 - timedelta(minutes=25),
        "replacement": t0 - timedelta(minutes=20),
        "delivery_arrival": t0 - timedelta(minutes=10),
        "delivery_departure": t0 - timedelta(minutes=5),
    }
    manifest["sample_times_local"] = {
        name: value.replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo(UAT_TIMEZONE))
        .strftime("%Y-%m-%d %H:%M:%S")
        for name, value in moments.items()
    }
    manifest["tax"] = {
        "id": tax.id or None,
        "name": tax.display_name if tax else None,
        "percentage": tax.amount if tax else 0.0,
    }
    manifest["next_steps"] = [
        "Set passwords manually for the two UAT users in Settings > Users. No password was generated or emailed.",
        "Find quotations by Customer Reference starting with " + PREFIX + ".",
        "Confirm and create trips yourself. Use A then B for MAIN, C for PLAN, D for auxiliary cases sequentially, E/F for reports.",
        "Reports are inactive and use a non-deliverable .invalid address. Use your own test recipient before any delivery case.",
        "Archive UAT users and finish/cancel UAT trips after testing; do not delete assignment history.",
    ]
    return manifest


try:
    result = prepare(env)
    if result is None:
        env.cr.rollback()
    else:
        env.cr.commit()
        print("\nSAVED UAT DATA. Copy the manifest below into your UAT notes.")
        print(json.dumps(result, indent=2, ensure_ascii=False))
except BaseException:
    env.cr.rollback()
    print("\nNO DATA FROM THIS RUN WAS COMMITTED.")
    raise
