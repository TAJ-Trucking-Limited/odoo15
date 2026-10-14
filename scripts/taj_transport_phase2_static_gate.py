#!/usr/bin/env python3
"""Offline, read-only Phase 2 module preflight (no Odoo database required).

    python3 scripts/taj_transport_phase2_static_gate.py

Checks Python/XML syntax, manifest file references, model/view/ACL consistency,
required lifecycle actions and accounting isolation without network access.
This is NOT a substitute for actual Odoo.sh integration tests.
"""
import ast
import csv
from pathlib import Path
import sys

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "taj_transport_operations"


def run():
    manifest = ast.literal_eval((MODULE / "__manifest__.py").read_text())
    if manifest["version"] != "19.0.1.6.2":
        raise AssertionError("Unexpected module version: " + manifest["version"])
    expected_depends = {"fleet", "mail", "move_invoice_line", "sale_management"}
    assert expected_depends.issubset(set(manifest["depends"]))
    assert len(set(manifest["data"])) == len(manifest["data"])
    for relative in manifest["data"]:
        assert (MODULE / relative).is_file(), "Missing manifest file: " + relative

    models = {}
    pyfiles = list(MODULE.rglob("*.py"))
    xmlfiles = list(MODULE.rglob("*.xml"))
    for file in pyfiles:
        tree = ast.parse(file.read_text(), filename=str(file))
        for definition in tree.body:
            if not isinstance(definition, ast.ClassDef):
                continue
            model_name = next((
                field.value.value for field in definition.body
                if isinstance(field, ast.Assign)
                and len(field.targets) == 1
                and isinstance(field.targets[0], ast.Name)
                and field.targets[0].id == "_name"
                and isinstance(field.value, ast.Constant)
            ), None)
            if not model_name:
                continue
            models[model_name] = {
                "fields": {
                    field.targets[0].id for field in definition.body
                    if isinstance(field, ast.Assign)
                    and len(field.targets) == 1
                    and isinstance(field.targets[0], ast.Name)
                },
                "methods": {
                    method.name for method in definition.body
                    if isinstance(method, ast.FunctionDef)
                },
            }

    checked_fields, checked_buttons = 0, 0
    for file in xmlfiles:
        doc = etree.parse(str(file))
        for record in doc.xpath('//record[field[@name="arch"]]'):
            modelfield = record.xpath('./field[@name="model"]')
            if not modelfield or modelfield[0].text not in models:
                continue
            model = modelfield[0].text
            arch = record.xpath('./field[@name="arch"]/*')
            if not arch:
                continue
            for field in arch[0].xpath('.//field[@name]'):
                parent = field.getparent()
                is_assignment_subview = (
                    parent is not None and parent.tag == "list"
                    and parent.getparent() is not None
                    and parent.getparent().tag == "field"
                    and parent.getparent().get("name") == "assignment_ids"
                )
                owner = "taj.transport.assignment" if is_assignment_subview else model
                assert field.get("name") in models[owner]["fields"], (
                    "Bad view field", file, record.get("id"), owner,
                    field.get("name"),
                )
                checked_fields += 1
            for button in arch[0].xpath('.//button[@type="object"]'):
                assert button.get("name") in models[model]["methods"], (
                    "Missing server button", file, record.get("id"),
                    button.get("name"),
                )
                checked_buttons += 1

    with (MODULE / "security/ir.model.access.csv").open(newline="") as fp:
        access = list(csv.DictReader(fp))
    assert len(access) == len({item["id"] for item in access})
    for item in access:
        xmlid = item["model_id:id"]
        if xmlid.startswith("model_taj_transport_"):
            model = xmlid[6:].replace("_", ".")
            assert model in models, ("Invalid ACL model reference", xmlid)

    trip = models["taj.transport.trip"]
    actions = {
        "action_mark_ready", "action_start_trip", "action_complete_trip",
        "action_open_cancel_trip_wizard", "action_open_initial_assignment_wizard",
        "action_open_replace_assignment_wizard",
    }
    assert actions.issubset(trip["methods"])
    assert {"initial_vehicle_id", "sale_line_id", "sale_order_id", "payment_term_id"}.issubset(
        trip["fields"]
    )
    form = etree.parse(str(MODULE / "views/transport_trip_views.xml"))
    header = form.xpath("//record[@id='view_transport_trip_form']"
                        "/field[@name='arch']/form/header")[0]
    assert header.xpath("./field[@name='state'][@readonly='1']")
    assert len(header.xpath("./button[@name='action_open_cancel_trip_wizard']")) == 2
    assert header.xpath("./button[@name='action_open_cancel_trip_wizard']"
                        "[@groups='taj_transport_operations.group_transport_manager']")
    assert len(form.xpath("//record[@id='view_transport_trip_form']"
                         "/field[@name='arch']/form/sheet/notebook"
                         "/page[@name='trip_resources']")) == 1
    # Transport module should not mutate source sale.order/sale.order.line or
    # account.move/account.move.line: Sales and Accounting are authoritative.
    trip_file = (MODULE / "models/transport_trip.py").read_text()
    assert not any(token in trip_file for token in (
        "self.sale_order_id.write(", "self.sale_line_id.write(",
        "self.env['account.move'].create(", 'self.env["account.move"].create(',
    ))

    test_files = list((MODULE / "tests").glob("test_*.py"))
    total_tests = 0
    for file in test_files:
        tree = ast.parse(file.read_text(), filename=str(file))
        total_tests += sum(
            isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
            for klass in tree.body if isinstance(klass, ast.ClassDef)
            for item in klass.body
        )
    print("PASS transport module:", manifest["version"])
    print("PASS parsed Python/XML:", len(pyfiles), "/", len(xmlfiles))
    print("PASS view fields/buttons:", checked_fields, "/", checked_buttons)
    print("PASS ACL rows:", len(access))
    print("Defined Odoo test cases:", total_tests)
    print("NOTE: Runtime, migration, permissions and invoice behavior require Odoo.sh staging.")


if __name__ == "__main__":
    try:
        run()
    except Exception as err:
        print("FAILED:", repr(err), file=sys.stderr)
        sys.exit(1)
