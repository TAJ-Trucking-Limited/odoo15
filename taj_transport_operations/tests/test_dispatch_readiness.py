"""Dispatch worklists: trip prerequisites, the menus and their cutoff date."""

from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestDispatchReadiness(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create(
            {"name": "Dispatch readiness customer"}
        )
        cls.brand = cls.env["fleet.vehicle.model.brand"].create(
            {"name": "Dispatch readiness brand"}
        )
        cls.truck_model = cls.env["fleet.vehicle.model"].create(
            {
                "name": "Dispatch readiness truck model",
                "brand_id": cls.brand.id,
                "category_id": cls.env.ref(
                    "taj_transport_operations.fleet_category_truck"
                ).id,
            }
        )
        cls.truck = cls.env["fleet.vehicle"].create(
            {
                "model_id": cls.truck_model.id,
                "license_plate": "DISPATCH-01",
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Dispatch readiness service",
                "type": "service",
                "list_price": 700,
            }
        )
        cls.order = cls.env["sale.order"].create(
            {"partner_id": cls.customer.id}
        )
        cls.trucked_line = cls.env["sale.order.line"].create(
            {
                "order_id": cls.order.id,
                "product_id": cls.product.id,
                "name": "Trucked freight",
                "product_uom_qty": 1,
                "price_unit": 700,
                "vehicle_id": cls.truck.id,
            }
        )
        cls.truckless_line = cls.env["sale.order.line"].create(
            {
                "order_id": cls.order.id,
                "product_id": cls.product.id,
                "name": "Freight awaiting truck",
                "product_uom_qty": 1,
                "price_unit": 700,
            }
        )
        cls.order.action_confirm()
        cls.other_order = cls.env["sale.order"].create(
            {"partner_id": cls.customer.id}
        )
        cls.other_line = cls.env["sale.order.line"].create(
            {
                "order_id": cls.other_order.id,
                "product_id": cls.product.id,
                "name": "Second trucked freight",
                "product_uom_qty": 1,
                "price_unit": 700,
                "vehicle_id": cls.truck.id,
            }
        )
        cls.other_order.action_confirm()

    def _action_lines(self, xmlid):
        # Read the action the way /web/action/load does: the cutoff date is
        # appended to the stored domain there.
        action = self.env.ref(xmlid)._get_action_dict()
        return self.env["sale.order.line"].search(safe_eval(action["domain"]))

    def _set_cutoff(self, value):
        self.env["ir.config_parameter"].sudo().set_param(
            "taj_transport_operations.dispatch_cutoff_date",
            value.isoformat() if value else "",
        )

    def _our_lines(self):
        return self.trucked_line | self.truckless_line | self.other_line

    def test_truckless_line_can_create_draft_but_not_ready_trip(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.truckless_line.id,
        })
        self.assertEqual(trip.state, "draft")
        self.assertFalse(trip.initial_vehicle_id)
        self.assertFalse(trip.current_assignment_id)
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()

    def test_dispatch_button_opens_form_without_creating_trip(self):
        action = self.truckless_line.action_prepare_transport_trip()
        self.assertEqual(action["res_model"], "taj.transport.trip")
        self.assertEqual(action["context"]["default_sale_line_id"], self.truckless_line.id)
        self.assertEqual(action["context"]["default_sale_order_id"], self.order.id)
        self.assertNotIn("res_id", action)
        self.assertFalse(self.truckless_line.transport_trip_ids)

    def test_worklist_selection_buttons_are_in_correct_list_headers(self):
        cases = (
            ("view_sale_order_line_dispatch_list",
             "action_create_selected_transport_trip", "Create Trip"),
            ("view_sale_order_line_without_allocation_list",
             "action_assign_selected_truck", "Assign Truck"),
        )
        for view_name, method, label in cases:
            view_id = self.env.ref(f"taj_transport_operations.{view_name}").id
            arch = self.env["sale.order.line"].get_view(
                view_id=view_id, view_type="list"
            )["arch"]
            buttons = etree.fromstring(arch).xpath(
                f"./header/button[@name='{method}']"
            )
            self.assertEqual(len(buttons), 1, view_name)
            self.assertEqual(buttons[0].get("string"), label)
            self.assertEqual(buttons[0].get("type"), "object")
            self.assertNotEqual(buttons[0].get("display"), "always")

    def test_dispatch_form_views_have_actions_for_individual_lines(self):
        cases = (
            ("action_sale_order_line_without_trip",
             "view_sale_order_line_dispatch_list",
             "view_sale_order_line_dispatch_form",
             "action_create_selected_transport_trip"),
            ("action_sale_order_line_without_truck",
             "view_sale_order_line_without_allocation_list",
             "view_sale_order_line_without_allocation_form",
             "action_assign_selected_truck"),
        )
        for action_name, list_name, form_name, button_name in cases:
            action = self.env.ref("taj_transport_operations." + action_name)
            listing = self.env.ref("taj_transport_operations." + list_name)
            form = self.env.ref("taj_transport_operations." + form_name)
            self.assertEqual(action.views, [
                (listing.id, "list"), (form.id, "form"),
            ])
            arch = etree.fromstring(
                self.env["sale.order.line"].get_view(
                    view_id=form.id, view_type="form",
                )["arch"]
            )
            self.assertEqual(len(
                arch.xpath(f"./header/button[@name='{button_name}']")
            ), 1)
            self.assertTrue(arch.xpath("//field[@name='etol']"))

    def test_worklist_selection_buttons_use_existing_single_line_actions(self):
        dispatcher = new_test_user(
            self.env,
            login="dispatch_selection_user",
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        line = self.truckless_line.with_user(dispatcher)
        allocation = line.action_assign_selected_truck()
        self.assertEqual(allocation["res_model"], "taj.transport.assign.truck.wizard")
        self.assertEqual(allocation["context"]["default_sale_line_id"], line.id)
        self.assertFalse(line.vehicle_id)
        self.env["taj.transport.route"].create({
            "name": "Dispatch selection route",
            "company_id": self.order.company_id.id,
            "product_tmpl_id": self.product.product_tmpl_id.id,
        })
        creation = line.action_create_selected_transport_trip()
        self.assertEqual(creation["res_model"], "taj.transport.trip")
        self.assertEqual(creation["res_id"], line.transport_trip_ids.id)
        self.assertEqual(len(line.transport_trip_ids), 1)
        repeat = line.action_create_selected_transport_trip()
        self.assertEqual(repeat["res_id"], creation["res_id"])
        self.assertEqual(len(line.transport_trip_ids), 1)

    def test_selection_buttons_reject_multiple_lines_and_unauthorized_users(self):
        dispatcher = new_test_user(
            self.env,
            login="dispatch_selection_multi",
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        selected = (self.truckless_line | self.trucked_line).with_user(dispatcher)
        for method in ("action_create_selected_transport_trip", "action_assign_selected_truck"):
            with self.subTest(method=method), self.assertRaises(UserError):
                getattr(selected, method)()
        self.assertFalse(self.truckless_line.transport_trip_ids)
        with self.assertRaises(UserError):
            self.trucked_line.with_user(dispatcher).action_assign_selected_truck()
        salesperson = new_test_user(
            self.env,
            login="dispatch_selection_salesperson",
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        with self.assertRaises(AccessError):
            self.truckless_line.with_user(salesperson).action_assign_selected_truck()
        with self.assertRaises(AccessError):
            self.truckless_line.with_user(salesperson).action_create_selected_transport_trip()

    def test_not_started_action_keeps_confirmed_draft_trips(self):
        lines = (
            self._action_lines(
                "taj_transport_operations.action_sale_order_line_without_trip"
            )
            & self._our_lines()
        )
        self.assertEqual(lines, self.trucked_line | self.other_line)
        self.assertNotIn(self.truckless_line, lines)

        trip = self.env["taj.transport.trip"].create(
            {"sale_line_id": self.trucked_line.id}
        )
        self.assertEqual(self.trucked_line.transport_trip_ids, trip)
        self.assertEqual(self.trucked_line.transport_trip_count, 1)
        lines = (
            self._action_lines(
                "taj_transport_operations.action_sale_order_line_without_trip"
            )
            & self._our_lines()
        )
        self.assertIn(self.trucked_line, lines)
        self.assertNotIn(self.truckless_line, lines)
        self.assertIn(self.other_line, lines)

    def test_without_truck_action_ignores_trucked_lines(self):
        lines = (
            self._action_lines(
                "taj_transport_operations.action_sale_order_line_without_truck"
            )
            & self._our_lines()
        )
        self.assertEqual(lines, self.truckless_line)

    def test_cutoff_date_hides_older_orders_from_every_worklist(self):
        actions = {
            "without_trip": self.trucked_line | self.other_line,
            "without_truck": self.truckless_line,
        }
        for suffix, expected in actions.items():
            xmlid = (
                "taj_transport_operations.action_sale_order_line_" + suffix
            )
            # Two days ahead: every line belongs to an older order.
            self._set_cutoff(fields.Date.today() + timedelta(days=2))
            self.assertFalse(
                self._action_lines(xmlid) & self._our_lines(), xmlid
            )
            # Yesterday: the cutoff does not remove anything.
            self._set_cutoff(fields.Date.today() - timedelta(days=1))
            self.assertEqual(
                self._action_lines(xmlid) & self._our_lines(), expected, xmlid
            )

    def test_cutoff_settings_save_and_reload(self):
        cutoff = fields.Date.today()
        settings = self.env["taj.transport.settings"].create({
            "dispatch_cutoff_date": cutoff,
        })
        settings.action_save()
        self.assertEqual(
            self.env["taj.transport.settings"].create({}).dispatch_cutoff_date,
            cutoff,
        )
        settings.write({"dispatch_cutoff_date": False})
        settings.action_save()
        self.assertFalse(
            self.env["taj.transport.settings"].create({}).dispatch_cutoff_date
        )

    def test_worklist_menus_run_uncached_server_actions(self):
        """Menus must not open the window action by id.

        The web client caches /web/action/load on disk, so an action id keeps
        serving the cutoff it was loaded with. Server actions run on every
        click and return the action with the cutoff just read from the config.
        """
        menus = (
            self.env.ref("taj_transport_operations.menu_transport_dispatch_lines"),
            self.env.ref("taj_transport_operations.menu_transport_lines_without_trip"),
            self.env.ref("taj_transport_operations.menu_transport_lines_without_truck"),
        )
        for menu in menus:
            self.assertEqual(menu.action._name, "ir.actions.server", menu.name)

        # A dispatcher has read access on sale.order.line only, never write.
        dispatcher = new_test_user(
            self.env,
            login="dispatch_worklist_server_action",
            company_id=self.env.company.id,
            groups=(
                "base.group_user,taj_transport_operations.group_transport_user"
            ),
        )
        menu = self.env.ref(
            "taj_transport_operations.menu_transport_lines_without_trip"
        )
        self._set_cutoff(fields.Date.today() + timedelta(days=2))
        action = menu.action.with_user(dispatcher).run()
        self.assertEqual(action["res_model"], "sale.order.line")
        self.assertFalse(
            self.env["sale.order.line"].search(safe_eval(action["domain"]))
            & self._our_lines()
        )
        self._set_cutoff(fields.Date.today() - timedelta(days=1))
        action = menu.action.with_user(dispatcher).run()
        self.assertEqual(
            self.env["sale.order.line"].search(safe_eval(action["domain"]))
            & self._our_lines(),
            self.trucked_line | self.other_line,
        )

    def test_dispatcher_can_read_the_worklists(self):
        dispatcher = new_test_user(
            self.env,
            login="dispatch_worklist_reader",
            company_id=self.env.company.id,
            groups=(
                "base.group_user,taj_transport_operations.group_transport_user"
            ),
        )
        Line = self.env["sale.order.line"].with_user(dispatcher)
        order_ids = (self.order | self.other_order).ids
        lines = Line.search([("order_id", "in", order_ids)])
        self.assertEqual(len(lines), 3)
        self.assertEqual(
            lines.mapped("order_id"), self.order | self.other_order
        )

    def test_dispatch_worklists_share_parent_menu(self):
        parent = self.env.ref(
            "taj_transport_operations.menu_transport_dispatch_lines"
        )
        without_trip = self.env.ref(
            "taj_transport_operations.menu_transport_lines_without_trip"
        )
        without_truck = self.env.ref(
            "taj_transport_operations.menu_transport_lines_without_truck"
        )
        self.assertEqual(without_trip.parent_id, parent)
        self.assertEqual(without_truck.parent_id, parent)
        self.assertEqual(
            parent.parent_id,
            self.env.ref("taj_transport_operations.menu_transport_operations"),
        )
        self.assertEqual(parent.parent_id.name, "TAJ Operations")
