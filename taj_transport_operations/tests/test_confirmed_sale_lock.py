"""Only explicitly authorized Sales users may change confirmed orders."""
import runpy

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, tagged
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

    def test_price_permission_allows_only_price_change(self):
        self._confirm()
        self._grant_edit_permission()
        order = self.order.with_user(self.salesperson)
        original_taxes = self.line.tax_ids
        wizard = self.env["taj.transport.sale.price.wizard"].with_user(self.salesperson).create({
            "sale_line_id": self.line.id, "price_unit": 150,
        })
        wizard.action_update_price()
        self.assertEqual(self.line.price_unit, 150)
        self.assertEqual(self.line.tax_ids, original_taxes)
        self.assertEqual(self.line.price_subtotal, 150)
        self.assertTrue(order.locked)
        self.assertTrue(order.message_ids.filtered(lambda message: "Unit price" in (message.body or "")))
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.action_unlock()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.write({"client_order_ref": "Not allowed"})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.line.with_user(self.salesperson).write({"container_num": "Not allowed"})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.line.with_user(self.salesperson).with_context(_confirmed_price_write=True).write({"price_unit": 160})
        self.salesperson.group_ids -= self.env.ref("taj_transport_operations.group_edit_confirmed_sale_orders")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.write({"client_order_ref": "Unapproved change"})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.line.with_user(self.salesperson).write({"price_unit": 200})

    def test_price_wizard_rejects_salesperson_without_price_permission(self):
        self._confirm()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["taj.transport.sale.price.wizard"].with_user(self.salesperson).create({
                "sale_line_id": self.line.id, "price_unit": 5,
            })
        self.assertEqual(self.line.price_unit, 100)

    def test_price_dialog_defaults_to_selected_line_and_current_price(self):
        self._confirm()
        self._grant_edit_permission()
        action = self.line.with_user(self.salesperson).action_open_confirmed_price_wizard()
        dialog = Form(self.env[action["res_model"]].with_user(self.salesperson).with_context(**action["context"]))
        self.assertEqual(dialog.sale_line_id.id, self.line.id)
        self.assertEqual(dialog.price_unit, 100)
        dialog.price_unit = 120
        dialog.save().action_update_price()
        self.assertEqual(self.line.price_unit, 120)
        self.assertTrue(self.order.locked)

    def test_sales_line_does_not_offer_create_trip(self):
        view = self.env["sale.order"].get_view(
            view_id=self.env.ref("sale.view_order_form").id, view_type="form",
        )
        arch = etree.fromstring(view["arch"])
        self.assertFalse(arch.xpath("//field[@name='order_line']/list/button[@string='Create Trip']"))

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

    def test_unlock_button_is_hidden_even_with_price_permission(self):
        view_id = self.env.ref("sale.view_order_form").id
        view = self.env["sale.order"].with_user(self.salesperson).get_view(view_id=view_id, view_type="form")
        self.assertFalse(etree.fromstring(view["arch"]).xpath("//button[@name='action_unlock'][not(@invisible='1')]"))
        self._grant_edit_permission()
        view = self.env["sale.order"].with_user(self.salesperson).get_view(view_id=view_id, view_type="form")
        self.assertFalse(etree.fromstring(view["arch"]).xpath("//button[@name='action_unlock'][not(@invisible='1')]"))

    def test_order_form_locks_all_editable_header_fields(self):
        view = self.env["sale.order"].get_view(
            view_id=self.env.ref("sale.view_order_form").id, view_type="form",
        )
        for node in etree.fromstring(view["arch"]).xpath(".//field[not(ancestor::field)]"):
            field = self.order._fields.get(node.get("name"))
            if field and node.get("name") != "order_line" and not field.readonly and node.get("readonly") not in ("1", "True"):
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

    def test_confirmed_analytic_account_direct_and_nested(self):
        order = self._confirm()
        plan = self.env['account.analytic.plan'].create({'name': 'Test plan'})
        account = self.env['account.analytic.account'].create({'name': 'Test account', 'plan_id': plan.id})
        line = self.line.with_user(self.salesperson)
        line.write({'analytic_account_id': account.id})
        self.assertEqual(line.analytic_account_id, account)
        order.write({'order_line': [Command.update(line.id, {'analytic_account_id': False})]})
        self.assertFalse(line.analytic_account_id)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            line.write({'analytic_account_id': account.id, 'price_unit': 10})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.write({'order_line': [Command.update(line.id, {'analytic_account_id': account.id, 'price_unit': 10})]})

    def test_confirmed_analytic_rejects_foreign_line_and_company(self):
        order = self._confirm()
        other = self.order.copy({"user_id": self.salesperson.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.write({"order_line": [Command.update(other.order_line.id, {"analytic_account_id": False})]})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            order.write({"order_line": [Command.unlink(self.line.id)]})
        company = self.env['res.company'].create({'name': 'Foreign analytics'})
        plan = self.env['account.analytic.plan'].create({'name': 'Foreign plan'})
        account = self.env['account.analytic.account'].create({'name': 'Foreign account', 'plan_id': plan.id, 'company_id': company.id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.line.write({'analytic_account_id': account.id})

    def test_order_line_create_delete_actions_are_restricted_when_locked(self):
        from ast import literal_eval
        view = self.env['sale.order'].get_view(view_id=self.env.ref('sale.view_order_form').id, view_type='form')
        node = etree.fromstring(view['arch']).xpath("//field[@name='order_line']")[0]
        options = literal_eval(node.get('options'))
        for action in ('create', 'delete'):
            self.assertIn(('state', 'not in', ['sale', 'done']), options[action])
            self.assertIn(('locked', '=', False), options[action])

    def test_confirmed_analytic_account_saved_from_order_form(self):
        self.salesperson.group_ids |= self.env.ref('analytic.group_analytic_accounting')
        order = self._confirm()
        plan = self.env['account.analytic.plan'].create({'name': 'Form plan'})
        account = self.env['account.analytic.account'].create({'name': 'Form account', 'plan_id': plan.id})
        with Form(order) as order_form:
            with order_form.order_line.edit(0) as line_form:
                line_form.analytic_account_id = account
        self.line.invalidate_recordset(['analytic_account_id'])
        self.assertEqual(self.line.analytic_account_id, account)
        self.assertTrue(order.locked)

    def test_analytic_audit_distinguishes_accounts_with_same_name(self):
        order = self._confirm()
        plan = self.env['account.analytic.plan'].create({'name': 'Audit plan'})
        accounts = self.env['account.analytic.account'].create([
            {'name': 'Same name', 'plan_id': plan.id},
            {'name': 'Same name', 'plan_id': plan.id},
        ])
        line = self.line.with_user(self.salesperson)
        line.write({'analytic_account_id': accounts[0].id})
        before = order.message_ids
        line.write({'analytic_account_id': accounts[1].id})
        new_messages = order.message_ids - before
        body = ' '.join(new_messages.mapped('body'))
        self.assertIn('#%s' % accounts[0].id, body)
        self.assertIn('#%s' % accounts[1].id, body)
