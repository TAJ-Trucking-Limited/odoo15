"""Company shipment requirements apply to UI, direct ORM and nested order saves."""

from types import SimpleNamespace

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestShipmentRequirements(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.customer = cls.env["res.partner"].create({"name": "Requirements customer"})
        cls.goods = cls.env["product.product"].create({
            "name": "Requirements goods", "type": "consu",
        })
        cls.service = cls.env["product.product"].create({
            "name": "Requirements service", "type": "service",
        })
        cls.env["taj.transport.route"].create({
            "name": "Requirements route", "company_id": cls.company.id,
            "product_tmpl_id": cls.goods.product_tmpl_id.id,
        })
        plan = cls.env["account.analytic.plan"].create({"name": "Requirements plan"})
        account = cls.env["account.analytic.account"].create({
            "name": "Requirements account", "plan_id": plan.id,
            "company_id": cls.company.id,
        })
        cls.values = {
            "container_num": "CONT-1", "srn": "REF-1", "consignee": "Receiver",
            "size": "40ft", "weight": "0", "file_name": "Shipment file",
            "file_type_id": cls.env.ref("taj_transport_operations.file_type_transit_import").id,
            "clearing_service": "not_included", "bill_of_lading": "BL-1",
            "eta": "2026-12-01", "etol": "2026-11-30", "shipping_line": "Carrier",
            "cargo_type_id": cls.env.ref("taj_transport_operations.cargo_type_general").id,
            "cargo_description": "Machinery", "special_handling_instructions": "Keep dry",
            "transport_planned_departure_at": "2026-12-01 09:00:00",
            "transport_planned_arrival_at": "2026-12-02 09:00:00",
            "analytic_account_id": account.id,
        }
        cls.manager = new_test_user(
            cls.env, login="shipment_requirements_manager",
            groups="taj_transport_operations.group_transport_manager",
            company_id=cls.company.id,
        )
        cls.sales = new_test_user(
            cls.env, login="shipment_requirements_sales",
            groups="sales_team.group_sale_salesman,analytic.group_analytic_accounting",
            company_id=cls.company.id,
        )

    def _order(self, **values):
        return self.env["sale.order"].create({"partner_id": self.customer.id, **values})

    def _line(self, order=None, **values):
        return self.env["sale.order.line"].create({
            "order_id": (order or self._order()).id,
            "product_id": self.goods.id, **self.values, **values,
        })

    def test_every_approved_field_blocks_missing_create_and_clear(self):
        for name in self.values:
            with self.subTest(field=name):
                self.company.transport_required_shipment_fields = [name]
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self._line(**{name: False})
                line = self._line()
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    line.write({name: False})

    def test_optional_file_type_and_other_fields_can_be_empty(self):
        self.company.transport_required_shipment_fields = []
        line = self._line(**dict.fromkeys(self.values, False))
        self.assertFalse(line.file_type_id)
        self.assertFalse(line.container_num)

    def test_required_text_rejects_whitespace_and_reports_all_missing_fields(self):
        self.company.transport_required_shipment_fields = ["consignee", "container_num"]
        with self.assertRaisesRegex(ValidationError, "(?is)Container Number.*Consignee"), self.env.cr.savepoint():
            self._line(consignee="  ", container_num="\n")

    def test_required_zero_weight_and_not_included_selection_are_valid(self):
        self.company.transport_required_shipment_fields = ["weight", "clearing_service"]
        line = self._line()
        self.assertEqual(line.weight, "0")
        self.assertEqual(line.clearing_service, "not_included")

    def test_nested_order_create_and_unrelated_order_save_are_checked(self):
        self.company.transport_required_shipment_fields = ["consignee"]
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._order(order_line=[Command.create({"product_id": self.service.id})])
        self.company.transport_required_shipment_fields = []
        line = self._line(consignee=False)
        self.company.transport_required_shipment_fields = ["consignee"]
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            line.order_id.write({"client_order_ref": "Changed reference"})
        line.write({"consignee": "Filled"})
        line.order_id.write({"client_order_ref": "Changed reference"})
        self.assertEqual(line.order_id.client_order_ref, "Changed reference")

    def test_notes_sections_and_downpayments_are_exempt(self):
        self.company.transport_required_shipment_fields = list(self.values)
        order = self._order()
        for kind in ("line_section", "line_note"):
            self.env["sale.order.line"].create({
                "order_id": order.id, "display_type": kind, "name": kind,
            })
        self.env["sale.order.line"].create({
            "order_id": order.id, "name": "Down payment", "is_downpayment": True,
        })
        order.write({"client_order_ref": "Exempt lines"})

    def test_file_type_requirement_remains_goods_only(self):
        self.company.transport_required_shipment_fields = ["file_type_id"]
        line = self._line(product_id=self.service.id, file_type_id=False)
        self.assertFalse(line.file_type_id)

    def test_sales_user_cannot_bypass_required_field(self):
        self.company.transport_required_shipment_fields = ["consignee"]
        order = self._order(user_id=self.sales.id)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["sale.order.line"].with_user(self.sales).create({
                "order_id": order.id, "product_id": self.service.id,
            })

    def test_manager_can_save_and_reopen_requirements_without_company_write_access(self):
        Fields = self.env["ir.model.fields"]
        selected = Fields.search([("model", "=", "sale.order.line"), ("name", "in", list(self.values))])
        Wizard = self.env["taj.transport.settings"].with_user(self.manager)
        self.assertIn("required_shipment_field_ids", Wizard._fields)
        choices = Fields.with_user(self.manager).with_context(hide_model=True).search_read(
            Wizard._fields["required_shipment_field_ids"].domain(Wizard),
            ["name", "display_name"],
        )
        self.assertEqual({choice["name"] for choice in choices}, set(self.values))
        wizard = Wizard.create({"required_shipment_field_ids": [Command.set(selected.ids)]})
        wizard.action_save()
        self.assertEqual(set(self.company.transport_required_shipment_fields), set(self.values))
        reopened = Wizard.create({})
        self.assertEqual(reopened.required_shipment_field_ids, selected)
        reopened.write({"required_shipment_field_ids": [Command.clear()]})
        reopened.action_save()
        self.assertFalse(self.company.transport_required_shipment_fields)

    def test_out_of_scope_fields_and_unauthorized_settings_are_rejected(self):
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["taj.transport.settings"].with_user(self.sales).create({})
        for invalid in (["price_unit"], ["unknown_field"], "consignee", {"consignee": True}):
            with self.subTest(value=invalid), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.company.write({"transport_required_shipment_fields": invalid})
        price = self.env["ir.model.fields"].search([("model", "=", "sale.order.line"), ("name", "=", "price_unit")])
        wizard = self.env["taj.transport.settings"].with_user(self.manager).create({
            "required_shipment_field_ids": [Command.set(price.ids)],
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            wizard.action_save()

    def test_company_isolation_and_settings_company_tampering(self):
        other = self.env["res.company"].create({"name": "Other requirements company"})
        self.company.transport_required_shipment_fields = ["consignee"]
        other.transport_required_shipment_fields = []
        line = self._line(
            product_id=self.service.id, analytic_account_id=False,
            order=self._order(company_id=other.id), consignee=False,
        )
        self.assertFalse(line.consignee)
        wizard = self.env["taj.transport.settings"].with_user(self.manager).create({"company_id": other.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            wizard.action_save()
        self.assertFalse(other.transport_required_shipment_fields)

    def test_ui_required_modifiers_follow_record_company_configuration(self):
        self.company.transport_required_shipment_fields = ["consignee"]
        view = self.env["sale.order"].with_user(self.sales).get_view(
            self.env.ref("sale.view_order_form").id, "form",
        )
        arch = etree.fromstring(view["arch"])
        self.assertTrue(arch.xpath("//sheet/field[@name='transport_required_shipment_fields']"))
        for name in self.values:
            nodes = arch.xpath("//field[@name='order_line']/list/field[@name='%s']" % name)
            self.assertTrue(nodes, name)
            expression = nodes[0].get("required", "False")
            for required in ([], [name]):
                self.assertEqual(bool(safe_eval(expression, {
                    "parent": SimpleNamespace(transport_required_shipment_fields=required),
                    "display_type": False, "is_downpayment": False, "product_type": "consu",
                })), bool(required), name)

    def test_form_save_accepts_completed_required_shipment_fields(self):
        self.company.transport_required_shipment_fields = ["consignee", "container_num"]
        order = self._order(user_id=self.sales.id)
        with Form(order.with_user(self.sales)) as form:
            with form.order_line.new() as line:
                line.product_id = self.service
                line.consignee = "Form receiver"
                line.container_num = "FORM-1"
        self.assertEqual(order.order_line.consignee, "Form receiver")

    def test_direct_company_configuration_requires_manager_role(self):
        administrator = new_test_user(
            self.env, login="shipment_requirements_nonmanager_admin", groups="base.group_system",
        )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.company.with_user(administrator).write({"transport_required_shipment_fields": []})

    def test_confirmed_shipment_update_cannot_clear_required_field(self):
        self.company.transport_required_shipment_fields = ["consignee"]
        line = self._line()
        line.order_id.action_confirm()
        row = line.transport_file_line_ids
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            line.with_user(self.manager)._write_file_shipment({"consignee": False}, row.with_user(self.manager))

    def test_new_requirements_do_not_prevent_cancelling_an_existing_order(self):
        line = self._line(consignee=False)
        line.order_id.action_confirm()
        self.company.transport_required_shipment_fields = ["consignee"]
        line.order_id.action_cancel()
        self.assertEqual(line.order_id.state, "cancel")
