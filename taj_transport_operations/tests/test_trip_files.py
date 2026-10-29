"""Trip File generation, source identity, late trip links and Sales access."""
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestTripFiles(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Trip File customer"})
        cls.product = cls.env["product.product"].create({
            "name": "Trip File freight", "type": "service", "list_price": 100,
        })
        cls.brand = cls.env["fleet.vehicle.model.brand"].create({
            "name": "Trip File brand",
        })
        cls.truck_model = cls.env["fleet.vehicle.model"].create({
            "name": "Trip File truck model",
            "brand_id": cls.brand.id,
            "category_id": cls.env.ref(
                "taj_transport_operations.fleet_category_truck"
            ).id,
        })
        cls.truck = cls.env["fleet.vehicle"].create({
            "model_id": cls.truck_model.id,
            "license_plate": "TRIP-FILE-01",
        })

    def _order(self):
        return self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "order_line": [
                Command.create({
                    "product_id": self.product.id, "product_uom_qty": 5,
                    "container_num": "CONT-A", "price_unit": 100,
                    "vehicle_id": self.truck.id,
                }),
                Command.create({
                    "display_type": "line_section", "name": "Shipment notes",
                }),
                Command.create({
                    "product_id": self.product.id, "product_uom_qty": 1,
                    "container_num": "CONT-B", "price_unit": 100,
                    "vehicle_id": self.truck.id,
                }),
                Command.create({"display_type": "line_note", "name": "Keep dry"}),
            ],
        })

    def test_confirmation_creates_one_file_per_product_line_without_trips(self):
        order = self._order()
        self.assertFalse(order.trip_file_ids)
        order.action_confirm()
        files = order.trip_file_ids
        self.assertEqual(len(files), 2)
        self.assertEqual(order.trip_file_count, 2)
        self.assertEqual(len(set(files.mapped("name"))), 2)
        self.assertTrue(all(name.startswith("TF/") for name in files.mapped("name")))
        self.assertEqual(set(files.mapped("container_number")), {"CONT-A", "CONT-B"})
        self.assertFalse(any(files.mapped("bill_of_lading")))
        self.assertFalse(files.mapped("trip_id"))
        self.assertFalse(order.transport_trip_ids)
        self.assertEqual(files.mapped("sale_order_id"), order)

    def test_batch_confirmation_generates_distinct_references_for_all_orders(self):
        orders = self._order() | self._order()
        orders.action_confirm()
        files = orders.trip_file_ids
        self.assertEqual(len(files), 4)
        self.assertEqual(len(set(files.mapped("name"))), 4)

    def test_reconfirmation_reuses_files_and_preserves_container_snapshot(self):
        order = self._order()
        order.action_confirm()
        files = order.trip_file_ids
        files.write({"bill_of_lading": "BOL-RETAIN"})
        original = {file.id: (file.name, file.container_number) for file in files}
        files.sale_line_id.write({"container_num": "CHANGED-LATER"})
        if order.locked:
            order.action_unlock()
        order.action_cancel()
        self.assertEqual(order.trip_file_ids, files)
        order.action_draft()
        order.action_confirm()
        self.env["taj.transport.trip.file"]._ensure_for_sale_lines(order.order_line)
        self.assertEqual(order.trip_file_ids, files)
        self.assertEqual(original, {
            file.id: (file.name, file.container_number) for file in order.trip_file_ids
        })
        self.assertTrue(all(file.bill_of_lading == "BOL-RETAIN" for file in files))

    def test_each_trip_backfills_only_its_own_line_file(self):
        order = self._order()
        order.action_confirm()
        lines = order.order_line.filtered("product_id")
        trips = self.env["taj.transport.trip"].create([
            {"sale_line_id": line.id} for line in lines
        ])
        files = order.trip_file_ids
        files.flush_recordset(["trip_id"])
        self.assertEqual(len(files), 2)
        for file in files:
            self.assertEqual(file.trip_id, trips.filtered(
                lambda trip: trip.sale_line_id == file.sale_line_id
            ))
        with self.assertRaises(UserError):
            files[:1].write({"trip_id": trips[-1].id})
        with self.assertRaises(UserError):
            files[:1].write({"name": "FORGED-REFERENCE"})

    def test_line_added_after_confirmation_gets_file_with_optional_container(self):
        order = self._order()
        order.action_confirm()
        line = self.env["sale.order.line"].create({
            "order_id": order.id, "product_id": self.product.id,
            "product_uom_qty": 1, "price_unit": 100,
        })
        self.assertEqual(len(order.trip_file_ids), 3)
        file = order.trip_file_ids.filtered(lambda row: row.sale_line_id == line)
        self.assertEqual(len(file), 1)
        self.assertFalse(file.container_number)

    def test_older_order_gets_file_only_when_its_line_is_used_for_trip(self):
        order = self._order()
        # Represent an order confirmed before the module introduced Trip Files.
        order.write({"state": "sale"})
        self.assertFalse(order.trip_file_ids)
        line = order.order_line.filtered("product_id")[:1]
        trip = self.env["taj.transport.trip"].create({"sale_line_id": line.id})
        self.assertEqual(len(order.trip_file_ids), 1)
        self.assertEqual(order.trip_file_ids.trip_id, trip)
        self.assertEqual(order.trip_file_ids.sale_line_id, line)

    def test_order_and_trip_links_cannot_be_replaced_by_context_defaults(self):
        other_order = self._order()
        other_order.action_confirm()
        other_trip = self.env["taj.transport.trip"].create({
            "sale_line_id": other_order.order_line.filtered("product_id")[0].id,
        })
        order = self._order()
        order.with_context(
            default_sale_order_id=other_order.id, default_trip_id=other_trip.id,
        ).action_confirm()
        self.assertEqual(order.trip_file_ids.sale_order_id, order)
        self.assertFalse(order.trip_file_ids.trip_id)

    def test_copying_an_order_generates_new_files_only_on_confirmation(self):
        order = self._order()
        order.action_confirm()
        duplicate = order.copy()
        self.assertFalse(duplicate.trip_file_ids)
        duplicate.action_confirm()
        self.assertEqual(len(duplicate.trip_file_ids), 2)
        self.assertFalse(set(order.trip_file_ids.ids) & set(duplicate.trip_file_ids.ids))
        self.assertFalse(set(order.trip_file_ids.mapped("name")) &
                         set(duplicate.trip_file_ids.mapped("name")))

    def test_salesperson_can_confirm_and_edit_bill_without_transport_role(self):
        salesperson = new_test_user(
            self.env, login="trip_file_salesperson", company_id=self.env.company.id,
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        order = self._order()
        order.user_id = salesperson
        order.with_user(salesperson).action_confirm()
        files = order.trip_file_ids.with_user(salesperson)
        files.write({"bill_of_lading": "BOL-SALES"})
        self.assertEqual(files.mapped("bill_of_lading"), ["BOL-SALES", "BOL-SALES"])
        other_order = self._order()
        other_order.action_confirm()
        with self.assertRaises(AccessError):
            other_order.trip_file_ids.with_user(salesperson).read(["name"])
        with self.assertRaises(AccessError):
            self.env["taj.transport.trip.file"].with_user(salesperson).create({
                "sale_line_id": order.order_line.filtered("product_id")[0].id,
            })

    def test_transport_access_stays_within_allowed_companies(self):
        operator = new_test_user(
            self.env, login="trip_file_company_operator", company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        other_company = self.env["res.company"].create({"name": "Trip File other company"})
        other_order = self.env["sale.order"].with_company(other_company).create({
            "partner_id": self.customer.id,
            "order_line": [Command.create({
                "product_id": self.product.id, "product_uom_qty": 1, "price_unit": 100,
            })],
        })
        other_order.action_confirm()
        self.assertEqual(other_order.trip_file_ids.company_id, other_company)
        with self.assertRaises(AccessError):
            other_order.trip_file_ids.with_user(operator).with_context(
                allowed_company_ids=[self.env.company.id],
            ).read(["name"])
