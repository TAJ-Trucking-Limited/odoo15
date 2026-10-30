"""Dispatch worklists: trip prerequisites, the menus and their cutoff date."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
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

    def test_trip_creation_requires_a_truck(self):
        with self.assertRaises(ValidationError):
            self.env["taj.transport.trip"].create(
                {"sale_line_id": self.truckless_line.id}
            )
        self.assertFalse(self.truckless_line.transport_trip_ids)
        self.assertEqual(self.truckless_line.transport_trip_count, 0)

    def test_without_trip_action_lists_confirmed_untripped_lines(self):
        lines = (
            self._action_lines(
                "taj_transport_operations.action_sale_order_line_without_trip"
            )
            & self._our_lines()
        )
        self.assertEqual(lines, self._our_lines())

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
        self.assertNotIn(self.trucked_line, lines)
        self.assertIn(self.truckless_line, lines)
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
            "without_trip": self._our_lines(),
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
