"""Audited truck-only defaults, driver completion and occupancy guards."""
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .phase3_case import TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestAutomaticFleetDefaults(TajTransportPhase3Case):
    user_tag = "automatic_fleet_defaults"

    def _source(self, tag="DEFAULT", **values):
        return self._sale_line(tag, vehicle_id=self.truck.id, **values)

    def _foreign_trip(self, tag):
        foreign = self.env["res.company"].create({"name": "Shared truck " + tag})
        order = self.env["sale.order"].with_company(foreign).create({
            "partner_id": self.customer.id,
        })
        line = self.env["sale.order.line"].with_company(foreign).create({
            "order_id": order.id, "product_id": self.product.id,
            "product_uom_qty": 1, "price_unit": 900, "vehicle_id": self.truck.id,
        })
        order.action_confirm()
        dispatcher = new_test_user(
            self.env, login="foreign_auto_" + tag, company_id=foreign.id,
            company_ids=[(6, 0, foreign.ids)],
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        return self.env["taj.transport.trip"].with_user(dispatcher).with_context(
            allowed_company_ids=foreign.ids,
        ).create({"sale_line_id": line.id})

    def test_driverless_truck_creates_one_audited_assignment(self):
        line = self._source()
        before = line._prepare_invoice_line()
        self.assertTrue(self.truck.transport_can_assign)
        trip = self._trip(line=line)
        assignment = trip.current_assignment_id
        self.assertEqual(len(trip.assignment_ids), 1)
        self.assertEqual(assignment.vehicle_id, self.truck)
        self.assertFalse(assignment.driver_id)
        self.assertEqual(assignment.assigned_by_id, self.env.user)
        self.assertTrue(assignment.effective_from)
        self.assertFalse(assignment.effective_to)
        self.assertEqual(trip.state, "draft")
        self.assertFalse(self.truck.transport_can_assign)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(line._prepare_invoice_line(), before)

    def test_same_company_fleet_driver_is_copied(self):
        self.driver.company_id = self.company
        self.truck.driver_id = self.driver
        trip = self._trip(line=self._source())
        self.assertEqual(trip.current_driver_id, self.driver)

    def test_shared_fleet_driver_is_copied(self):
        self.assertFalse(self.driver.company_id)
        self.truck.driver_id = self.driver
        trip = self._trip(line=self._source())
        self.assertEqual(trip.current_driver_id, self.driver)

    def test_inactive_driver_is_omitted_without_changing_fleet(self):
        self.truck.driver_id = self.driver
        self.driver.active = False
        trip = self._trip(line=self._source())
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertFalse(trip.current_driver_id)
        self.assertEqual(self.truck.driver_id, self.driver)

    def test_wrong_company_driver_is_omitted(self):
        other = self.env["res.company"].create({"name": "Foreign driver company"})
        self.driver.company_id = other
        self.truck.driver_id = self.driver
        trip = self._trip(line=self._source())
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertFalse(trip.current_driver_id)
        self.assertEqual(self.truck.driver_id, self.driver)

    def test_invalid_trucks_skip_automatic_and_reject_explicit_defaults(self):
        foreign = self.env["res.company"].create({"name": "Foreign truck company"})
        for label, values in (
            ("archived", {"active": False}),
            ("uncategorized", {"category_id": False}),
            ("trailer", {"category_id": self.env.ref("taj_transport_operations.fleet_category_trailer").id}),
            ("foreign", {"company_id": foreign.id}),
        ):
            with self.subTest(label=label):
                truck = self.env["fleet.vehicle"].create({
                    "model_id": self.truck_model.id, "license_plate": "INVALID-" + label,
                    **values,
                })
                line = self._sale_line(label, vehicle_id=truck.id)
                trip = self._trip(line=line)
                self.assertFalse(trip.assignment_ids)
                self.assertEqual(trip.initial_vehicle_id, truck)
                with self.assertRaisesRegex(UserError, "active, categorized"), self.env.cr.savepoint():
                    trip.action_apply_initial_fleet_defaults()

    def test_missing_truck_stays_unassigned(self):
        trip = self._trip(line=self._sale_line("MISSING", vehicle_id=False))
        self.assertFalse(trip.assignment_ids)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            trip.action_apply_initial_fleet_defaults()

    def test_driver_added_by_replacement_allows_ready_and_preserves_history(self):
        line = self._source()
        before = line._prepare_invoice_line()
        with freeze_time(fields.Datetime.now() - timedelta(hours=1)):
            trip = self._trip(line=line)
        original = trip.current_assignment_id
        self._plan(trip)
        with self.assertRaisesRegex(ValidationError, "Assign a driver"), self.env.cr.savepoint():
            trip.action_mark_ready()
        action = trip.action_open_replace_assignment_wizard()
        wizard = self.env["taj.transport.replace.assignment.wizard"].with_context(
            action["context"]
        ).create({"trip_id": trip.id, "driver_id": self.driver.id, "reason": "Driver assigned"})
        self.assertEqual(wizard.vehicle_id, self.truck)
        wizard.action_replace_resources()
        self.assertEqual(len(trip.assignment_ids), 2)
        self.assertTrue(original.effective_to)
        self.assertFalse(original.driver_id)
        self.assertEqual(original.vehicle_id, self.truck)
        self.assertEqual(trip.current_driver_id, self.driver)
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(line._prepare_invoice_line(), before)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            original.write({"driver_id": self.driver.id})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            trip.action_open_initial_assignment_wizard()
        trip.action_mark_ready()
        self.assertEqual(trip.state, "ready")

    def test_repeated_defaults_do_not_duplicate_or_follow_fleet_driver_edits(self):
        trip = self._trip(line=self._source())
        original = trip.current_assignment_id
        self.truck.driver_id = self.driver
        trip._assign_initial_fleet_defaults()
        self.assertEqual(trip.assignment_ids, original)
        self.assertFalse(original.driver_id)
        with self.assertRaisesRegex(UserError, "without assignment history"), self.env.cr.savepoint():
            trip.action_apply_initial_fleet_defaults()
        self.assertEqual(trip.assignment_ids, original)

    def test_occupied_truck_skips_automatic_assignment_and_explains_manual_action(self):
        first = self._trip(line=self._source("FIRST"))
        other = self._trip(line=self._source("OTHER"))
        self.assertFalse(other.assignment_ids)
        self.assertEqual(first.current_vehicle_id, self.truck)
        self.assertIn("already assigned", " ".join(other.message_ids.mapped("body")))
        with self.assertRaisesRegex(UserError, "Use Assign Initial Resources to choose a different available truck"), self.env.cr.savepoint():
            other.action_apply_initial_fleet_defaults()
        self.assertEqual(len(first.assignment_ids), 1)

    def test_in_progress_truck_is_not_reassigned(self):
        self.truck.driver_id = self.driver
        first = self._trip(line=self._source("BUSY"))
        self._plan(first)
        first.action_mark_ready()
        first.action_start_trip()
        other = self._trip(line=self._source("WAITING"))
        self.assertEqual(self.truck.transport_availability_state, "busy")
        self.assertFalse(other.assignment_ids)
        self.assertEqual(first.current_vehicle_id, self.truck)
        self.assertEqual(len(first.assignment_ids), 1)

    def test_own_reservation_does_not_block_apply_defaults(self):
        category = self.truck.category_id
        self.truck.category_id = False
        trip = self._trip(line=self._source())
        self.assertFalse(trip.assignment_ids)
        self.truck.category_id = category
        trip.action_apply_initial_fleet_defaults()
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertEqual(len(trip.assignment_ids), 1)

    def test_other_draft_reservation_blocks_defaults(self):
        category = self.truck.category_id
        self.truck.category_id = False
        first = self._trip(line=self._source("RESERVED"))
        self.truck.category_id = category
        second = self._trip(line=self._source("BLOCKED"))
        self.assertFalse(first.assignment_ids)
        self.assertFalse(second.assignment_ids)
        with self.assertRaisesRegex(UserError, "reserved by another Draft trip"), self.env.cr.savepoint():
            second.action_apply_initial_fleet_defaults()

    def test_batch_using_one_truck_never_double_assigns(self):
        lines = self._source("BATCH1") | self._source("BATCH2")
        trips = self.env["taj.transport.trip"].create([
            {"sale_line_id": line.id} for line in lines
        ])
        self.assertFalse(trips.assignment_ids)
        self.assertEqual(trips.initial_vehicle_id, self.truck)
        self.assertTrue(all(trip.state == "draft" for trip in trips))

    def test_shared_truck_occupied_in_another_company_is_not_reassigned(self):
        self.truck.company_id = False
        first = self._trip(line=self._source("LOCAL"))
        second = self._foreign_trip("occupied")
        self.assertFalse(second.assignment_ids)
        self.assertEqual(len(first.assignment_ids), 1)
        self.assertFalse(second.search([("id", "=", first.id)]))
        self.assertNotIn(first.name, " ".join(second.message_ids.mapped("body")))

    def test_shared_truck_reservation_in_another_company_is_respected(self):
        self.truck.company_id = False
        category = self.truck.category_id
        self.truck.category_id = False
        first = self._trip(line=self._source("HIDDENRESERVATION"))
        self.truck.category_id = category
        second = self._foreign_trip("reserved")
        self.assertFalse(first.assignment_ids)
        self.assertFalse(second.assignment_ids)
        self.assertFalse(second.search([("id", "=", first.id)]))
        self.assertNotIn(first.name, " ".join(second.message_ids.mapped("body")))
        with self.assertRaisesRegex(UserError, "reserved by another Draft trip"), self.env.cr.savepoint():
            second.action_apply_initial_fleet_defaults()
