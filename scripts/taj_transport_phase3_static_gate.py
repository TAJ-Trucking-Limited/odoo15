#!/usr/bin/env python3
"""Offline, read-only Phase 3 module preflight (no Odoo database required).

    python3 scripts/taj_transport_phase3_static_gate.py

Verifies the Phase 3 deliverables statically: manifest wiring, model/view/ACL
consistency, the manual checkpoint workflow surface, the audited correction
path, company record rules, the generation policy markers, the backend route
guards (active/company/access, Draft-only re-planning, the clear path) and the
deliberate absence of Phase 4 (GPS / event-driven automation) code.

This is NOT a substitute for the runtime tests:
    odoo -d <testdb> -u taj_transport_operations --test-enable \\
         --test-tags /taj_transport_operations
"""

import ast
import csv
from pathlib import Path
import sys

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "taj_transport_operations"
EXPECTED_VERSION = "19.0.1.8.1"

# Comodel of the one2many fields that embed an inline list in a parent view.
EMBEDDED_VIEWS = {
    "assignment_ids": "taj.transport.assignment",
    "milestone_ids": "taj.transport.milestone",
    "event_ids": "taj.transport.milestone.event",
    "line_ids": "taj.transport.route.line",
}


def _owner_model(element, fallback):
    """Model that owns an element of an inline (embedded) view."""
    parent = element.getparent()
    while parent is not None:
        if parent.tag == "field" and parent.get("name") in EMBEDDED_VIEWS:
            return EMBEDDED_VIEWS[parent.get("name")]
        parent = parent.getparent()
    return fallback


def _inherit_names(definition):
    """Models an ``_inherit``-only class extends, or None."""
    inherited = next(
        (
            assign.value
            for assign in definition.body
            if isinstance(assign, ast.Assign)
            and len(assign.targets) == 1
            and isinstance(assign.targets[0], ast.Name)
            and assign.targets[0].id == "_inherit"
        ),
        None,
    )
    if inherited is None:
        return None
    if isinstance(inherited, ast.Constant):
        return [inherited.value]
    return [element.value for element in inherited.elts]


PHASE3_FILES = {
    "models/transport_milestone_type.py",
    "models/transport_route.py",
    "models/transport_milestone.py",
    "models/transport_trip_milestones.py",
    "data/milestone_types.xml",
    "views/transport_route_views.xml",
    "views/transport_milestone_type_views.xml",
    "views/transport_milestone_views.xml",
    "wizard/trip_milestone_wizard_views.xml",
    "wizard/trip_milestone_action_wizard.py",
    "wizard/trip_milestone_add_wizard.py",
    "wizard/trip_milestone_correction_wizard.py",
    "wizard/regenerate_milestones_wizard.py",
    "tests/test_route_templates.py",
    "tests/test_trip_milestones.py",
}

MILESTONE_FIELDS = {
    "trip_id",
    "company_id",
    "route_id",
    "template_line_id",
    "sequence",
    "name",
    "milestone_type_id",
    "type_name",
    "requires_confirmation",
    "planned_offset_hours",
    "planned_arrival_at",
    "actual_arrival_at",
    "actual_departure_at",
    "arrived_by_id",
    "departed_by_id",
    "confirmed_at",
    "confirmed_by_id",
    "confirmation_note",
    "state",
    "notes",
    "event_ids",
    "event_count",
}
MILESTONE_METHODS = {
    "_record_arrival",
    "_record_departure",
    "_confirm_checkpoint",
    "_cancel_confirmation",
    "_skip_checkpoint",
    "_correct_timestamp",
    "action_open_arrival_wizard",
    "action_open_departure_wizard",
    "action_open_confirmation_wizard",
    "action_open_cancel_confirmation_wizard",
    "action_open_correction_wizard",
    "action_open_skip_wizard",
    "write",
    "unlink",
}
EVENT_FIELDS = {
    "milestone_id",
    "trip_id",
    "company_id",
    "event_type",
    "source",
    "occurred_at",
    "recorded_at",
    "user_id",
    "field_name",
    "old_value",
    "new_value",
    "reason",
    "note",
}
TRIP_MILESTONE_FIELDS = {
    "route_id",
    "generated_route_id",
    "milestone_ids",
    "milestone_count",
    "milestone_done_count",
    "milestone_open_count",
    "milestone_progress",
}
TRIP_MILESTONE_METHODS = {
    "_generate_milestones_from_route",
    "_regenerate_milestones_from_route",
    "_add_manual_milestone",
    "_has_recorded_milestone_progress",
    "action_generate_milestones",
    "action_open_regenerate_milestones_wizard",
    "action_open_add_milestone_wizard",
    "action_open_trip_milestones",
}


