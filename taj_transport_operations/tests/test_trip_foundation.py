"""Acceptance tests for Phase 2, batch 1. Run with Odoo's --test-tags."""
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
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
            "category_id": cls.env.ref(
                "taj_transport_operations.fleet_category_truck"
            ).id,
        })
        cls.trailer_model = cls.env["fleet.vehicle.model"].create({
            "name": "Transport Test Trailer",
            "brand_id": cls.brand.id,
            "category_id": cls.env.ref(
                "taj_transport_operations.fleet_category_trailer"
            ).id,
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
        self.assertEqual(len(root.xpath("//group/filter")), 4)
        self.assertEqual(
            len(root.xpath("//filter[@name='by_transporter']")), 1
        )

    def test_batch_four_initial_assignment_preserves_sales_truck_and_invoice(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        self.assertFalse(trip.current_assignment_id)
        self.assertFalse(trip.current_vehicle_id)
        self.assertEqual(trip.initial_vehicle_id, self.truck)

        carrier_driver = self.env["res.partner"].create({
            "name": "Batch Four Driver",
        })
        co_driver = self.env["res.partner"].create({
            "name": "Batch Four Co-driver",
        })
        trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id,
            "license_plate": "TRL-001",
        })
        before_invoice_line = self.line_1._prepare_invoice_line()
        open_action = trip.action_open_initial_assignment_wizard()
        self.assertEqual(
            open_action["res_model"],
            "taj.transport.initial.assignment.wizard",
        )
        self.assertEqual(
            open_action["context"]["default_vehicle_id"], self.truck.id,
        )
        wizard = self.env[
            "taj.transport.initial.assignment.wizard"
        ].with_context(**open_action["context"]).create({
            "trip_id": trip.id,
            "trailer_id": trailer.id,
            "driver_id": carrier_driver.id,
            "co_driver_id": co_driver.id,
            "effective_location": "Dar es Salaam depot",
        })
        self.assertEqual(wizard.vehicle_id, self.truck)
        result = wizard.action_assign_initial_resources()
        self.assertEqual(result["res_id"], trip.id)
        self.assertEqual(len(trip.assignment_ids), 1)

        assignment = trip.current_assignment_id
        self.assertTrue(assignment)
        self.assertEqual(assignment.trip_id, trip)
        self.assertEqual(assignment.company_id, trip.company_id)
        self.assertEqual(assignment.vehicle_id, self.truck)
        self.assertEqual(assignment.driver_id, carrier_driver)
        self.assertEqual(assignment.co_driver_id, co_driver)
        self.assertEqual(assignment.trailer_id, trailer)
        self.assertEqual(assignment.assigned_by_id, self.env.user)
        self.assertFalse(assignment.effective_to)
        self.assertTrue(assignment.effective_from)
        self.assertEqual(
            assignment.effective_location, "Dar es Salaam depot",
        )
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertEqual(trip.current_driver_id, carrier_driver)
        self.assertEqual(trip.current_co_driver_id, co_driver)
        self.assertEqual(trip.current_trailer_id, trailer)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(self.line_1.vehicle_id, self.truck)
        self.assertEqual(self.line_1._prepare_invoice_line(), before_invoice_line)

        with self.assertRaises(UserError):
            trip.action_open_initial_assignment_wizard()
        with self.assertRaises((UserError, IntegrityError)):
            with self.env.cr.savepoint():
                self.env["taj.transport.assignment"].create({
                    "trip_id": trip.id,
                    "vehicle_id": self.truck_two.id,
                }).flush_recordset()

    def test_batch_four_driver_only_assignment_for_unassigned_draft_trip(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_no_truck.id,
        })
        driver = self.env["res.partner"].create({
            "name": "Driver without truck yet",
        })
        self.assertFalse(trip.initial_vehicle_id)
        wizard = self.env["taj.transport.initial.assignment.wizard"].create({
            "trip_id": trip.id,
            "driver_id": driver.id,
        })
        wizard.action_assign_initial_resources()
        self.assertEqual(trip.current_driver_id, driver)
        self.assertFalse(trip.current_vehicle_id)
        self.assertFalse(trip.initial_vehicle_id)
        self.assertEqual(trip.state, "draft")
        self.assertEqual(len(trip.assignment_ids), 1)

    def test_batch_four_rejects_same_driver_and_codriver(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        driver = self.env["res.partner"].create({
            "name": "A single driver contact",
        })
        with self.assertRaises((ValidationError, IntegrityError)):
            with self.env.cr.savepoint():
                self.env["taj.transport.assignment"].create({
                    "trip_id": trip.id,
                    "vehicle_id": self.truck.id,
                    "driver_id": driver.id,
                    "co_driver_id": driver.id,
                }).flush_recordset()

    def test_batch_four_rejects_empty_assignment_and_protects_history(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        with self.assertRaises((ValidationError, IntegrityError)):
            with self.env.cr.savepoint():
                self.env["taj.transport.assignment"].create({
                    "trip_id": trip.id,
                }).flush_recordset()

        assignment = self.env["taj.transport.assignment"].create({
            "trip_id": trip.id,
            "vehicle_id": self.truck.id,
        })
        with self.assertRaises(UserError):
            assignment.write({"driver_id": self.customer.id})
        with self.assertRaises(UserError):
            assignment.write({"effective_to": "2026-10-13 12:00:00"})
        with self.assertRaises(UserError):
            assignment.unlink()
        self.assertEqual(len(trip.assignment_ids), 1)
        self.assertFalse(assignment.effective_to)

    def test_batch_four_rejects_inconsistent_company(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        other_company = self.env["res.company"].create({
            "name": "Other Transport Test Company",
        })
        other_trailer = self.env["fleet.vehicle"].sudo().create({
            "model_id": self.trailer_model.id,
            "license_plate": "OTHER-TRAILER-001",
            "company_id": other_company.id,
        })
        with self.assertRaises((ValidationError, UserError, AccessError)):
            with self.env.cr.savepoint():
                self.env["taj.transport.assignment"].create({
                    "trip_id": trip.id,
                    "trailer_id": other_trailer.id,
                })

    def test_batch_four_resource_views_and_permissions(self):
        from lxml import etree

        form = self.env.ref(
            "taj_transport_operations.view_transport_trip_form"
        )
        root = etree.fromstring(form.get_combined_arch())
        self.assertEqual(
            len(root.xpath("//page[@name='trip_resources']")), 1,
        )
        self.assertEqual(
            len(root.xpath("//button[@name='action_open_initial_assignment_wizard']")),
            1,
        )
        for name in (
            "current_vehicle_id", "current_trailer_id",
            "current_driver_id", "current_co_driver_id",
        ):
            self.assertEqual(
                len(root.xpath(
                    f"//page[@name='trip_resources']//field[@name='{name}'][@readonly='1']"
                )), 1, name,
            )
        history = root.xpath(
            "//page[@name='trip_resources']//field[@name='assignment_ids']/list"
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].get("edit"), "0")
        self.assertEqual(history[0].get("delete"), "0")
        self.assertTrue(self.env.ref(
            "taj_transport_operations.menu_transport_trailers"
        ))
        self.assertTrue(self.env.ref(
            "taj_transport_operations.rule_transport_assignment_companies"
        ))
        assignment_dispatcher = self.env.ref(
            "taj_transport_operations.access_taj_transport_assignment_user"
        )
        fleet_dispatcher = self.env.ref(
            "taj_transport_operations.access_taj_transport_fleet_vehicle_read"
        )
        self.assertFalse(assignment_dispatcher.perm_write)
        self.assertFalse(assignment_dispatcher.perm_unlink)
        self.assertTrue(fleet_dispatcher.perm_read)
        self.assertFalse(fleet_dispatcher.perm_write)
        self.assertEqual(self.env.ref(
            "taj_transport_operations.action_transport_trailer"
        ).res_model, "fleet.vehicle")

    def test_assignment_rejects_wrong_fleet_categories(self):
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line_1.id})
        trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id, "license_plate": "TRL-CATEGORY",
        })
        for values in ({"trailer_id": self.truck.id}, {"vehicle_id": trailer.id}):
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.env["taj.transport.assignment"].create(dict(values, trip_id=trip.id))
        self.assertFalse(trip.assignment_ids)

    def test_assignment_rejects_archived_resources(self):
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line_1.id})
        self.truck.active = False
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["taj.transport.assignment"].create({
                "trip_id": trip.id, "vehicle_id": self.truck.id,
            })

    def test_model_change_cannot_reclassify_assigned_truck(self):
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line_1.id})
        assignment = self.env["taj.transport.assignment"].create({
            "trip_id": trip.id, "vehicle_id": self.truck.id,
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.truck.model_id = self.trailer_model
        self.assertEqual(assignment.vehicle_id, self.truck)
        self.assertEqual(trip.initial_vehicle_id, self.truck)

    def test_assignment_does_not_change_fleet_default_driver(self):
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line_1.id})
        self.truck.driver_id = self.customer
        driver = self.env["res.partner"].create({"name": "Independent trip driver"})
        self.env["taj.transport.assignment"].create({
            "trip_id": trip.id, "vehicle_id": self.truck.id, "driver_id": driver.id,
        })
        self.assertEqual(self.truck.driver_id, self.customer)
        self.assertEqual(trip.current_driver_id, driver)

    def test_dispatcher_cannot_edit_fleet_or_select_other_company(self):
        from odoo.tests.common import new_test_user

        user = new_test_user(self.env, login="transport_fleet_dispatcher",
            groups="taj_transport_operations.group_transport_user,sales_team.group_sale_salesman_all_leads")
        user.company_ids = self.env.company
        user.company_id = self.env.company
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line_1.id})
        self.assertEqual(self.truck.with_user(user).license_plate, "PH2-TEST-01")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.truck.with_user(user).write({"license_plate": "UNAUTHORIZED"})
        other_company = self.env["res.company"].create({"name": "Restricted Fleet Company"})
        other_trailer = self.env["fleet.vehicle"].sudo().create({
            "model_id": self.trailer_model.id, "company_id": other_company.id,
        })
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["taj.transport.assignment"].with_user(user).create({
                "trip_id": trip.id, "trailer_id": other_trailer.id,
            })

    def test_batch_three_route_timing_optional_and_independent_of_sales(self):
        before = self.line_1._prepare_invoice_line()
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        self.assertFalse(trip.origin_location)
        self.assertFalse(trip.destination_location)
        self.assertFalse(trip.planned_departure_at)
        self.assertFalse(trip.actual_arrival_at)

        trip.write({
            "origin_location": "Dar es Salaam",
            "destination_location": "Lusaka",
            "planned_departure_at": "2026-10-13 08:00:00",
            "planned_arrival_at": "2026-10-14 18:00:00",
            "actual_departure_at": "2026-10-13 08:20:00",
            "actual_arrival_at": "2026-10-14 19:00:00",
            "route_notes": "Confirm unloading appointment",
        })
        self.assertEqual(trip.origin_location, "Dar es Salaam")
        self.assertEqual(trip.destination_location, "Lusaka")
        self.assertEqual(trip.route_notes, "Confirm unloading appointment")
        self.assertEqual(
            trip.planned_arrival_at.strftime("%Y-%m-%d %H:%M:%S"),
            "2026-10-14 18:00:00",
        )
        self.assertEqual(
            trip.actual_arrival_at.strftime("%Y-%m-%d %H:%M:%S"),
            "2026-10-14 19:00:00",
        )
        self.assertEqual(trip.state, "draft")
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(
            self.line_1._prepare_invoice_line(), before,
        )
        for name in (
            "origin_location", "destination_location",
            "planned_departure_at", "planned_arrival_at",
            "actual_departure_at", "actual_arrival_at", "route_notes",
        ):
            self.assertTrue(trip._fields[name].tracking, name)

    def test_batch_three_rejects_reversed_planned_times(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                trip.write({
                    "planned_departure_at": "2026-10-14 09:00:00",
                    "planned_arrival_at": "2026-10-13 11:00:00",
                })

    def test_batch_three_rejects_reversed_actual_times(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self.env["taj.transport.trip"].create({
                    "sale_line_id": self.line_1.id,
                    "actual_departure_at": "2026-10-14 09:00:00",
                    "actual_arrival_at": "2026-10-13 11:00:00",
                })

    def test_batch_three_form_has_route_tab(self):
        from lxml import etree

        view = self.env.ref(
            "taj_transport_operations.view_transport_trip_form"
        )
        root = etree.fromstring(view.get_combined_arch())
        self.assertEqual(
            len(root.xpath("//page[@name='route_timing']")), 1,
        )
        for name in (
            "origin_location", "destination_location",
            "planned_departure_at", "planned_arrival_at",
            "actual_departure_at", "actual_arrival_at", "route_notes",
        ):
            self.assertEqual(
                len(root.xpath(f"//page[@name='route_timing']//field[@name='{name}']")),
                1, name,
            )

    def test_batch_two_commercial_source_fields_follow_sales(self):
        self.order.write({
            "client_order_ref": "PO-EXT-2026",
            "origin": "CUSTOMER-QUOTE-01",
        })
        self.line_1.write({"file_name": "FILE-TRANSPORT-01"})
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        self.assertEqual(trip.customer_reference, "PO-EXT-2026")
        self.assertEqual(trip.source_document, "CUSTOMER-QUOTE-01")
        self.assertEqual(trip.partner_id, self.customer)
        self.assertEqual(trip.price_unit, self.line_1.price_unit)
        self.assertEqual(trip.currency_id, self.order.currency_id)
        self.assertEqual(trip.payment_term_id, self.order.payment_term_id)
        self.assertEqual(trip.cargo_quantity, self.line_1.product_uom_qty)
        self.assertEqual(trip.cargo_uom_id, self.line_1.product_uom_id)
        self.assertEqual(trip.file_name, "FILE-TRANSPORT-01")

        self.order.write({"client_order_ref": "UPDATED-PO"})
        self.line_1.write({"file_name": "UPDATED-FILE"})
        self.assertEqual(trip.customer_reference, "UPDATED-PO")
        self.assertEqual(trip.file_name, "UPDATED-FILE")

        for name in (
            "customer_reference", "source_document", "payment_term_id",
            "cargo_quantity", "cargo_uom_id", "file_name",
        ):
            field = trip._fields[name]
            self.assertTrue(field.related, name)
            self.assertTrue(field.readonly, name)

    def test_batch_two_operational_fields_do_not_change_sales_or_invoices(self):
        carrier = self.env["res.partner"].create({
            "name": "Transport Test Carrier",
        })
        before = self.line_1._prepare_invoice_line()
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
            "transporter_id": carrier.id,
            "transport_reference": "BOOK-100",
            "cargo_type": "General cargo",
            "cargo_description": "Palletized goods",
            "special_handling_instructions": "Keep dry",
        })
        self.assertEqual(trip.transporter_id, carrier)
        self.assertEqual(trip.transport_reference, "BOOK-100")
        self.assertEqual(trip.cargo_type, "General cargo")
        self.assertEqual(trip.cargo_description, "Palletized goods")
        self.assertEqual(trip.special_handling_instructions, "Keep dry")

        trip.write({
            "transport_reference": "BOOK-101",
            "cargo_type": "Fragile cargo",
            "cargo_description": "Fragile palletized goods",
            "special_handling_instructions": "Do not stack",
        })
        self.assertEqual(trip.transport_reference, "BOOK-101")
        self.assertEqual(trip.cargo_type, "Fragile cargo")
        self.assertEqual(trip.cargo_description, "Fragile palletized goods")
        self.assertEqual(trip.special_handling_instructions, "Do not stack")
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(trip.state, "draft")
        after = self.line_1._prepare_invoice_line()
        for name in before:
            self.assertEqual(after[name], before[name], name)

        for name in (
            "transporter_id", "transport_reference", "cargo_type",
            "cargo_description", "special_handling_instructions",
        ):
            self.assertTrue(trip._fields[name].tracking, name)

    def test_batch_two_blank_operational_fields_remain_optional(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_no_truck.id,
        })
        self.assertFalse(trip.transporter_id)
        self.assertFalse(trip.transport_reference)
        self.assertFalse(trip.cargo_type)
        self.assertFalse(trip.cargo_description)
        self.assertFalse(trip.special_handling_instructions)
        trip.write({"cargo_type": "Bulk"})
        self.assertEqual(trip.cargo_type, "Bulk")
        self.assertFalse(trip.initial_vehicle_id)
        self.assertEqual(trip.sale_line_id.product_uom_qty, 3)

    def test_batch_two_form_separates_read_only_sales_and_operations(self):
        from lxml import etree

        view = self.env.ref(
            "taj_transport_operations.view_transport_trip_form"
        )
        root = etree.fromstring(view.get_combined_arch())
        for name in ("commercial_details", "cargo_details"):
            self.assertEqual(
                len(root.xpath(f"//page[@name='{name}']")), 1,
            )
        for name in (
            "customer_reference", "source_document", "payment_term_id",
            "cargo_quantity", "cargo_uom_id", "file_name",
            "sale_description", "price_unit", "currency_id",
        ):
            self.assertEqual(
                len(root.xpath(f"//field[@name='{name}'][@readonly='1']")),
                1,
                name,
            )
        for name in (
            "transporter_id", "transport_reference", "cargo_type",
            "cargo_description", "special_handling_instructions",
        ):
            self.assertEqual(
                len(root.xpath(f"//field[@name='{name}']")), 1, name,
            )
            self.assertFalse(
                root.xpath(f"//field[@name='{name}'][@readonly='1']"),
                name,
            )

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
