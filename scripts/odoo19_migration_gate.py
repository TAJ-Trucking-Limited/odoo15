"""Rollback-safe Odoo 19 migration gate for TAJ Trucking.

Run only on an Odoo.sh development or staging build:

    odoo-bin shell -d "$PGDATABASE" < ~/src/user/scripts/odoo19_migration_gate.py

The script deliberately tests a database uniqueness violation, so one expected
ERROR log entry is printed. All transactional test records are rolled back.
PostgreSQL row-ID sequences may still advance.
"""

from contextlib import contextmanager
from io import BytesIO
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.addons.send_report_via_mail.models.send_mail import DETAILS


if "production" in env.cr.dbname.lower():
    raise RuntimeError(
        "Refusing to run the migration gate on a production database."
    )


TOKEN = uuid4().hex[:8].upper()
MARKER = f"TAJ-SMOKE-{TOKEN}"
PASSES = []
ARTIFACTS = {}


def ok(condition, message):
    if not condition:
        raise AssertionError(f"FAIL: {message}")
    PASSES.append(message)
    print(f"  PASS: {message}")


@contextmanager
def step(number, title):
    label = f"{number}/9 - {title}"
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    try:
        with env.cr.savepoint():
            yield
    except Exception as error:
        print(f"  FAIL: {type(error).__name__}: {error}")
        raise
    print(f"STEP PASS: {label}")


def expected_error(types, callback, message, text=None):
    try:
        with env.cr.savepoint():
            callback()
    except types as error:
        ok(not text or text.lower() in str(error).lower(), message)
        return error
    raise AssertionError(f"FAIL: {message}: no exception was raised")


def pdf(xml_id, records):
    content, kind = env["ir.actions.report"]._render_qweb_pdf(
        xml_id,
        res_ids=records.ids,
    )
    ok(kind == "pdf", f"{xml_id}: report type is PDF")
    ok(isinstance(content, (bytes, bytearray)), f"{xml_id}: returned bytes")
    ok(bytes(content).startswith(b"%PDF"), f"{xml_id}: valid PDF signature")
    ok(len(content) > 100, f"{xml_id}: non-empty ({len(content)} bytes)")
    ARTIFACTS[xml_id] = len(content)


def cargo_matches(line, order, route, vehicle, values, label):
    expected = {
        "vehicle_id": vehicle,
        "container_num": values["container_num"],
        "file_name": values["file_name"],
        "consignee": values["consignee"],
        "weight": values["weight"],
        "size": values["size"],
        "srn": values["srn"],
        "order_id": order,
        "route_id": route,
    }
    for field_name, expected_value in expected.items():
        ok(
            getattr(line, field_name) == expected_value,
            f"{label}: {field_name}",
        )


mail_before = env["mail.mail"].sudo().search_count([])
completed = False