def parse_models(pyfiles):
    """Collect ``_name`` models with their field and method names."""
    models = {}
    extensions = []
    for file in pyfiles:
        tree = ast.parse(file.read_text(), filename=str(file))
        for definition in tree.body:
            if not isinstance(definition, ast.ClassDef):
                continue
            model_name = next(
                (
                    assign.value.value
                    for assign in definition.body
                    if isinstance(assign, ast.Assign)
                    and len(assign.targets) == 1
                    and isinstance(assign.targets[0], ast.Name)
                    and assign.targets[0].id == "_name"
                    and isinstance(assign.value, ast.Constant)
                ),
                None,
            )
            fields = {
                assign.targets[0].id
                for assign in definition.body
                if isinstance(assign, ast.Assign)
                and len(assign.targets) == 1
                and isinstance(assign.targets[0], ast.Name)
            }
            methods = {
                method.name
                for method in definition.body
                if isinstance(method, ast.FunctionDef)
            }
            if not model_name:
                # A class declared with ``_inherit`` only extends a model
                # from another file; models of other addons stay out of the
                # map so the view checks below still skip their views.
                names = _inherit_names(definition)
                if names:
                    extensions.append((names, fields, methods))
                continue
            entry = models.setdefault(model_name, {"fields": set(), "methods": set()})
            entry["fields"].update(fields)
            entry["methods"].update(methods)
    for names, fields, methods in extensions:
        for model_name in names:
            if model_name in models:
                models[model_name]["fields"].update(fields)
                models[model_name]["methods"].update(methods)
    return models


def check_views(xmlfiles, models):
    """Every view field exists on its model and every button has a method."""
    checked_fields = checked_buttons = 0
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
            for field in arch[0].xpath(".//field[@name]"):
                owner = _owner_model(field, model)
                assert field.get("name") in models[owner]["fields"], (
                    "Bad view field",
                    file,
                    record.get("id"),
                    owner,
                    field.get("name"),
                )
                checked_fields += 1
            for button in arch[0].xpath('.//button[@type="object"]'):
                owner = _owner_model(button, model)
                assert button.get("name") in models[owner]["methods"], (
                    "Missing server button",
                    file,
                    record.get("id"),
                    button.get("name"),
                )
                checked_buttons += 1
    return checked_fields, checked_buttons


def check_access():
    with (MODULE / "security/ir.model.access.csv").open(newline="") as fp:
        rows = {row["id"]: row for row in csv.DictReader(fp)}
    assert len(rows) == len(list(rows)), "Duplicate ACL ids"
    expected = {
        # Checkpoints are system-managed: nobody creates or deletes them by
        # hand, dispatchers only read and note them.
        "access_taj_transport_milestone_dispatcher": ("0", "0"),
        "access_taj_transport_milestone_manager": ("0", "0"),
        "access_taj_transport_milestone_event_dispatcher": ("0", "0"),
        "access_taj_transport_milestone_event_manager": ("0", "0"),
    }
    for acl_id, (create, unlink) in expected.items():
        assert acl_id in rows, "Missing ACL row: " + acl_id
        assert rows[acl_id]["perm_create"] == create, acl_id
        assert rows[acl_id]["perm_unlink"] == unlink, acl_id
    # Templates are manager configuration, dispatchers only read them.
    for acl_id, create in (
        ("access_taj_transport_route_dispatcher", "0"),
        ("access_taj_transport_route_manager", "1"),
        ("access_taj_transport_route_line_dispatcher", "0"),
        ("access_taj_transport_route_line_manager", "1"),
        ("access_taj_transport_milestone_type_dispatcher", "0"),
        ("access_taj_transport_milestone_type_manager", "1"),
        ("access_taj_transport_milestone_correction_wizard_manager", "1"),
    ):
        assert acl_id in rows, "Missing ACL row: " + acl_id
        assert rows[acl_id]["perm_create"] == create, acl_id
    assert "access_taj_transport_milestone_correction_wizard_dispatcher" not in rows
    return rows


def check_record_rules():
    xml = (MODULE / "security/transport_security.xml").read_text()
    for model in (
        "model_taj_transport_route",
        "model_taj_transport_route_line",
        "model_taj_transport_milestone",
        "model_taj_transport_milestone_event",
    ):
        assert model in xml, "Missing record rule for " + model
    assert xml.count("[('company_id', 'in', company_ids)]") >= 5
    assert "group_transport_manager" in xml


