"""Only explicitly authorized Sales users may change confirmed orders."""
import runpy

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import file_path


@tagged("post_install", "-at_install")
class TestConfirmedSaleLock(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.salesperson = new_test_user(cls.env, login="confirmed_lock_sales",
                                       groups="base.group_user,sales_team.group_sale_salesman")
        cls.customer = cls.env["res.partner"].create({"name": "Confirmed lock customer"})
        cls.product = cls.env["product.product"].create({"name": "Confirmed lock service", "type": "service"})
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.customer.id, "user_id": cls.salesperson.id,
            "order_line": [Command.create({"product_id": cls.product.id, "price_unit": 100})],
        })
        cls.line = cls.order.order_line

    def _confirm(self):
        self.order.with_user(self.salesperson).action_confirm()
        return self.order.with_user(self.salesperson)

    def _grant_edit_permission(self):
        self.salesperson.group_ids |= self.env.ref("taj_transport_operations.group_edit_confirmed_sale_orders")

    def test_confirmation_locks_order_for_ordinary_salesperson(self):
        order = self._confirm()
        self.assertEqual(order.state, "sale")
        self.assertTrue(order.locked)
        self.assertEqual(len(self.env["taj.transport.trip.file"].search([("sale_order_id", "=", order.id)])), 1)

    def test_blocks_header_edit_and_unlock_through_orm(self):
        order = self._confirm()
        for values in ({"client_order_ref": "Changed"}, {"note": "Changed"},
                       {"locked": False}, {"state": "draft"}):
            with self.subTest(values=values), self.assertRaises(AccessError), self.env.cr.savepoint():
                order.with_context(skip_confirmed_sale_lock=True, _confirmed_sale_accounting=True).write(values)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.action_unlock()

    def test_blocks_line_changes_additions_and_removal(self):
        order = self._confirm()
        line = self.line.with_user(self.salesperson)
        for values in ({"price_unit": 50}, {"container_num": "Changed"},
                       {"cargo_description": "Changed"}, {"tax_ids": [Command.clear()]}):
            with self.subTest(values=values), self.assertRaises(AccessError), self.env.cr.savepoint():
                line.write(values)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["sale.order.line"].with_user(self.salesperson).create({
                "order_id": order.id, "product_id": self.product.id,
            })
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            line.unlink()

    def test_nested_commands_and_line_reparenting_cannot_bypass_lock(self):
        self._confirm()
        draft = self.order.copy({"user_id": self.salesperson.id})
        for values in ({"order_line": [Command.update(self.line.id, {"price_unit": 50})]},
                       {"order_line": [Command.create({"product_id": self.product.id})]}):
            with self.subTest(values=values), self.assertRaises(AccessError), self.env.cr.savepoint():
                self.order.with_user(self.salesperson).write(values)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.line.with_user(self.salesperson).write({"order_id": draft.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            draft.order_line.with_user(self.salesperson).write({"order_id": self.order.id})

    def test_saved_order_default_cannot_bypass_line_creation_lock(self):
        self._confirm()
        self.env["ir.default"].with_user(self.salesperson).set(
            "sale.order.line", "order_id", self.order.id, user_id=True,
        )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["sale.order.line"].with_user(self.salesperson).create({"product_id": self.product.id})

    def test_permission_allows_unlock_and_edit_but_others_stay_blocked(self):
        self._confirm()
        self._grant_edit_permission()
        order = self.order.with_user(self.salesperson)
        order.action_unlock()
        order.write({"client_order_ref": "Approved change"})
        self.line.with_user(self.salesperson).write({"price_unit": 150, "container_num": "APPROVED"})
        wizard = self.env["taj.transport.duplicate.sale.line.wizard"].with_user(self.salesperson).create({
            "sale_order_id": order.id, "source_line_id": self.line.id, "clean_mode": "all",
        })
        wizard.action_duplicate()
        self.assertEqual(len(order.order_line), 2)
        self.assertEqual(self.line.price_unit, 150)
        self.salesperson.group_ids -= self.env.ref("taj_transport_operations.group_edit_confirmed_sale_orders")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.write({"client_order_ref": "Unapproved change"})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.line.with_user(self.salesperson).write({"price_unit": 200})

    def test_sales_manager_needs_explicit_permission_too(self):
        order = self._confirm()
        manager = new_test_user(self.env, login="confirmed_lock_manager",
                                groups="base.group_user,sales_team.group_sale_manager")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.with_user(manager).action_unlock()

    def test_drafts_remain_editable_and_confirmed_orders_can_be_copied(self):
        order = self.order.with_user(self.salesperson)
        order.write({"client_order_ref": "Draft change"})
        self.line.with_user(self.salesperson).write({"price_unit": 80})
        self._confirm()
        duplicate = order.copy()
        self.assertEqual(duplicate.state, "draft")
        self.assertFalse(duplicate.locked)
        duplicate.order_line.write({"price_unit": 90})

    def test_authorized_unlock_button_is_hidden_without_permission(self):
        view_id = self.env.ref("sale.view_order_form").id
        view = self.env["sale.order"].with_user(self.salesperson).get_view(view_id=view_id, view_type="form")
        self.assertFalse(etree.fromstring(view["arch"]).xpath("//button[@name='action_unlock']"))
        self._grant_edit_permission()
        view = self.env["sale.order"].with_user(self.salesperson).get_view(view_id=view_id, view_type="form")
        self.assertTrue(etree.fromstring(view["arch"]).xpath("//button[@name='action_unlock']"))

    def test_order_form_locks_all_editable_header_fields(self):
        view = self.env["sale.order"].get_view(
            view_id=self.env.ref("sale.view_order_form").id, view_type="form",
        )
        for node in etree.fromstring(view["arch"]).xpath(".//field[not(ancestor::field)]"):
            field = self.order._fields.get(node.get("name"))
            if field and not field.readonly and node.get("readonly") not in ("1", "True"):
                self.assertIn("transport_confirmed_readonly", node.get("readonly", ""), node.get("name"))

    def test_notes_and_attachments_do_not_change_locked_commercial_data(self):
        order = self._confirm()
        message = order.message_post(body="Shipment follow-up", attachments=[("follow-up.txt", b"Shipment note")])
        self.assertTrue(message)
        self.assertTrue(order.locked)
        self.assertEqual(self.line.price_unit, 100)

    def test_upgrade_locks_existing_confirmed_orders_without_changing_drafts(self):
        self.order.action_confirm()
        self.order.action_unlock()
        draft = self.order.copy()
        self.env.flush_all()
        migrate = runpy.run_path(file_path(
            "taj_transport_operations/migrations/19.0.1.23.0/post-migration.py"
        ))["migrate"]
        migrate(self.env.cr, "19.0.1.22.0")
        (self.order | draft).invalidate_recordset(["locked"])
        self.assertTrue(self.order.locked)
        self.assertFalse(draft.locked)