try:
    print("\n" + "#" * 72)
    print("TAJ TRUCKING - ODOO 19 COMPLETE MIGRATION GATE")
    print(
        f"DB={env.cr.dbname} | "
        f"company={env.company.display_name} | "
        f"token={TOKEN}"
    )
    print("DEV/STAGING ONLY: no SMTP, no posting, no retained test records")
    print("#" * 72)

    with step(1, "Installation, registry, schema and XML IDs"):
        module_names = {
            "account_cheque_printing",
            "move_invoice_line",
            "send_report_via_mail",
            "report_xlsx",
        }
        modules = env["ir.module.module"].search([
            ("name", "in", list(module_names)),
        ])
        states = {
            module.name: module.state
            for module in modules
        }
        ok(
            states == {
                name: "installed"
                for name in module_names
            },
            f"modules installed: {states}",
        )

        cron = env.ref("send_report_via_mail.report_cron")
        ok(
            not cron.active,
            "aged-report cron disabled before smoke test",
        )

        models = (
            "account.cheque",
            "account.cheque.wizard",
            "account.payment",
            "sale.order.line",
            "purchase.order",
            "purchase.order.line",
            "account.move.line",
            "report.send.mail",
            "report.trip.profit",
            "report.report_xlsx.abstract",
            "report.send_report_via_mail.profit_loss_report",
        )
        for model_name in models:
            ok(
                env.get(model_name) is not None,
                f"model registered: {model_name}",
            )

        contracts = {
            "sale.order.line": {
                "vehicle_id",
                "container_num",
                "file_name",
                "consignee",
                "weight",
                "size",
                "srn",
            },
            "account.move.line": {
                "vehicle_id",
                "container_num",
                "file_name",
                "consignee",
                "weight",
                "size",
                "srn",
                "order_id",
                "route_id",
            },
            "purchase.order": {
                "sale_order_id",
            },
            "purchase.order.line": {
                "source_sale_line_id",
                "truck_number",
                "cargo_type",
                "rout",
            },
            "account.payment": {
                "cheque_id",
                "cheque_number",
                "cheque_reason_note",
                "cheque_printed",
                "cheque_print_count",
                "cheque_amount",
                "cheque_payee_name",
            },
        }
        for model_name, names in contracts.items():
            missing = names - set(env[model_name]._fields)
            ok(
                not missing,
                f"{model_name}: all custom fields present",
            )

        xml_ids = (
            "account_cheque_printing.report_cheque_pdf_action",
            "account_cheque_printing.report_voucher_pdf",
            "account_cheque_printing.report_cheque_preview",
            "account_cheque_printing.paperformat_cheque",
            "account_cheque_printing.action_open_cheque_wizard",
            "send_report_via_mail.report_profit_and_loss_excel",
            "send_report_via_mail.report_cron",
            "account_reports.aged_payable_report",
            "account_reports.aged_receivable_report",
            "account.account_invoices",
            "purchase.action_report_purchase_order",
        )
        for xml_id in xml_ids:
            ok(
                bool(env.ref(xml_id, raise_if_not_found=False)),
                f"XML ID: {xml_id}",
            )

        custom_view_data = env["ir.model.data"].sudo().search([
            ("module", "in", list(module_names)),
            ("model", "=", "ir.ui.view"),
        ])
        custom_views = env["ir.ui.view"].sudo().browse(
            custom_view_data.mapped("res_id")
        ).exists()
        inactive_views = custom_views.filtered(
            lambda custom_view: not custom_view.active
        )
        ok(
            len(custom_views) == len(custom_view_data)
            and not inactive_views,
            "all custom XML views exist and remain active",
        )

    with step(2, "Controller and report registrations"):
        from odoo.addons.account_cheque_printing.controllers.cheque_preview_controller import (
            ChequePreviewController,
        )
        from odoo.addons.report_xlsx.controllers.main import (
            ReportController as XlsxController,
        )

        ok(
            callable(
                getattr(
                    ChequePreviewController,
                    "cheque_preview",
                    None,
                )
            ),
            "cheque preview handler exists",
        )

        routing = getattr(
            ChequePreviewController.cheque_preview,
            "original_routing",
            getattr(
                ChequePreviewController.cheque_preview,
                "routing",
                {},
            ),
        )
        ok(
            "/cheque/preview/<int:wizard_id>"
            in routing.get("routes", [])
            and routing.get("auth") == "user",
            "cheque preview handler is routed for authenticated users",
        )

        ok(
            callable(
                getattr(
                    XlsxController,
                    "report_routes",
                    None,
                )
            ),
            "XLSX report route exists",
        )
        ok(
            callable(
                getattr(
                    XlsxController,
                    "report_download",
                    None,
                )
            ),
            "XLSX download route exists",
        )

        xlsx_action = env.ref(
            "send_report_via_mail.report_profit_and_loss_excel"
        )
        ok(
            xlsx_action.model == "report.send.mail",
            "profit action model",
        )
        ok(
            xlsx_action.report_type == "xlsx",
            "profit action type",
        )
        ok(
            xlsx_action.report_name
            == "send_report_via_mail.profit_loss_report",
            "profit action report_name",
        )
        ok(
            env["ir.actions.report"]._get_report_from_name(
                xlsx_action.report_name
            ) == xlsx_action,
            "profit action resolves",
        )

        move_view = env.ref(
            "move_invoice_line.view_move_form_madfox_17"
        )
        move_form = ElementTree.fromstring(
            move_view.get_combined_arch()
        )
        cargo_fields = {
            "order_id",
            "route_id",
            "vehicle_id",
            "analytic_account_id",
            "file_name",
            "container_num",
            "consignee",
            "size",
            "weight",
            "srn",
        }
        move_line_lists = {
            "invoice lines": (
                ".//page[@name='invoice_tab']"
                "/field[@name='invoice_line_ids']/list"
            ),
            "journal items": (
                ".//page[@name='aml_tab']"
                "/field[@name='line_ids']/list"
            ),
        }
        for label, xpath in move_line_lists.items():
            lists = move_form.findall(xpath)
            visible_fields = (
                {
                    field.get("name")
                    for field in lists[0].findall("./field")
                }
                if len(lists) == 1
                else set()
            )
            ok(
                len(lists) == 1
                and cargo_fields.issubset(visible_fields),
                f"move form exposes cargo fields on {label}",
            )

        manual_cheque_menu = env.ref(
            "account_cheque_printing.menu_account_cheque_wizard"
        )
        ok(
            manual_cheque_menu.group_ids
            == env.ref("account.group_account_user"),
            "manual cheque menu remains restricted to accountants",
        )

        embedded_list_contracts = (
            (
                "move_invoice_line.view_sale_form_madfox",
                ".//field[@name='order_line']/list",
                {
                    "vehicle_id",
                    "analytic_account_id",
                    "file_name",
                    "container_num",
                    "srn",
                    "consignee",
                    "size",
                    "weight",
                },
                "sale-order cargo columns",
            ),
            (
                "move_invoice_line.view_purchase_order_form",
                ".//field[@name='order_line']/list",
                {"truck_number", "cargo_type", "rout"},
                "purchase-order cargo columns",
            ),
        )
        for xml_id, xpath, fields, label in embedded_list_contracts:
            root = ElementTree.fromstring(
                env.ref(xml_id).get_combined_arch()
            )
            lists = root.findall(xpath)
            present_fields = (
                {
                    field.get("name")
                    for field in lists[0].findall("./field")
                }
                if len(lists) == 1
                else set()
            )
            ok(
                len(lists) == 1 and fields.issubset(present_fields),
                f"compiled view retains {label}",
            )

        purchase_form = ElementTree.fromstring(
            env.ref(
                "move_invoice_line.view_purchase_order_form"
            ).get_combined_arch()
        )
        ok(
            bool(purchase_form.findall(".//field[@name='sale_order_id']")),
            "purchase form retains sale-order link",
        )

        partner_form = ElementTree.fromstring(
            env.ref(
                "move_invoice_line.view_partner_form"
            ).get_combined_arch()
        )
        ok(
            bool(partner_form.findall(".//field[@name='vrn']")),
            "partner form retains VRN field",
        )

        asset_form = ElementTree.fromstring(
            env.ref(
                "move_invoice_line.view_account_asset_inherit_form"
            ).get_combined_arch()
        )
        base_asset_form = ElementTree.fromstring(
            env.ref(
                "account_asset.view_account_asset_form"
            ).with_context(lang=None).arch_db
        )
        asset_field_paths = (
            (
                ".//group[@invisible=\"state != 'model'\"]"
                "//field[@name='account_depreciation_expense_id']"
            ),
            (
                ".//notebook[@invisible=\"state == 'model'\"]"
                "//field[@name='account_depreciation_expense_id']"
            ),
        )
        asset_contracts = []
        for xpath in asset_field_paths:
            expected_fields = base_asset_form.findall(xpath)
            expense_fields = asset_form.findall(xpath)
            asset_contracts.append(
                len(expected_fields) == 1
                and len(expense_fields) == 1
                and expense_fields[0].get("required") == "1"
                and expense_fields[0].get("readonly") == "state == 'close'"
                and all(
                    expense_fields[0].get(attribute)
                    == expected_fields[0].get(attribute)
                    for attribute in ("domain", "context")
                )
            )
        ok(
            all(asset_contracts),
            "asset form retains required expense accounts and modifiers",
        )

        payment_form = ElementTree.fromstring(
            env.ref(
                "account.view_account_payment_form"
            ).get_combined_arch()
        )
        payment_buttons = payment_form.findall(".//button")
        cheque_action_id = str(env.ref(
            "account_cheque_printing.action_open_cheque_wizard"
        ).id)
        ok(
            len([
                button
                for button in payment_buttons
                if button.get("name") == cheque_action_id
                and button.get("string") == "Print Cheque"
            ]) == 1
            and not [
                button
                for button in payment_buttons
                if button.get("name") == "print_checks"
            ],
            "payment form retains only the custom cheque button",
        )

        profit_form = ElementTree.fromstring(
            env.ref(
                "send_report_via_mail.send_pdf_report_form"
            ).get_combined_arch()
        )
        profit_buttons = {
            button.get("name")
            for button in profit_form.findall(".//button")
        }
        ok(
            {"send_report", "view_report"}.issubset(profit_buttons),
            "profit report form retains both actions",
        )

    with step(3, "Safe fixtures"):
        company = env.company

        for journal_type in ("sale", "purchase"):
            ok(
                bool(
                    env["account.journal"].search([
                        ("company_id", "=", company.id),
                        ("type", "=", journal_type),
                    ], limit=1)
                ),
                f"{journal_type} journal exists",
            )

        bank = env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "bank"),
            ("outbound_payment_method_line_ids", "!=", False),
        ], limit=1)
        ok(
            bool(bank),
            "bank journal with outbound method exists",
        )

        partner = env["res.partner"].create({
            "name": MARKER,
            "email": f"{TOKEN.lower()}@example.invalid",
            "customer_rank": 1,
            "supplier_rank": 1,
            "vrn": f"VRN-{TOKEN}",
        })
        payee = env["res.partner"].create({
            "name": f"{MARKER}-PAYEE",
        })

        ok(
            bool(partner.property_account_receivable_id),
            "partner receivable account",
        )
        ok(
            bool(partner.property_account_payable_id),
            "partner payable account",
        )

        brand = env["fleet.vehicle.model.brand"].create({
            "name": f"{MARKER}-BRAND",
        })
        vehicle_model = env["fleet.vehicle.model"].create({
            "name": f"{MARKER}-MODEL",
            "brand_id": brand.id,
        })
        vehicle = env["fleet.vehicle"].create({
            "model_id": vehicle_model.id,
            "license_plate": f"SMK-{TOKEN[:6]}",
        })

        product = env["product.product"].create({
            "name": f"Dar Smoke Route {TOKEN}",
            "type": "service",
            "sale_ok": True,
            "purchase_ok": True,
            "service_tracking": "no",
            "invoice_policy": "order",
            "purchase_method": "purchase",
            "list_price": 3500.0,
            "standard_price": 1500.0,
            "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
        })
        ok(
            product.invoice_policy == "order",
            "product invoices ordered quantity",
        )
        ok(
            product.purchase_method == "purchase",
            "product bills ordered quantity",
        )

        cargo = {
            "vehicle_id": vehicle.id,
            "container_num": f"CONT-{TOKEN}",
            "file_name": f"FILE-{TOKEN}",
            "consignee": f"CONSIGNEE-{TOKEN}",
            "weight": "30 Tons",
            "size": "40FT",
            "srn": f"SRN-{TOKEN}",
        }

    with step(4, "SO -> customer invoice propagation and PDF"):
        so = env["sale.order"].create({
            "name": f"SMOKE-SO-{TOKEN}",
            "partner_id": partner.id,
            "company_id": company.id,
        })
        env["sale.order.line"].create({
            "order_id": so.id,
            "display_type": "line_section",
            "name": "Cargo",
        })
        sol = env["sale.order.line"].create({
            "order_id": so.id,
            "product_id": product.id,
            "name": "Dar es Salaam - Mbeya Transit Route",
            "product_uom_qty": 1.0,
            "price_unit": 3500.0,
            **cargo,
        })

        so.with_context(send_email=False).action_confirm()
        ok(
            so.state == "sale",
            "sale order confirmed",
        )
        ok(
            sol.qty_to_invoice == 1.0,
            "sale line immediately invoiceable",
        )

        invoice = so._create_invoices(grouped=True)
        ok(
            len(invoice) == 1 and invoice.state == "draft",
            "one draft customer invoice",
        )

        inv_line = invoice.invoice_line_ids.filtered(
            lambda line:
                line.display_type == "product"
                and line.product_id == product
        )
        ok(
            len(inv_line) == 1,
            "one invoice product line",
        )
        cargo_matches(
            inv_line,
            so,
            sol,
            vehicle,
            cargo,
            "invoice",
        )

        section_line = invoice.invoice_line_ids.filtered(
            lambda line:
                line.display_type == "line_section"
        )
        ok(
            bool(section_line)
            and not section_line.route_id
            and not section_line.container_num,
            "section remains cargo-free",
        )

        pdf(
            "account.account_invoices",
            invoice,
        )

    with step(5, "PO -> vendor bill, source guards and PDF"):
        po = env["purchase.order"].create({
            "name": f"SMOKE-PO-{TOKEN}",
            "partner_id": partner.id,
            "company_id": company.id,
            "sale_order_id": so.id,
        })
        pol = env["purchase.order.line"].create({
            "order_id": po.id,
            "product_id": product.id,
            "product_qty": 1.0,
            "price_unit": 1500.0,
        })

        ok(
            pol.source_sale_line_id == sol,
            "PO resolves sole SO product line",
        )

        po.button_confirm()
        if po.state == "to approve":
            po.button_approve()

        ok(
            po.state == "purchase"
            and pol.qty_to_invoice == 1.0,
            "PO confirmed and billable",
        )

        po.action_create_invoice()
        po.invalidate_recordset(["invoice_ids"])
        bill = po.invoice_ids

        ok(
            len(bill) == 1
            and bill.state == "draft",
            "one draft vendor bill",
        )

        bill_line = bill.invoice_line_ids.filtered(
            lambda line:
                line.display_type == "product"
                and line.product_id == product
        )
        ok(
            len(bill_line) == 1,
            "one vendor bill product line",
        )
        cargo_matches(
            bill_line,
            so,
            sol,
            vehicle,
            cargo,
            "bill",
        )

        pdf(
            "purchase.action_report_purchase_order",
            po,
        )

        ambiguous_so = env["sale.order"].create({
            "name": f"SMOKE-SO-AMB-{TOKEN}",
            "partner_id": partner.id,
        })
        for suffix in ("A", "B"):
            env["sale.order.line"].create({
                "order_id": ambiguous_so.id,
                "product_id": product.id,
                "product_uom_qty": 1.0,
                "price_unit": 100.0,
                "container_num": f"AMB-{suffix}-{TOKEN}",
            })

        ambiguous_po = env["purchase.order"].create({
            "name": f"SMOKE-PO-AMB-{TOKEN}",
            "partner_id": partner.id,
            "sale_order_id": ambiguous_so.id,
        })
        ambiguous_pol = env["purchase.order.line"].create({
            "order_id": ambiguous_po.id,
            "product_id": product.id,
            "product_qty": 1.0,
            "price_unit": 50.0,
        })

        ok(
            not ambiguous_pol.source_sale_line_id,
            "ambiguous display source stays blank",
        )
        expected_error(
            UserError,
            lambda:
                ambiguous_pol._prepare_account_move_line(),
            "ambiguous bill source is blocked",
            "multiple product lines",
        )

    with step(6, "Cheque lifecycle, uniqueness, reprint and PDFs"):
        currency = company.currency_id
        manual_no = f"MANUAL-{TOKEN}"

        manual_wizard = env["account.cheque.wizard"].create({
            "cheque_number": manual_no,
            "payee_id": partner.id,
            "amount": 123.45,
            "currency_id": currency.id,
            "reason_note": "Rollback-safe manual cheque",
        })
        action = (
            manual_wizard.action_print_cheque_and_voucher()
        )
        manual = env["account.cheque"].browse(
            action["params"]["cheque_id"]
        )

        ok(
            action["tag"] == "print_cheque_action",
            "manual cheque client action",
        )
        ok(
            manual.name == f"CH/{manual_no}",
            "manual reference generated",
        )
        ok(
            manual.cheque_type == "manual_cheque"
            and manual.print_count == 1,
            "manual cheque printed once",
        )

        def duplicate():
            env["account.cheque"].create({
                "cheque_number": manual_no,
                "payee_id": partner.id,
                "amount": 1.0,
                "currency_id": currency.id,
                "reason_note": "Expected duplicate",
                "amount_in_words": "One Only",
            })

        env.cr.execute("""
            SELECT pg_get_constraintdef(oid)
              FROM pg_constraint
             WHERE conrelid = 'account_cheque'::regclass
               AND contype = 'u'
        """)
        unique_definitions = [
            row[0]
            for row in env.cr.fetchall()
        ]
        ok(
            any(
                "cheque_number" in definition
                for definition in unique_definitions
            ),
            "database UNIQUE constraint covers cheque_number",
        )

        duplicate_error = expected_error(
            IntegrityError,
            duplicate,
            "duplicate cheque rejected without poisoning transaction",
        )
        ok(
            duplicate_error.pgcode == "23505",
            "duplicate rejected by PostgreSQL unique violation",
        )

        expected_error(
            UserError,
            lambda:
                env["account.cheque.wizard"].create({
                    "cheque_number": manual_no,
                    "payee_id": partner.id,
                    "amount": 1.0,
                    "currency_id": currency.id,
                    "reason_note": "Expected duplicate",
                }).action_print_cheque_and_voucher(),
            "wizard duplicate guard",
            "already exists",
        )

        payment_currency = (
            bank.currency_id
            or company.currency_id
        )
        payment = env["account.payment"].create({
            "payment_type": "outbound",
            "partner_type": "supplier",
            "partner_id": partner.id,
            "amount": 200.0,
            "currency_id": payment_currency.id,
            "journal_id": bank.id,
        })

        values = {
            "payment_id": payment.id,
            "cheque_number": f"PAY-{TOKEN}",
            "payee_id": payee.id,
            "amount": payment.amount,
            "currency_id": payment.currency_id.id,
            "reason_note": "Rollback-safe payment cheque",
        }

        payment_action = (
            env["account.cheque.wizard"]
            .create(values)
            .action_print_cheque_and_voucher()
        )
        cheque = env["account.cheque"].browse(
            payment_action["params"]["cheque_id"]
        )

        ok(
            payment.cheque_id == cheque
            and cheque.payment_id == payment,
            "payment/cheque links",
        )
        ok(
            cheque.journal_id == bank
            and cheque.cheque_type == "payment_cheque",
            "payment cheque metadata",
        )
        ok(
            payment.cheque_printed
            and payment.cheque_print_count == 1,
            "payment print tracking",
        )
        ok(
            bool(cheque.partner_comparison_note),
            "changed-payee comparison note",
        )

        (
            env["account.cheque.wizard"]
            .create(values)
            .action_print_cheque_and_voucher()
        )
        ok(
            cheque.print_count == 2
            and payment.cheque_print_count == 2,
            "exact-data reprint counters",
        )

        changed = dict(
            values,
            reason_note="Changed without new number",
        )
        expected_error(
            UserError,
            lambda:
                env["account.cheque.wizard"]
                .create(changed)
                .action_print_cheque_and_voucher(),
            "changed cheque data requires new number",
            "new cheque number",
        )

        preview = manual_wizard.action_preview()
        ok(
            preview["url"]
            == f"/cheque/preview/{manual_wizard.id}",
            "preview URL",
        )

        pdf(
            "account_cheque_printing.report_cheque_pdf_action",
            cheque,
        )
        pdf(
            "account_cheque_printing.report_voucher_pdf",
            cheque,
        )

    with step(7, "Real custom XLSX generation"):
        wizard = env["report.send.mail"].create({})

        row = {
            report_key: 0.0
            for _code, report_key, _field in DETAILS
        }
        row.update({
            "license_plate": vehicle.license_plate,
            "order_name": so.name,
            "root": product.display_name,
            "trip": "Going",
            "size": "40FT",
            "date": str(fields.Date.today()),
            "operating_income": 100.0,
            "total_going": 10.0,
            "total_fuel": 20.0,
            "total_return": 0.0,
            "expenses": 30.0,
            "cross_profit": 70.0,
        })

        content, kind = env["ir.actions.report"]._render(
            xlsx_action,
            wizard.ids,
            {"products": [row]},
        )
        ok(
            kind == "xlsx"
            and content.startswith(b"PK"),
            "XLSX type and ZIP signature",
        )
        ok(
            len(content) > 1000,
            f"XLSX non-empty ({len(content)} bytes)",
        )

        with ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            ok(
                "xl/workbook.xml" in names
                and "xl/worksheets/sheet1.xml" in names,
                "XLSX structure",
            )
            xml = b"".join(
                archive.read(name)
                for name in names
                if name.endswith(".xml")
            )
            ok(
                b"Operating Income" in xml
                and so.name.encode() in xml,
                "XLSX heading and data row",
            )

        ARTIFACTS["profitability_xlsx"] = len(content)

    with step(8, "Enterprise aged reports using the Odoo 19 API"):
        mailer = (
            env["report.send.mail"]
            .with_company(company)
        )
        config = mailer._get_mail_configuration()

        ok(
            bool(
                config["email_from"]
                and config["email_to"]
            ),
            "mail sender/recipient configured",
        )
        ok(
            cron.state == "code"
            and "send_email_with_pdf_attach" in cron.code,
            "cron method configuration",
        )
        ok(
            not cron.active,
            "cron remains disabled throughout gate",
        )

        for xml_id in (
            "account_reports.aged_payable_report",
            "account_reports.aged_receivable_report",
        ):
            report = env.ref(xml_id)

            ok(
                report._name == "account.report",
                f"{xml_id}: account.report",
            )

            options = report.get_options({})
            ok(
                isinstance(options, dict),
                f"{xml_id}: get_options({{}})",
            )

            exporter = getattr(
                mailer,
                "_export_aged_report",
                None,
            )
            result = (
                exporter(report)
                if callable(exporter)
                else report.dispatch_report_action(
                    options,
                    "export_to_pdf",
                )
            )

            ok(
                isinstance(result, dict),
                f"{xml_id}: PDF dispatch result",
            )

            aged_pdf = result.get("file_content")
            ok(
                isinstance(
                    aged_pdf,
                    (bytes, bytearray),
                ),
                f"{xml_id}: bytes",
            )
            ok(
                bytes(aged_pdf).startswith(b"%PDF"),
                f"{xml_id}: PDF signature",
            )
            ok(
                len(aged_pdf) > 100,
                f"{xml_id}: non-empty ({len(aged_pdf)} bytes)",
            )

            ARTIFACTS[xml_id] = len(aged_pdf)

        ok(
            env["mail.mail"].sudo().search_count([])
            == mail_before,
            "no email created or sent",
        )

    with step(9, "Final safety gates"):
        ok(
            invoice.state == "draft",
            "invoice not posted",
        )
        ok(
            bill.state == "draft",
            "bill not posted",
        )
        ok(
            payment.state == "draft",
            "payment not posted",
        )
        ok(
            so.name.startswith("SMOKE-SO-"),
            "SO sequence not consumed",
        )
        ok(
            po.name.startswith("SMOKE-PO-"),
            "PO sequence not consumed",
        )
        ok(
            env["mail.mail"].sudo().search_count([])
            == mail_before,
            "mail queue unchanged",
        )
        ok(
            bool(ARTIFACTS),
            "binary reports generated in memory",
        )

    completed = True

finally:
    env.cr.rollback()
    print(
        "\nROLLBACK COMPLETE: "
        "transactional test records were discarded."
    )
    print(
        "Note: PostgreSQL row-ID sequences may advance; "
        "run only on dev/staging."
    )

if completed:
    ok(
        not env["res.partner"].search_count([
            ("name", "=", MARKER),
        ]),
        "marker absent after rollback",
    )

    print("\n" + "#" * 72)
    print("FULL ODOO 19 MIGRATION GATE: PASS")
    print(f"Checks passed: {len(PASSES)}")
    print(f"Artifact byte sizes: {ARTIFACTS}")
    print(
        "No email sent. No invoice/bill/payment posted. "
        "Ready for next step."
    )
    print("#" * 72)