def check_generation_policy():
    trip_file = (MODULE / "models/transport_trip_milestones.py").read_text()
    milestone_file = (MODULE / "models/transport_milestone.py").read_text()
    route_file = (MODULE / "models/transport_route.py").read_text()
    # Trip checkpoints are a snapshot: templates never write to them.
    route_scope = route_file.replace("taj.transport.milestone.type", "")
    assert 'self.env["taj.transport.milestone"]' not in route_scope, (
        "Route templates must not write trip checkpoints"
    )
    assert "milestone_ids" not in route_scope, (
        "Route templates must not touch the checkpoints of a trip"
    )
    assert "replace_pending" in trip_file, "Missing regeneration scope control"
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in milestone_file and (
        "(trip_id, template_line_id)" in milestone_file
    ), "Missing partial unique index on (trip_id, template_line_id)"
    assert "_has_recorded_milestone_progress" in trip_file
    assert ".sudo()" in trip_file, "Generation must bypass the ACL-free create rule"
    assert 'DIRECT_WRITABLE_FIELDS = frozenset({"notes"})' in milestone_file, (
        "Milestone operational fields must not be directly writable"
    )
    assert "old_value" in milestone_file and "new_value" in milestone_file
    assert "reason" in milestone_file


def check_route_guards():
    """Backend guards added by the independent review pass.

    Issue 1: clearing a template removes its generated checkpoints and the
             generation marker together, and never discards history.
    Issue 2: selecting, switching, clearing, generating and regenerating are
             Draft-only decisions, refused by the model (not by the view).
    Issue 3: a template is applied only when it exists, is active, belongs to
             the trip company and is readable by the requesting user.
    """
    trip_file = (MODULE / "models/transport_trip_milestones.py").read_text()
    milestone_file = (MODULE / "models/transport_milestone.py").read_text()
    wizard_file = (MODULE / "wizard/regenerate_milestones_wizard.py").read_text()
    # Issue 3: one validator, used by every entry point.
    assert "_check_route_template_usable" in trip_file
    assert 'route.check_access("read")' in trip_file, "Missing read check"
    assert "if not route.active:" in trip_file, "Missing archived check"
    assert "route.company_id != self.company_id" in trip_file, "Missing company check"
    assert trip_file.count("_check_route_template_usable(") >= 4, (
        "Assignment, creation, generation and regeneration must validate"
    )
    # Issue 2: no destructive planning outside Draft, for any entry point.
    assert "_check_route_change_allowed" in trip_file
    assert trip_file.count('if self.state != "draft"') >= 2, (
        "Generation and replacement must both require Draft"
    )
    assert "_check_milestone_change_allowed" in trip_file
    assert 'if trip.state != "draft":' in wizard_file, (
        "The regeneration wizard must refuse non-Draft trips"
    )
    # Issue 1: clearing is atomic and keeps manual checkpoints.
    assert "_clear_generated_milestones" in trip_file
    assert '"generated_route_id": False' in trip_file, (
        "Clearing must reset the generation marker in the same transaction"
    )
    assert "MANUAL_MILESTONE_FIELDS" in trip_file, (
        "Manual checkpoints must accept a documented whitelist only"
    )
    assert "_milestone_audit_event_count" in trip_file, (
        "Audit entries must freeze the plan too"
    )
    assert "cannot be discarded" in milestone_file, (
        "Removal must refuse a checkpoint with history"
    )
    assert "Checkpoint notes cannot be changed on a completed or " in milestone_file
    # The interface hides what the backend refuses.
    views = (MODULE / "views/transport_trip_views.xml").read_text()
    assert views.count("state != 'draft'") >= 2, (
        "Generate and Regenerate buttons must be Draft-only"
    )


