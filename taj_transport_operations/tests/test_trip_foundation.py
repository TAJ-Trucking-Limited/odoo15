"""Acceptance tests for Phase 2, batch 1. Run with Odoo's --test-tags."""
from contextlib import contextmanager
from datetime import timedelta

from psycopg2 import IntegrityError
from psycopg2.errors import CheckViolation

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@contextmanager
def assert_raises_any(*exceptions):
    """``assertRaises`` accepting several exception classes.

    Odoo 19's ``TestCase.assertRaises`` calls ``issubclass(exception,
    AccessError)`` internally and therefore rejects a tuple of classes. A
    violated PostgreSQL CHECK constraint also reaches a test unwrapped as
    ``CheckViolation``, so database-level refusals accept it too.
    """
    try:
        yield
    except exceptions:
        return
    raise AssertionError(
        "Expected one of %s to be raised."
        % ", ".join(exception.__name__ for exception in exceptions)
    )


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
        with assert_raises_any(UserError, IntegrityError, CheckViolation):
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
        with assert_raises_any(ValidationError, IntegrityError, CheckViolation):
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
        with assert_raises_any(ValidationError, IntegrityError, CheckViolation):
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
        with assert_raises_any(ValidationError, UserError, AccessError):
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

    def test_assigned_truck_and_trailer_cannot_change_company(self):
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line_1.id})
        trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id, "license_plate": "COMPANY-GUARD",
        })
        assignment = self.env["taj.transport.assignment"].create({
            "trip_id": trip.id, "vehicle_id": self.truck.id, "trailer_id": trailer.id,
        })
        other_company = self.env["res.company"].create({"name": "Fleet Transfer Company"})
        resources = self.truck | trailer
        for resource in (self.truck, trailer, resources):
            for company_id in (other_company.id, False):
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    resource.sudo().write({"company_id": company_id})
        resources.write({"company_id": trip.company_id.id})  # A no-op stays valid.
        self.assertEqual(assignment.company_id, trip.company_id)
        self.assertEqual(self.truck.company_id, trip.company_id)
        self.assertEqual(trailer.company_id, trip.company_id)
        self.assertEqual(trip.initial_vehicle_id, self.truck)

    def test_closed_assignment_also_prevents_company_transfer(self):
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line_1.id})
        assignment = self.env["taj.transport.assignment"].create({
            "trip_id": trip.id, "vehicle_id": self.truck.id,
            "effective_from": "2026-10-01 08:00:00",
        })
        # Historical period fixture: production has no closing workflow yet.
        assignment.flush_recordset()
        self.env.cr.execute("UPDATE taj_transport_assignment SET effective_to = %s WHERE id = %s",
                            ("2026-10-01 12:00:00", assignment.id))
        assignment.invalidate_recordset(["effective_to"])
        other_company = self.env["res.company"].create({"name": "Historical Transfer Company"})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.truck.sudo().write({"company_id": other_company.id})
        self.assertEqual(assignment.vehicle_id, self.truck)
        self.assertEqual(self.truck.company_id, trip.company_id)

    def test_unassigned_vehicle_can_change_company(self):
        other_company = self.env["res.company"].create({"name": "Unassigned Transfer Company"})
        self.truck_two.sudo().write({"company_id": other_company.id})
        self.assertEqual(self.truck_two.company_id, other_company)

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

    def _replacement_fixture(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id,
            "license_plate": "REPLACEMENT-TRAILER",
        })
        driver = self.env["res.partner"].create({
            "name": "Original replacement-test driver",
        })
        started = fields.Datetime.now() - timedelta(hours=6)
        original = self.env["taj.transport.assignment"].create({
            "trip_id": trip.id,
            "vehicle_id": self.truck.id,
            "trailer_id": trailer.id,
            "driver_id": driver.id,
            "effective_from": started,
            "effective_location": "Origin Depot",
        })
        return trip, original, trailer, driver

    def _replacement_wizard(self, trip, effective_from, **overrides):
        action = trip.action_open_replace_assignment_wizard()
        vals = {
            "trip_id": trip.id,
            "expected_assignment_id": action["context"]["default_expected_assignment_id"],
            "effective_from": effective_from,
            "reason": "Truck replacement",
        }
        vals.update(overrides)
        return self.env[
            "taj.transport.replace.assignment.wizard"
        ].with_context(**action["context"]).create(vals)

    def test_replace_resources_preserves_history_sales_and_unchanged_crew(self):
        trip, original, trailer, driver = self._replacement_fixture()
        before_invoice = self.line_1._prepare_invoice_line()
        replacement_at = original.effective_from + timedelta(hours=2)
        wizard = self._replacement_wizard(
            trip, replacement_at,
            vehicle_id=self.truck_two.id,
            effective_location="Nakonde",
            reason="Truck breakdown",
            notes="Replacement truck sent by dispatcher",
        )
        self.assertEqual(wizard.trailer_id, trailer)
        self.assertEqual(wizard.driver_id, driver)
        result = wizard.action_replace_resources()
        self.assertEqual(result["res_id"], trip.id)
        original.invalidate_recordset()
        trip.invalidate_recordset()
        current = trip.current_assignment_id
        self.assertTrue(current)
        self.assertNotEqual(current, original)
        self.assertEqual(len(trip.assignment_ids), 2)
        self.assertEqual(original.effective_to, replacement_at)
        self.assertEqual(original.vehicle_id, self.truck)
        self.assertEqual(original.driver_id, driver)
        self.assertEqual(current.effective_from, replacement_at)
        self.assertFalse(current.effective_to)
        self.assertEqual(current.vehicle_id, self.truck_two)
        self.assertEqual(current.trailer_id, trailer)
        self.assertEqual(current.driver_id, driver)
        self.assertFalse(current.co_driver_id)
        self.assertEqual(current.assigned_by_id, self.env.user)
        self.assertEqual(current.effective_location, "Nakonde")
        self.assertEqual(current.reason, "Truck breakdown")
        self.assertEqual(current.notes, "Replacement truck sent by dispatcher")
        self.assertEqual(self.line_1._prepare_invoice_line(), before_invoice)
        self.assertEqual(self.line_1.vehicle_id, self.truck)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(trip.state, "draft")
        self.assertIn(
            "Truck breakdown", " ".join(trip.message_ids.mapped("body")),
        )

    def test_two_sequential_replacements_keep_contiguous_history(self):
        trip, original, trailer, driver = self._replacement_fixture()
        at_one = original.effective_from + timedelta(hours=1)
        at_two = original.effective_from + timedelta(hours=3)
        first = self._replacement_wizard(
            trip, at_one, driver_id=self.customer.id,
        )
        first.action_replace_resources()
        middle = trip.current_assignment_id
        self.assertEqual(middle.driver_id, self.customer)
        self.assertEqual(middle.vehicle_id, self.truck)
        self._replacement_wizard(
            trip, at_two, vehicle_id=self.truck_two.id,
        ).action_replace_resources()
        original.invalidate_recordset()
        middle.invalidate_recordset()
        trip.invalidate_recordset()
        current = trip.current_assignment_id
        self.assertEqual(len(trip.assignment_ids), 3)
        self.assertEqual(original.effective_to, at_one)
        self.assertEqual(middle.effective_from, at_one)
        self.assertEqual(middle.effective_to, at_two)
        self.assertEqual(current.effective_from, at_two)
        self.assertFalse(current.effective_to)
        self.assertEqual(current.vehicle_id, self.truck_two)
        self.assertEqual(current.driver_id, self.customer)
        self.assertEqual(current.trailer_id, trailer)
        self.assertEqual(original.driver_id, driver)
        self.assertEqual(trip.initial_vehicle_id, self.truck)

    def test_replacement_can_swap_trailer_and_clear_codriver(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        trailer_before = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id,
            "license_plate": "PREVIOUS-TRAILER",
        })
        trailer_after = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id,
            "license_plate": "NEW-TRAILER",
        })
        driver = self.env["res.partner"].create({"name": "Primary"})
        co_driver = self.env["res.partner"].create({"name": "Previous co-driver"})
        old = self.env["taj.transport.assignment"].create({
            "trip_id": trip.id, "vehicle_id": self.truck.id,
            "trailer_id": trailer_before.id, "driver_id": driver.id,
            "co_driver_id": co_driver.id,
            "effective_from": fields.Datetime.now() - timedelta(hours=3),
        })
        at = old.effective_from + timedelta(hours=1)
        self._replacement_wizard(
            trip, at, trailer_id=trailer_after.id, co_driver_id=False,
        ).action_replace_resources()
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertEqual(trip.current_driver_id, driver)
        self.assertEqual(trip.current_trailer_id, trailer_after)
        self.assertFalse(trip.current_co_driver_id)
        self.assertEqual(old.trailer_id, trailer_before)
        self.assertEqual(old.co_driver_id, co_driver)
        self.assertEqual(old.effective_to, at)

    def test_stale_replace_dialog_cannot_replace_newer_assignment(self):
        trip, original, trailer, driver = self._replacement_fixture()
        replacement_at = original.effective_from + timedelta(hours=2)
        stale = self._replacement_wizard(
            trip, replacement_at + timedelta(minutes=20),
            vehicle_id=self.truck_two.id,
        )
        self._replacement_wizard(
            trip, replacement_at, driver_id=self.customer.id,
        ).action_replace_resources()
        current = trip.current_assignment_id
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                stale.action_replace_resources()
        current.invalidate_recordset()
        self.assertFalse(current.effective_to)
        self.assertEqual(trip.current_assignment_id, current)
        self.assertEqual(len(trip.assignment_ids), 2)

    def test_replace_resources_rejects_no_change_and_empty_resources(self):
        trip, original, trailer, driver = self._replacement_fixture()
        at = original.effective_from + timedelta(hours=1)
        for values in (
            {},
            dict(vehicle_id=False, trailer_id=False,
                 driver_id=False, co_driver_id=False),
        ):
            wizard = self._replacement_wizard(trip, at, **values)
            with self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    wizard.action_replace_resources()
        original.invalidate_recordset()
        self.assertFalse(original.effective_to)
        self.assertEqual(len(trip.assignment_ids), 1)

    def test_replace_resources_rejects_bad_dates_and_missing_reason(self):
        trip, original, trailer, driver = self._replacement_fixture()
        for effective_at in (
            original.effective_from,
            original.effective_from - timedelta(minutes=1),
            fields.Datetime.now() + timedelta(hours=1),
        ):
            wizard = self._replacement_wizard(
                trip, effective_at, vehicle_id=self.truck_two.id,
            )
            with self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    wizard.action_replace_resources()
        wizard = self._replacement_wizard(
            trip, original.effective_from + timedelta(hours=1),
            vehicle_id=self.truck_two.id, reason="   ",
        )
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                wizard.action_replace_resources()
        original.invalidate_recordset()
        self.assertFalse(original.effective_to)

    def test_failed_replacement_rolls_back_old_closure_atomically(self):
        trip, original, trailer, driver = self._replacement_fixture()
        invalid_driver = self._replacement_wizard(
            trip, original.effective_from + timedelta(hours=2),
            driver_id=driver.id, co_driver_id=driver.id,
        )
        with assert_raises_any(ValidationError, CheckViolation):
            with self.env.cr.savepoint():
                invalid_driver.action_replace_resources()
        bad_category = self._replacement_wizard(
            trip, original.effective_from + timedelta(hours=2),
            trailer_id=self.truck_two.id,
        )
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                bad_category.action_replace_resources()
        original.invalidate_recordset()
        trip.invalidate_recordset()
        self.assertFalse(original.effective_to)
        self.assertEqual(trip.current_assignment_id, original)
        self.assertEqual(len(trip.assignment_ids), 1)

    def test_cross_company_resource_failure_preserves_current_assignment(self):
        trip, original, trailer, driver = self._replacement_fixture()
        other_company = self.env["res.company"].create({
            "name": "Replacement Restricted Company",
        })
        other_trailer = self.env["fleet.vehicle"].sudo().create({
            "model_id": self.trailer_model.id,
            "license_plate": "RESTRICTED-TRAILER",
            "company_id": other_company.id,
        })
        wizard = self._replacement_wizard(
            trip, original.effective_from + timedelta(hours=2),
            trailer_id=other_trailer.id,
        )
        with assert_raises_any(ValidationError, UserError, AccessError):
            with self.env.cr.savepoint():
                wizard.action_replace_resources()
        original.invalidate_recordset()
        trip.invalidate_recordset()
        self.assertFalse(original.effective_to)
        self.assertEqual(trip.current_assignment_id, original)

    def test_archived_current_truck_can_be_replaced_preserving_old_history(self):
        trip, original, trailer, driver = self._replacement_fixture()
        self.truck.active = False
        replacement_at = original.effective_from + timedelta(hours=2)
        self._replacement_wizard(
            trip, replacement_at, vehicle_id=self.truck_two.id,
        ).action_replace_resources()
        original.invalidate_recordset()
        self.assertEqual(original.vehicle_id, self.truck)
        self.assertEqual(original.effective_to, replacement_at)
        self.assertFalse(self.truck.active)
        self.assertEqual(trip.current_vehicle_id, self.truck_two)

    def test_immutable_history_and_direct_initial_assignment_bypass_blocked(self):
        trip, original, trailer, driver = self._replacement_fixture()
        self._replacement_wizard(
            trip, original.effective_from + timedelta(hours=2),
            vehicle_id=self.truck_two.id,
        ).action_replace_resources()
        with self.assertRaises(UserError):
            original.write({"effective_to": fields.Datetime.now()})
        with self.assertRaises(UserError):
            original.write({"vehicle_id": self.truck_two.id})
        with self.assertRaises(UserError):
            original.unlink()
        with self.assertRaises(UserError):
            self.env["taj.transport.assignment"].create({
                "trip_id": trip.id, "vehicle_id": self.truck.id,
            })
        self.assertEqual(len(trip.assignment_ids), 2)

    def test_dispatcher_can_replace_without_general_assignment_write_acl(self):
        from odoo.tests.common import new_test_user

        trip, original, trailer, driver = self._replacement_fixture()
        dispatcher = new_test_user(
            self.env, login="transport_replacement_dispatcher",
            groups="taj_transport_operations.group_transport_user,sales_team.group_sale_salesman_all_leads",
        )
        dispatcher.company_ids = self.env.company
        dispatcher.company_id = self.env.company
        with self.assertRaises(UserError):
            original.with_user(dispatcher).write({
                "effective_to": fields.Datetime.now(),
            })
        # The dispatcher creates and runs *their own* transient wizard.
        # Direct history edits remain forbidden even to authorized dispatchers.
        action = trip.with_user(dispatcher).action_open_replace_assignment_wizard()
        wizard = self.env[
            "taj.transport.replace.assignment.wizard"
        ].with_user(dispatcher).with_context(**action["context"]).create({
            "trip_id": trip.id,
            "expected_assignment_id": original.id,
            "effective_from": original.effective_from + timedelta(hours=1),
            "vehicle_id": self.truck_two.id,
            "reason": "Dispatcher-approved replacement",
        })
        wizard.action_replace_resources()
        self.assertEqual(trip.current_assignment_id.vehicle_id, self.truck_two)
        self.assertEqual(trip.current_assignment_id.assigned_by_id, dispatcher)

    def test_closed_history_cannot_overlap_new_assignment(self):
        trip, original, trailer, driver = self._replacement_fixture()
        closed_at = original.effective_from + timedelta(hours=3)
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                # Simulate a faulty internal workflow trying to backdate a new
                # assignment over an existing closed interval.
                original._close_for_replacement(closed_at)
                self.env["taj.transport.assignment"]._create_from_replacement({
                    "trip_id": trip.id,
                    "vehicle_id": self.truck_two.id,
                    "effective_from": closed_at - timedelta(hours=1),
                    "reason": "Invalid overlapping period",
                })
        original.invalidate_recordset()
        trip.invalidate_recordset()
        self.assertFalse(original.effective_to)
        self.assertEqual(trip.current_assignment_id, original)

    def test_replace_resources_form_and_access(self):
        from lxml import etree

        trip, original, trailer, driver = self._replacement_fixture()
        action = trip.action_open_replace_assignment_wizard()
        self.assertEqual(
            action["res_model"], "taj.transport.replace.assignment.wizard",
        )
        self.assertEqual(
            action["context"]["default_expected_assignment_id"], original.id,
        )
        self.assertEqual(action["context"]["default_trailer_id"], trailer.id)
        self.assertEqual(action["context"]["default_driver_id"], driver.id)
        form = self.env.ref(
            "taj_transport_operations.view_transport_trip_form"
        )
        root = etree.fromstring(form.get_combined_arch())
        self.assertEqual(
            len(root.xpath(
                "//page[@name='trip_resources']"
                "//button[@name='action_open_replace_assignment_wizard']"
            )), 1,
        )
        wizard_view = self.env.ref(
            "taj_transport_operations.view_replace_resource_assignment_wizard_form"
        )
        wizard_root = etree.fromstring(wizard_view.get_combined_arch())
        self.assertEqual(
            len(wizard_root.xpath("//button[@name='action_replace_resources']")), 1,
        )
        self.assertEqual(
            len(wizard_root.xpath("//field[@name='expected_assignment_id']")), 1,
        )
        access = self.env.ref(
            "taj_transport_operations.access_taj_transport_replace_wizard_user"
        )
        self.assertTrue(access.perm_create)
        self.assertTrue(access.perm_write)
        self.assertFalse(access.perm_unlink)

    def test_transport_company_record_rules_hide_other_company_history(self):
        from odoo.tests.common import new_test_user

        own_trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        own_assignment = self.env["taj.transport.assignment"].create({
            "trip_id": own_trip.id,
            "vehicle_id": self.truck.id,
        })
        other_company = self.env["res.company"].create({
            "name": "Transport security isolated company",
        })
        other_order = self.env["sale.order"].sudo().with_company(
            other_company
        ).create({
            "company_id": other_company.id,
            "partner_id": self.customer.id,
        })
        other_line = self.env["sale.order.line"].sudo().with_company(
            other_company
        ).create({
            "order_id": other_order.id,
            "company_id": other_company.id,
            "product_id": self.product.id,
            "product_uom_qty": 1,
            "price_unit": 95,
        })
        other_order.action_confirm()
        foreign_trip = self.env["taj.transport.trip"].sudo().with_company(
            other_company
        ).create({
            "sale_line_id": other_line.id,
        })
        foreign_assignment = self.env[
            "taj.transport.assignment"
        ].sudo().with_company(other_company).create({
            "trip_id": foreign_trip.id,
            "driver_id": self.customer.id,
        })

        dispatcher = new_test_user(
            self.env,
            login="cross_company_transport_dispatcher",
            groups=(
                "taj_transport_operations.group_transport_user,"
                "sales_team.group_sale_salesman_all_leads"
            ),
        )
        dispatcher.company_ids = self.env.company
        dispatcher.company_id = self.env.company
        visible_trips = self.env["taj.transport.trip"].with_user(
            dispatcher
        ).search([("id", "in", (own_trip | foreign_trip).ids)])
        self.assertEqual(visible_trips, own_trip)

        visible_assignments = self.env[
            "taj.transport.assignment"
        ].with_user(dispatcher).search([
            ("id", "in", (own_assignment | foreign_assignment).ids),
        ])
        self.assertEqual(visible_assignments, own_assignment)
        with self.assertRaises(AccessError):
            foreign_trip.with_user(dispatcher).check_access("read")
        with self.assertRaises(AccessError):
            foreign_trip.with_user(dispatcher).action_mark_ready()
        with self.assertRaises(AccessError):
            foreign_assignment.with_user(dispatcher).check_access("read")

    def test_uncategorized_or_mismatched_fleet_vehicle_error_is_actionable(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id,
            "license_plate": "TRUCK-CATEGORY-MISMATCH",
        })
        with self.assertRaisesRegex(ValidationError, "expected Truck"):
            with self.env.cr.savepoint():
                self.env["taj.transport.assignment"].create({
                    "trip_id": trip.id,
                    "vehicle_id": trailer.id,
                })
        self.assertFalse(trip.current_assignment_id)

    def test_product_name_route_parser_strips_cargo_specifications(self):
        Trip = self.env["taj.transport.trip"]
        self.assertEqual(
            Trip._route_from_product_name(
                "DRC kasumbalesa - TZ Dar (40ft, Normal, No Special Requirements)"
            ),
            ("DRC kasumbalesa", "TZ Dar"),
        )
        self.assertEqual(
            Trip._route_from_product_name(
                "  Dar es Salaam \u2013 Lusaka (20ft Reefer) "
            ),
            ("Dar es Salaam", "Lusaka"),
        )
        self.assertEqual(
            Trip._route_from_product_name("Dar (Port) - Lusaka (Destination)"),
            ("Dar (Port)", "Lusaka (Destination)"),
        )
        self.assertEqual(
            Trip._route_from_product_name(
                "Kasumbalesa - Dar (40ft - Normal - No Special Requirements)"
            ),
            ("Kasumbalesa", "Dar"),
        )
        for ambiguous in (
            "Freight service",
            "DRC Kasumbalesa - TZ Dar - Border Point",
            "No spaces-TZ Dar",
            "DRC - ",
            "",
        ):
            self.assertEqual(
                Trip._route_from_product_name(ambiguous),
                (False, False),
                ambiguous,
            )

    def test_new_trip_prefills_route_from_product_not_sale_line_description(self):
        self.product.product_tmpl_id.write({
            "name": "DRC kasumbalesa - TZ Dar (40ft, Normal, No Special Requirements)",
        })
        # The Sales description deliberately differs from the Product name.
        self.assertEqual(self.line_1.name, "Freight line 1")
        before_invoice = self.line_1._prepare_invoice_line()
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        self.assertEqual(trip.origin_location, "DRC kasumbalesa")
        self.assertEqual(trip.destination_location, "TZ Dar")
        self.assertEqual(trip.state, "draft")
        self.assertEqual(self.line_1._prepare_invoice_line(), before_invoice)

        # Changing the product catalog later must not change trip operations.
        self.product.product_tmpl_id.write({
            "name": "TZ Dar - DRC kasumbalesa (20ft, Normal)",
        })
        self.assertEqual(trip.origin_location, "DRC kasumbalesa")
        self.assertEqual(trip.destination_location, "TZ Dar")

    def test_sales_buttons_and_batch_wizard_use_same_product_route_defaults(self):
        self.product.product_tmpl_id.write({
            "name": "DRC kasumbalesa - TZ Dar (40ft, Normal, No Special Requirements)",
        })
        action = self.line_1.action_open_or_create_transport_trip()
        trip = self.env["taj.transport.trip"].browse(action["res_id"])
        self.assertEqual(trip.origin_location, "DRC kasumbalesa")
        self.assertEqual(trip.destination_location, "TZ Dar")

        wizard = self.env["taj.transport.trip.create.wizard"].create({
            "sale_order_id": self.order.id,
            "line_ids": [Command.set([self.line_2.id])],
        })
        action = wizard.action_create_trips()
        created = self.env["taj.transport.trip"].browse(action["res_id"])
        self.assertEqual(created.origin_location, "DRC kasumbalesa")
        self.assertEqual(created.destination_location, "TZ Dar")
        self.assertEqual(len(self.order.transport_trip_ids), 2)

    def test_explicit_origin_wins_and_operator_may_edit_product_suggestion(self):
        self.product.product_tmpl_id.write({
            "name": "DRC kasumbalesa - TZ Dar (40ft, Normal, No Special Requirements)",
        })
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
            "origin_location": "Custom loading location",
        })
        self.assertEqual(trip.origin_location, "Custom loading location")
        self.assertEqual(trip.destination_location, "TZ Dar")
        trip.write({"destination_location": "Manual destination"})
        self.assertEqual(trip.destination_location, "Manual destination")
        trip.action_prefill_route_from_product()
        self.assertEqual(trip.origin_location, "Custom loading location")
        self.assertEqual(trip.destination_location, "Manual destination")

    def test_existing_draft_can_fill_missing_route_without_overwriting_origin(self):
        # The trip predates a route-like product name. Existing Trips are
        # deliberately NOT overwritten by module upgrades.
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
            "origin_location": "Existing custom origin",
        })
        self.assertFalse(trip.destination_location)
        self.product.product_tmpl_id.write({
            "name": "DRC kasumbalesa - TZ Dar (40ft, Normal, No Special Requirements)",
        })
        action = trip.action_prefill_route_from_product()
        self.assertEqual(action, {"type": "ir.actions.client", "tag": "reload"})
        self.assertEqual(trip.origin_location, "Existing custom origin")
        self.assertEqual(trip.destination_location, "TZ Dar")

    def test_manual_new_trip_form_onchange_suggests_missing_route_only(self):
        self.product.product_tmpl_id.write({
            "name": "DRC kasumbalesa - TZ Dar (40ft, Normal, No Special Requirements)",
        })
        preview = self.env["taj.transport.trip"].new({
            "sale_line_id": self.line_1.id,
            "origin_location": "Entered in form",
        })
        preview._onchange_sale_line_suggest_route()
        self.assertEqual(preview.origin_location, "Entered in form")
        self.assertEqual(preview.destination_location, "TZ Dar")

    def test_ambiguous_product_does_not_invent_route(self):
        self.product.product_tmpl_id.write({
            "name": "DRC - TZ Dar - Extra Stop",
        })
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        self.assertFalse(trip.origin_location)
        self.assertFalse(trip.destination_location)
        with self.assertRaises(UserError):
            trip.action_prefill_route_from_product()

    def test_route_suggestion_button_only_available_in_draft(self):
        self.product.product_tmpl_id.write({
            "name": "DRC kasumbalesa - TZ Dar (40ft, Normal, No Special Requirements)",
        })
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        trip._cancel_with_reason("QA cancelled before assignment")
        with self.assertRaises(UserError):
            trip.action_prefill_route_from_product()

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
        carrier = self.env["taj.transport.transporter"].create({
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
        self.assertEqual(
            trip.cargo_type_id,
            self.env.ref("taj_transport_operations.cargo_type_general"),
        )
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
        self.assertEqual(
            trip.cargo_type_id,
            self.env.ref("taj_transport_operations.cargo_type_fragile"),
        )
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
        self.assertEqual(
            trip.cargo_type_id,
            self.env.ref("taj_transport_operations.cargo_type_bulk"),
        )
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
            "transporter_id", "transport_reference", "cargo_type_id",
            "cargo_description", "special_handling_instructions",
        ):
            self.assertEqual(
                len(root.xpath(f"//field[@name='{name}']")), 1, name,
            )
            self.assertFalse(
                root.xpath(f"//field[@name='{name}'][@readonly='1']"),
                name,
            )

    def test_cargo_type_dropdown_writes_legacy_text_without_changing_sales(self):
        before = self.line_1._prepare_invoice_line()
        first = self.env.ref("taj_transport_operations.cargo_type_general")
        second = self.env.ref("taj_transport_operations.cargo_type_refrigerated")
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
            "cargo_type_id": first.id,
        })
        self.assertEqual(trip.cargo_type_id, first)
        self.assertEqual(trip.cargo_type, first.name)

        trip.write({"cargo_type_id": second.id})
        self.assertEqual(trip.cargo_type_id, second)
        self.assertEqual(trip.cargo_type, second.name)
        self.assertEqual(self.line_1._prepare_invoice_line(), before)

        trip.write({"cargo_type_id": False})
        self.assertFalse(trip.cargo_type_id)
        self.assertFalse(trip.cargo_type)

    def test_cargo_type_dropdown_disallows_invalid_or_archived_types(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
        })
        with self.assertRaises(ValidationError):
            trip.write({"cargo_type_id": 999999999})
        category = self.env.ref("taj_transport_operations.cargo_type_general")
        category.write({"active": False})
        with self.assertRaises(ValidationError):
            trip.write({"cargo_type_id": category.id})

    def test_cargo_type_legacy_unknown_is_preserved_without_fake_mapping(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
            "cargo_type": "Unique historical commodity type",
        })
        self.assertEqual(trip.cargo_type, "Unique historical commodity type")
        self.assertFalse(trip.cargo_type_id)

    def test_cargo_type_used_record_cannot_be_renamed(self):
        category = self.env.ref("taj_transport_operations.cargo_type_general")
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_1.id,
            "cargo_type_id": category.id,
        })
        self.assertEqual(trip.cargo_type_id, category)
        with self.assertRaises(UserError):
            category.write({"name": "Renamed type"})
        self.assertEqual(trip.cargo_type, "General cargo")

    def test_cargo_type_catalog_dispatcher_read_manager_edit(self):
        from odoo.tests.common import new_test_user

        dispatcher = new_test_user(
            self.env, login="cargo_type_test_dispatcher",
            groups="taj_transport_operations.group_transport_user",
        )
        manager = new_test_user(
            self.env, login="cargo_type_test_manager",
            groups="taj_transport_operations.group_transport_manager",
        )
        catalog = self.env["taj.transport.cargo.type"]
        self.assertEqual(
            self.env.ref(
                "taj_transport_operations.cargo_type_general"
            ).with_user(dispatcher).name,
            "General cargo",
        )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            catalog.with_user(dispatcher).create({
                "name": "Unauthorized dispatcher-created type",
            })
        created = catalog.with_user(manager).create({
            "name": "Manager-approved cargo type",
        })
        self.assertEqual(created.name, "Manager-approved cargo type")

    def test_cargo_type_ui_uses_catalog_many2one_not_free_text(self):
        from lxml import etree
        field = self.env["taj.transport.trip"]._fields["cargo_type_id"]
        self.assertEqual(field.type, "many2one")
        self.assertEqual(field.comodel_name, "taj.transport.cargo.type")
        view = self.env.ref("taj_transport_operations.view_transport_trip_form")
        root = etree.fromstring(view.get_combined_arch())
        dropdown = root.xpath(
            "//page[@name='cargo_details']//field[@name='cargo_type_id']"
        )
        self.assertEqual(len(dropdown), 1)
        self.assertIn("no_create", dropdown[0].get("options", ""))
        self.assertFalse(
            root.xpath("//page[@name='cargo_details']//field[@name='cargo_type']")
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
