"""Sales line duplication clears only the shipment fields selected by Sales."""
from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.service.model import call_kw
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestDuplicateSaleLine(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Duplicate shipment customer"})
        cls.product = cls.env["product.product"].create({
            "name": "Duplicate shipment goods", "type": "consu", "list_price": 900,
        })
        cls.file_type = cls.env.ref("taj_transport_operations.file_type_transit_import")
        cls.env["taj.transport.route"].create({
            "name": "Duplicate shipment route", "company_id": cls.env.company.id,
            "product_tmpl_id": cls.product.product_tmpl_id.id,
        })
        cls.order = cls.env["sale.order"].create({"partner_id": cls.customer.id})
        brand = cls.env["fleet.vehicle.model.brand"].create({"name": "Duplicate brand"})
        model = cls.env["fleet.vehicle.model"].create({"name": "Duplicate truck", "brand_id": brand.id})
        cls.truck = cls.env["fleet.vehicle"].create({"model_id": model.id, "license_plate": "DUPLICATE-01"})
        cls.line = cls.env["sale.order.line"].create({
            "order_id": cls.order.id, "product_id": cls.product.id,
            "file_type_id": cls.file_type.id, "clearing_service": "included",
            "consignee": "Shipment consignee", "bill_of_lading": "BL-DUPLICATE",
            "container_num": "CONT-123", "eta": "2026-12-01", "shipping_line": "Carrier",
            "cargo_type_id": cls.env.ref("taj_transport_operations.cargo_type_general").id,
            "size": "40ft", "weight": "22000", "cargo_description": "Machinery",
            "special_handling_instructions": "Keep upright", "product_uom_qty": 2.5,
            "price_unit": 321, "discount": 7, "tax_ids": [], "sequence": 10,
            "vehicle_id": cls.truck.id, "file_name": "Customer file", "srn": "REF-123",
            "transport_planned_departure_at": "2026-12-01 08:00:00",
            "transport_planned_arrival_at": "2026-12-03 08:00:00",
        })

    def _wizard(self, mode="container", **values):
        return self.env["taj.transport.duplicate.sale.line.wizard"].create({
            "sale_order_id": self.order.id, "source_line_id": self.line.id,
            "clean_mode": mode, **values,
        })

    def _duplicate(self, mode):
        before = self.order.order_line
        result = self._wizard(mode).action_duplicate()
        self.assertEqual(result["type"], "ir.actions.act_window_close")
        duplicate = self.order.order_line - before
        self.assertEqual(len(duplicate), 1)
        return duplicate

    def test_control_button_opens_chooser_for_current_order(self):
        view = self.env["sale.order"].get_view(
            view_id=self.env.ref("sale.view_order_form").id, view_type="form",
        )
        arch = etree.fromstring(view["arch"])
        controls = arch.xpath("//field[@name='order_line']/list/control")[0]
        buttons = controls.xpath("button[@name='action_open_duplicate_line_wizard']")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].getprevious().get("name"), "add_product_control")
        action = self.env["sale.order.line"].with_context(order_id=self.order.id).action_open_duplicate_line_wizard()
        self.assertEqual(action["target"], "new")
        wizard = self.env[action["res_model"]].with_context(action["context"]).new({})
        self.assertEqual(wizard.sale_order_id, self.order)
        self.assertFalse(wizard.source_line_id)

    def test_clear_mode_labels_match_the_cargo_clearing_behavior(self):
        labels = dict(self.env["taj.transport.duplicate.sale.line.wizard"]._fields["clean_mode"].selection)
        self.assertEqual(labels, {
            "container": "Clear Container",
            "all": "Clear All Variables",
        })

    def test_duplicate_control_button_rpc_dispatch(self):
        # A One2many <control> object button sends an ids argument through
        # call_kw. @api.model incorrectly treats it as a method argument.
        action = call_kw(
            self.env["sale.order.line"],
            "action_open_duplicate_line_wizard",
            [[]],
            {"context": {"order_id": self.order.id}},
        )
        self.assertEqual(action["res_model"], "taj.transport.duplicate.sale.line.wizard")
        self.assertEqual(action["context"]["default_sale_order_id"], self.order.id)
        with self.assertRaises(UserError):
            call_kw(
                self.env["sale.order.line"],
                "action_open_duplicate_line_wizard",
                [[]],
                {"context": {}},
            )

    def test_clean_container_preserves_other_values_and_original(self):
        duplicate = self._duplicate("container")
        self.assertFalse(duplicate.container_num)
        self.assertFalse(duplicate.weight)
        self.assertEqual(duplicate.price_unit, 321)
        for name in (
            "product_id", "file_type_id", "clearing_service", "consignee", "bill_of_lading",
            "eta", "shipping_line", "cargo_type_id", "size", "cargo_description",
            "special_handling_instructions", "product_uom_qty", "product_uom_id", "discount",
            "tax_ids", "file_name", "srn", "transport_planned_departure_at", "transport_planned_arrival_at",
        ):
            self.assertEqual(duplicate[name], self.line[name], name)
        self.assertGreater(duplicate.sequence, self.line.sequence)
        self.assertFalse(duplicate.vehicle_id)
        self.assertEqual(self.line.container_num, "CONT-123")
        self.assertEqual(self.line.weight, "22000")
        self.assertEqual(self.line.vehicle_id, self.truck)

    def test_clean_all_clears_shipment_fields_and_sets_price_and_amount_to_zero(self):
        duplicate = self._duplicate("all")
        for name in ("container_num", "cargo_type_id", "size", "weight", "cargo_description",
                     "special_handling_instructions"):
            self.assertFalse(duplicate[name], name)
        self.assertEqual(duplicate.price_unit, 0)
        self.assertEqual(duplicate.price_subtotal, 0)
        self.assertEqual(duplicate.price_total, 0)
        self.assertEqual(duplicate.product_id, self.product)
        self.assertEqual(duplicate.file_type_id, self.file_type)
        self.assertEqual(duplicate.clearing_service, "included")
        self.assertEqual(duplicate.product_uom_qty, 2.5)
        self.assertEqual(self.line.cargo_description, "Machinery")
        self.assertEqual(self.line.price_unit, 321)

    def test_analytical_account_is_always_cleared_on_duplicate(self):
        plan = self.env["account.analytic.plan"].create({
            "name": "Duplicated shipment test plan",
        })
        account = self.env["account.analytic.account"].create({
            "name": "Original shipment analytical account",
            "plan_id": plan.id,
        })
        self.line.analytic_account_id = account
        self.line.analytic_distribution = {str(account.id): 100}
        for mode in ("container", "all"):
            duplicated = self._duplicate(mode)
            self.assertFalse(duplicated.analytic_account_id)
            self.assertFalse(duplicated.analytic_distribution)
            self.assertEqual(self.line.analytic_account_id, account)

    def test_rejects_source_from_another_order_and_nonproduct_lines(self):
        other = self.env["sale.order"].create({"partner_id": self.customer.id})
        note = self.env["sale.order.line"].create({
            "order_id": self.order.id, "display_type": "line_note", "name": "Do not duplicate",
        })
        for values in ({"sale_order_id": other.id}, {"source_line_id": note.id}):
            with self.subTest(values=values), self.assertRaises(UserError), self.env.cr.savepoint():
                self._wizard(**values).action_duplicate()

    def test_rechecks_order_lock_and_cancellation_at_confirmation(self):
        wizard = self._wizard()
        for values in ({"locked": True}, {"locked": False, "state": "cancel"}):
            self.order.write(values)
            with self.subTest(values=values), self.assertRaises(UserError), self.env.cr.savepoint():
                wizard.action_duplicate()

    def test_sales_user_can_duplicate_without_transport_role(self):
        salesperson = new_test_user(self.env, login="duplicate_salesperson",
                                    groups="base.group_user,sales_team.group_sale_salesman")
        self.order.user_id = salesperson
        wizard = self.env["taj.transport.duplicate.sale.line.wizard"].with_user(salesperson).create({
            "sale_order_id": self.order.id, "source_line_id": self.line.id, "clean_mode": "all",
        })
        view = wizard.get_view(
            view_id=self.env.ref("taj_transport_operations.view_duplicate_sale_line_wizard_form").id,
            view_type="form",
        )
        field = etree.fromstring(view["arch"]).xpath("//field[@name='source_line_id']")[0]
        labels = self.env["sale.order.line"].with_user(salesperson).with_context(
            safe_eval(field.get("context")),
        ).name_search(domain=[("order_id", "=", self.order.id)])
        self.assertEqual(len(labels), 1)
        self.assertIn("CONT-123", labels[0][1])
        before = self.order.order_line
        wizard.action_duplicate()
        self.assertEqual(len(self.order.order_line - before), 1)
        self.assertEqual((self.order.order_line - before).price_unit, 0)

    def test_read_only_dispatcher_cannot_duplicate_sales_lines(self):
        dispatcher = new_test_user(self.env, login="duplicate_dispatcher",
                                  groups="base.group_user,taj_transport_operations.group_transport_user")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["sale.order.line"].with_user(dispatcher).with_context(
                order_id=self.order.id,
            ).action_open_duplicate_line_wizard()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["taj.transport.duplicate.sale.line.wizard"].with_user(dispatcher).create({
                "sale_order_id": self.order.id, "source_line_id": self.line.id,
            })

    def test_confirmed_order_duplicate_gets_own_file_without_copying_trip(self):
        self.order.action_confirm()
        self.order.action_unlock()
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line.id})
        duplicate = self._duplicate("container")
        self.assertFalse(duplicate.transport_trip_ids)
        self.assertEqual(self.line.transport_trip_ids, trip)
        files = self.env["taj.transport.trip.file.line"].search([("sale_line_id", "=", duplicate.id)])
        self.assertEqual(len(files), 1)
        self.assertFalse(files.container_num)
        self.assertEqual(files.bill_of_lading, "BL-DUPLICATE")