def check_no_phase4():
    """Phase 4 (GPS ingestion / event-driven automation) stays out of scope."""
    for file in MODULE.rglob("*"):
        if file.is_dir() or file.suffix not in {".py", ".xml", ".csv"}:
            continue
        text = file.read_text(errors="ignore")
        assert "ir.cron" not in text, "Phase 4 automation found in " + str(file)
        for token in (
            "import requests",
            "urllib",
            "http.client",
            "socket.",
            "webhook",
        ):
            assert token not in text, "%s uses %s" % (file, token)
    milestone_file = (MODULE / "models/transport_milestone.py").read_text()
    # Coordinates are manual, read-only data: nothing ingests them yet and
    # the GPS event source stays a documented placeholder.
    assert "latitude = fields.Float(digits=(10, 7), readonly=True)" in milestone_file
    assert "longitude = fields.Float(digits=(10, 7), readonly=True)" in milestone_file
    assert '("manual", "Manual")' in milestone_file
    assert "def _ingest" not in milestone_file
    # Every checkpoint movement writes an immutable audit event.
    milestone_file = (MODULE / "models/transport_milestone.py").read_text()
    assert milestone_file.count("self._log_event(") >= 5, (
        "Each checkpoint movement must append an audit event"
    )
    for movement in (
        "_record_arrival",
        "_record_departure",
        "_confirm_checkpoint",
        "_skip_checkpoint",
        "_correct_timestamp",
    ):
        body = milestone_file.split("def %s(" % movement, 1)[1]
        body = body.split("\n    def ", 1)[0]
        assert "_log_event(" in body, movement + " is not audited"


def check_confirmation_integrity():
    """A recorded confirmation never disappears without an explicit audit row."""
    milestone_file = (MODULE / "models/transport_milestone.py").read_text()
    assert '("confirmation_cancelled", "Confirmation Cancelled")' in milestone_file, (
        "The audit trail needs an event type for a withdrawn confirmation"
    )
    correction = milestone_file.split("def _correct_timestamp(", 1)[1]
    correction = correction.split("\n    def ", 1)[0]
    flat_correction = " ".join(correction.split())
    assert '"confirmed_at": False' not in correction, (
        "A timestamp correction must never clear a confirmation on its own"
    )
    assert "self._cancel_confirmation(" not in correction, (
        "A timestamp correction must not cancel a confirmation implicitly"
    )
    assert (
        "not new_value and self.confirmed_at" in flat_correction
        and "raise UserError(" in correction
    ), "Clearing the last event of a confirmed checkpoint must be refused"
    assert "savepoint()" in correction, (
        "A timestamp correction must be a single atomic change"
    )
    cancellation = milestone_file.split("def _cancel_confirmation(", 1)[1]
    cancellation = cancellation.split("\n    def ", 1)[0]
    for token in (
        "_check_manager(",
        "savepoint()",
        "_log_event(",
        '"confirmation_cancelled"',
        'reason = (reason or "").strip()',
        '"confirmed_at": False',
        "_recompute_state()",
    ):
        assert token in cancellation, "confirmation cancellation misses " + token
    assert "def action_open_cancel_confirmation_wizard(" in milestone_file, (
        "The cancellation needs a manager-only UI entry point"
    )
    wizard_file = (MODULE / "wizard/trip_milestone_action_wizard.py").read_text()
    assert '("cancel_confirmation", "Cancel Confirmation")' in wizard_file
    assert "_cancel_confirmation(self.reason, self.note)" in wizard_file
    wizard_views = (MODULE / "wizard/trip_milestone_wizard_views.xml").read_text()
    assert "action_type not in ('skip', 'cancel_confirmation')" in wizard_views


def count_tests():
    total = 0
    per_file = {}
    for file in sorted((MODULE / "tests").glob("test_*.py")):
        tree = ast.parse(file.read_text(), filename=str(file))
        count = sum(
            isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
            for klass in tree.body
            if isinstance(klass, ast.ClassDef)
            for item in klass.body
        )
        per_file[file.name] = count
        total += count
    return per_file, total


