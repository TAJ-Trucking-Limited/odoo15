"""Acceptance tests for Phase 2, batch 1. Run with Odoo's --test-tags."""
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTransportTripFoundation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({
            "name": "Trip test customer",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Transport service",
            "type": "service",
            "list_price": 500,
        })
        cls.brand = cls.env["fleet.vehicle.model.brand"].create({
            "name": "Transport Test Brand",
        })
        cls.model = cls.env["fleet.vehicle.model"].create({
            "name": "Transport Test Truck",
            "brand_id": cls.brand.id,
        })
        cls.truck = cls.env["fleet.vehicle"].create({
            "model_id": cls.model.id,
            "license_plate": "PH2-TEST-01",
        })
        cls.truck_two = cls.env["fleet.vehicle"].create({
            "model_id": cls.model.id,
            "license_plate": "PH2-TEST-02",
        })
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.customer.id,
        })
        cls.line_1 = cls.env["sale.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.product.id,
            "name": "Freight line 1",
            "product_uom_qty": 1,
            "price_unit": 500,
            "vehicle_id": cls.truck.id,
            "container_num": "TRIP-CONT-1",
            "srn": "TRIP-SRN-1",
        })
        cls.line_2 = cls.env["sale.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.product.id,
            "name": "Freight line 2",
            "product_uom_qty": 1,
            "price_unit": 600,
            "vehicle_id": cls.truck.id,
        })
        cls.line_no_truck = cls.env["sale.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.product.id,
            "name": "Freight awaiting truck",
            "product_uom_qty": 3,
            "price_unit": 200,
        })
        cls.note_line = cls.env["sale.order.line"].create({
            "order_id": cls.order.id,
            "display_type": "line_note",
            "name": "Internal reminder",
        })
        cls.order.action_confirm()

    def test_confirmation_does_not_automatically_create_trips(self):
        self.assertFalse(self.order.transport_trip_ids)

    def test_one_trip_per_sales_line_and_original_snapshot(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        self.assertTrue(trip.name.startswith("TRIP/"))
        self.assertEqual(trip.state, "draft")
        self.assertEqual(trip.sale_order_id, self.order)
        self.assertEqual(trip.partner_id, self.customer)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(trip.container_num, "TRIP-CONT-1")
        self.assertEqual(trip.srn, "TRIP-SRN-1")
        self.assertEqual(self.order.transport_trip_count, 1)
        self.assertEqual(trip.sale_line_id, self.line_1)

        self.line_1.write({"vehicle_id": self.truck_two.id})
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(self.line_1.vehicle_id, self.truck_two)

    def test_duplicate_trip_rejected_by_sql_constraint(self):
        trip_model = self.env["taj.transport.trip"]
        trip_model.create({"sale_line_id": self.line_1.id})
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                duplicate = trip_model.create({
                    "sale_line_id": self.line_1.id,
                })
                duplicate.flush_recordset()

    def test_unconfirmed_and_note_lines_cannot_create_trips(self):
        draft_order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
        })
        draft_line = self.env["sale.order.line"].create({
            "order_id": draft_order.id,
            "product_id": self.product.id,
            "product_uom_qty": 1,
            "price_unit": 100,
        })
        with self.assertRaises(ValidationError):
            self.env["taj.transport.trip"].create({
                "sale_line_id": draft_line.id,
            })
        with self.assertRaises(ValidationError):
            self.env["taj.transport.trip"].create({
                "sale_line_id": self.note_line.id,
            })

    def test_trip_without_truck_and_quantity_not_one(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_no_truck.id,
        })
        self.assertFalse(trip.initial_vehicle_id)
        self.assertEqual(trip.sale_line_id.product_uom_qty, 3)
        self.assertEqual(len(self.line_no_truck.transport_trip_ids), 1)

    def test_source_and_status_immutable(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        with self.assertRaises(UserError):
            trip.write({"sale_line_id": self.line_2.id})
        with self.assertRaises(UserError):
            trip.write({"name": "CUSTOM"})
        with self.assertRaises(UserError):
            trip.write({"initial_vehicle_id": self.truck_two.id})
        with self.assertRaises(UserError):
            trip.write({"state": "completed"})

    def test_line_action_is_idempotent(self):
        first = self.line_1.action_open_or_create_transport_trip()
        again = self.line_1.action_open_or_create_transport_trip()
        self.assertEqual(first["res_id"], again["res_id"])
        self.assertEqual(len(self.line_1.transport_trip_ids), 1)

    def test_wizard_defaults_only_truck_assigned_lines(self):
        defaults = self.env["taj.transport.trip.create.wizard"].with_context(
            default_sale_order_id=self.order.id
        ).default_get(["sale_order_id", "line_ids"])
        self.assertEqual(defaults["sale_order_id"], self.order.id)
        self.assertEqual(
            defaults["line_ids"], [Command.set(
                (self.line_1 | self.line_2).ids
            )],
        )

    def test_wizard_creates_only_selected_lines_and_reuses_existing(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        wizard = self.env["taj.transport.trip.create.wizard"].create({
            "sale_order_id": self.order.id,
            "line_ids": [Command.set([
                self.line_1.id,
                self.line_2.id,
                self.line_no_truck.id,
            ])],
        })
        result = wizard.action_create_trips()
        self.assertEqual(result["res_model"], "taj.transport.trip")
        self.assertEqual(
            len(self.order.transport_trip_ids), 3
        )
        self.assertEqual(
            self.line_1.transport_trip_ids, trip
        )
        again = wizard.action_create_trips()
        self.assertEqual(
            len(self.order.transport_trip_ids), 3
        )
        self.assertEqual(again["res_model"], "taj.transport.trip")

    def test_wizard_rejects_lines_from_other_sales_order(self):
        second_order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
        })
        other_line = self.env["sale.order.line"].create({
            "order_id": second_order.id,
            "product_id": self.product.id,
            "price_unit": 250,
            "product_uom_qty": 1,
        })
        second_order.action_confirm()
        wizard = self.env["taj.transport.trip.create.wizard"].create({
            "sale_order_id": self.order.id,
            "line_ids": [Command.set([other_line.id])],
        })
        with self.assertRaises(UserError):
            wizard.action_create_trips()

    def test_invoice_data_is_not_modified_by_trip_creation(self):
        before = self.line_1._prepare_invoice_line()
        self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        after = self.line_1._prepare_invoice_line()
        for key in (
            "vehicle_id", "container_num", "srn", "route_id", "price_unit",
        ):
            self.assertEqual(after[key], before[key])

    def test_search_view_uses_odoo_19_group_syntax(self):
        # Odoo 19 search-group nodes reject legacy expand/string attributes.
        from lxml import etree

        view = self.env.ref(
            "taj_transport_operations.view_transport_trip_search"
        )
        root = etree.fromstring(view.get_combined_arch())
        self.assertEqual(root.tag, "search")
        self.assertFalse(root.xpath("//group[@expand or @string]"))
        self.assertEqual(len(root.xpath("//group/filter")), 3)

    def test_sales_order_buttons_and_combined_view(self):
        with self.assertRaises(UserError):
            draft = self.env["sale.order"].create({
                "partner_id": self.customer.id,
            })
            draft.action_prepare_transport_trips()
        wizard_action = self.order.action_prepare_transport_trips()
        self.assertEqual(
            wizard_action["res_model"], "taj.transport.trip.create.wizard"
        )
        view_action = self.order.action_view_transport_trips()
        self.assertEqual(view_action["res_model"], "taj.transport.trip")
        view = self.env.ref(
            "taj_transport_operations.view_sale_order_form_transport"
        )
        from lxml import etree
        root = etree.fromstring(view.get_combined_arch())
        names = root.xpath("//button/@name")
        self.assertIn("action_prepare_transport_trips", names)
        self.assertIn("action_open_or_create_transport_trip", names)