def run():
    manifest = ast.literal_eval((MODULE / "__manifest__.py").read_text())
    assert manifest["version"] == EXPECTED_VERSION, manifest["version"]
    assert {"fleet", "mail", "move_invoice_line", "sale_management"}.issubset(
        set(manifest["depends"])
    ), "Phase 2 dependencies must be preserved"
    assert len(set(manifest["data"])) == len(manifest["data"])
    for relative in manifest["data"]:
        assert (MODULE / relative).is_file(), "Missing manifest file: " + relative
    for relative in sorted(PHASE3_FILES):
        assert (MODULE / relative).is_file(), "Missing Phase 3 file: " + relative
        if relative.endswith(".xml") or relative.endswith(".py"):
            if relative.startswith(("models", "wizard", "tests")):
                continue
    for reference in (
        "data/milestone_types.xml",
        "views/transport_route_views.xml",
        "views/transport_milestone_type_views.xml",
        "views/transport_milestone_views.xml",
        "wizard/trip_milestone_wizard_views.xml",
    ):
        assert reference in manifest["data"], "Not loaded by the manifest: " + reference

    pyfiles = sorted(MODULE.rglob("*.py"))
    xmlfiles = sorted(MODULE.rglob("*.xml"))
    models = parse_models(pyfiles)
    assert MILESTONE_FIELDS.issubset(models["taj.transport.milestone"]["fields"])
    assert MILESTONE_METHODS.issubset(models["taj.transport.milestone"]["methods"])
    assert EVENT_FIELDS.issubset(models["taj.transport.milestone.event"]["fields"])
    assert {
        "taj.transport.route",
        "taj.transport.route.line",
        "taj.transport.milestone.type",
    }.issubset(models)
    assert TRIP_MILESTONE_FIELDS.issubset(models["taj.transport.trip"]["fields"])
    assert TRIP_MILESTONE_METHODS.issubset(models["taj.transport.trip"]["methods"])

    checked_fields, checked_buttons = check_views(xmlfiles, models)
    access = check_access()
    check_record_rules()
    check_generation_policy()
    check_route_guards()
    check_confirmation_integrity()
    check_no_phase4()

    trip_file = (MODULE / "models/transport_trip.py").read_text()
    assert not any(
        token in trip_file
        for token in (
            "self.sale_order_id.write(",
            "self.sale_line_id.write(",
            "self.env['account.move'].create(",
            'self.env["account.move"].create(',
        )
    ), "Phase 2 sales/accounting isolation must be preserved"

    trip_form = etree.parse(str(MODULE / "views/transport_trip_views.xml"))
    assert (
        len(
            trip_form.xpath(
                "//record[@id='view_transport_trip_form']/field[@name='arch']"
                "/form/sheet/notebook/page[@name='trip_milestones']"
            )
        )
        == 1
    )
    assert (
        len(
            trip_form.xpath(
                "//page[@name='trip_milestones']//field[@name='milestone_ids']"
            )
        )
        == 1
    )
    # The Phase 2 route/timing page keeps exactly one origin_location field.
    assert (
        len(
            trip_form.xpath(
                "//page[@name='route_timing']//field[@name='origin_location']"
            )
        )
        == 1
    )

    milestone_views = etree.parse(str(MODULE / "views/transport_milestone_views.xml"))
    headers = milestone_views.xpath(
        "//record[@id='view_transport_milestone_form']/field[@name='arch']/form/header"
    )
    assert len(headers) == 1, "Milestone form needs one header"
    header = headers[0]
    for button in (
        "action_open_arrival_wizard",
        "action_open_departure_wizard",
        "action_open_confirmation_wizard",
    ):
        assert len(header.xpath(".//button[@name='%s']" % button)) == 1, button
        assert (
            len(
                milestone_views.xpath(
                    "//record[@id='view_transport_milestone_list']"
                    "//button[@name='%s']" % button
                )
            )
            == 1
        ), button
    for button in (
        "action_open_correction_wizard",
        "action_open_cancel_confirmation_wizard",
        "action_open_skip_wizard",
    ):
        nodes = header.xpath("./button[@name='%s']" % button)
        assert len(nodes) == 1, button
        assert nodes[0].get("groups") == (
            "taj_transport_operations.group_transport_manager"
        ), button
    cancel_buttons = header.xpath(
        "./button[@name='action_open_cancel_confirmation_wizard']"
    )
    assert "confirmed_at" in (cancel_buttons[0].get("invisible") or ""), (
        "Cancelling a confirmation is only offered once one is recorded"
    )
    assert len(milestone_views.xpath("//field[@name='event_ids']")) == 1

    per_file, total_tests = count_tests()
    print("PASS transport module:", manifest["version"])
    print("PASS parsed Python/XML:", len(pyfiles), "/", len(xmlfiles))
    print(
        "PASS models:",
        len(models),
        "| view fields/buttons:",
        checked_fields,
        "/",
        checked_buttons,
    )
    print("PASS ACL rows:", len(access))
    print("PASS Phase 3 checkpoints: manual workflow, audited corrections,")
    print("     snapshot generation policy, company rules, no Phase 4 code")
    print("PASS route guards: active/company/access validation, Draft-only")
    print("     planning, atomic clear path, manual checkpoint whitelist")
    print("PASS confirmation integrity: no implicit withdrawal, manager-only")
    print("     cancellation with its own reasoned audit event")
    print("Defined Odoo test cases:", total_tests, per_file)
    print("NOTE: Runtime behaviour and permissions require the Odoo test run.")


if __name__ == "__main__":
    try:
        run()
    except Exception as err:
        print("FAILED:", repr(err), file=sys.stderr)
        sys.exit(1)
