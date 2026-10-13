"""Phase 2 / Batch 5 lifecycle, audit and resource interoperability tests."""
from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestTripLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({
            "name": "Lifecycle test customer",
        })
        cls.driver = cls.env["res.partner"].create({
            "name": "Lifecycle driver",
        })
        cls.brand = cls.env["fleet.vehicle.model.brand"].create({
            "name": "Lifecycle test brand",
        })
        cls.truck_model = cls.env["fleet.vehicle.model"].create({
            "name": "Lifecycle truck model",
            "brand_id": cls.brand.id,
            "category_id": cls.env.ref(
                "taj_transport_operations.fleet_category_truck"
            ).id,
        })
        cls.trailer_model = cls.env["fleet.vehicle.model"].create({
            "name": "Lifecycle trailer model",
            "brand_id": cls.brand.id,
            "category_id": cls.env.ref(
                "taj_transport_operations.fleet_category_trailer"
            ).id,
        })
        cls.truck = cls.env["fleet.vehicle"].create({
            "model_id": cls.truck_model.id,
            "license_plate": "LIFECYCLE-TRUCK-01",
        })
        cls.truck_two = cls.env["fleet.vehicle"].create({
            "model_id": cls.truck_model.id,
            "license_plate": "LIFECYCLE-TRUCK-02",
        })
        cls.trailer = cls.env["fleet.vehicle"].create({
            "model_id": cls.trailer_model.id,
            "license_plate": "LIFECYCLE-TRAILER",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Lifecycle transport service", "type": "service",
            "list_price": 700,
        })
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.customer.id,
        })
        cls.line = cls.env["sale.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.product.id,
            "name": "Lifecycle freight",
            "product_uom_qty": 1,
            "price_unit": 700,
            "vehicle_id": cls.truck.id,
        })
        cls.order.action_confirm()

    def _trip(self, route=True, truck=True, driver=True, assignment=True):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        if route:
            now = fields.Datetime.now()
            trip.write({
                "origin_location": "Dar es Salaam",
                "destination_location": "Lusaka",
                "planned_departure_at": now + timedelta(hours=2),
                "planned_arrival_at": now + timedelta(days=1),
            })
        if assignment:
            self.env["taj.transport.assignment"].create({
                "trip_id": trip.id,
                "vehicle_id": self.truck.id if truck else False,
                "driver_id": self.driver.id if driver else False,
                "effective_from": fields.Datetime.now() - timedelta(hours=4),
            })
        return trip

    def _started_trip(self):
        trip = self._trip()
        trip.action_mark_ready()
        trip.write({
            "actual_departure_at": fields.Datetime.now() - timedelta(hours=2),
        })
        trip.action_start_trip()
        return trip

    def _cancel_wizard(self, trip, reason="Customer changed destination", user=None):
        model = self.env["taj.transport.cancel.trip.wizard"]
        if user:
            model = model.with_user(user)
        return model.create({"trip_id": trip.id, "reason": reason})

    def test_happy_path_draft_ready_in_progress_completed(self):
        trip = self._trip()
        original = trip.current_assignment_id
        invoice_before = self.line._prepare_invoice_line()
        self.assertEqual(trip.state, "draft")
        self.assertFalse(trip.ready_at)
        self.assertFalse(trip.started_at)
        self.assertFalse(trip.completed_at)

        self.assertTrue(trip.action_mark_ready())
        self.assertEqual(trip.state, "ready")
        self.assertTrue(trip.ready_at)
        self.assertFalse(trip.actual_departure_at)

        trip.write({
            "actual_departure_at": fields.Datetime.now() - timedelta(hours=2),
        })
        self.assertTrue(trip.action_start_trip())
        self.assertEqual(trip.state, "in_progress")
        self.assertTrue(trip.started_at)
        departure = trip.actual_departure_at
        self.assertTrue(departure)

        self.assertTrue(trip.action_complete_trip())
        self.assertEqual(trip.state, "completed")
        self.assertTrue(trip.completed_at)
        self.assertTrue(trip.actual_arrival_at)
        self.assertGreater(trip.actual_arrival_at, departure)
        original.invalidate_recordset()
        trip.invalidate_recordset()
        self.assertEqual(original.effective_to, trip.actual_arrival_at)
        self.assertFalse(trip.current_assignment_id)
        self.assertFalse(trip.current_vehicle_id)
        self.assertEqual(trip.last_assignment_id, original)
        self.assertEqual(trip.last_vehicle_id, self.truck)
        self.assertEqual(trip.last_driver_id, self.driver)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(self.line.vehicle_id, self.truck)
        self.assertEqual(self.line._prepare_invoice_line(), invoice_before)

    def test_start_stamps_actual_departure_if_missing(self):
        trip = self._trip()
        trip.action_mark_ready()
        self.assertFalse(trip.actual_departure_at)
        trip.action_start_trip()
        self.assertTrue(trip.actual_departure_at)
        self.assertEqual(trip.state, "in_progress")

    def test_cannot_mark_ready_without_route(self):
        trip = self._trip(route=False)
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()
        self.assertEqual(trip.state, "draft")
        self.assertFalse(trip.ready_at)

    def test_cannot_mark_ready_without_planned_arrival(self):
        trip = self._trip()
        trip.write({"planned_arrival_at": False})
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()
        self.assertEqual(trip.state, "draft")

    def test_cannot_mark_ready_with_equal_planned_times(self):
        trip = self._trip()
        trip.write({"planned_arrival_at": trip.planned_departure_at})
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()

    def test_cannot_mark_ready_without_active_assignment(self):
        trip = self._trip(assignment=False)
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()
        self.assertEqual(trip.state, "draft")

    def test_cannot_mark_ready_without_truck(self):
        trip = self._trip(truck=False)
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()

    def test_cannot_mark_ready_without_driver(self):
        trip = self._trip(driver=False)
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()

    def test_trailer_and_codriver_are_optional_for_ready(self):
        trip = self._trip()
        self.assertFalse(trip.current_trailer_id)
        self.assertFalse(trip.current_co_driver_id)
        trip.action_mark_ready()
        self.assertEqual(trip.state, "ready")

    def test_archived_fleet_truck_blocks_ready(self):
        trip = self._trip()
        self.truck.active = False
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()
        self.assertEqual(trip.state, "draft")

    def test_inactive_primary_driver_blocks_ready(self):
        trip = self._trip()
        self.driver.active = False
        with self.assertRaises(ValidationError):
            trip.action_mark_ready()
        self.assertEqual(trip.state, "draft")

    def test_start_requires_ready_and_valid_actual_departure(self):
        trip = self._trip()
        with self.assertRaises(UserError):
            trip.action_start_trip()
        trip.action_mark_ready()
        trip.write({
            "actual_departure_at": fields.Datetime.now() + timedelta(hours=2),
        })
        with self.assertRaises(ValidationError):
            trip.action_start_trip()
        self.assertEqual(trip.state, "ready")
        self.assertFalse(trip.started_at)

    def test_completion_requires_in_progress_and_valid_arrival(self):
        trip = self._trip()
        with self.assertRaises(UserError):
            trip.action_complete_trip()
        trip.action_mark_ready()
        trip.write({
            "actual_departure_at": fields.Datetime.now() - timedelta(hours=1),
        })
        trip.action_start_trip()
        original = trip.current_assignment_id
        trip.write({
            "actual_arrival_at": fields.Datetime.now() + timedelta(hours=1),
        })
        with self.assertRaises(ValidationError):
            trip.action_complete_trip()
        original.invalidate_recordset()
        self.assertFalse(original.effective_to)
        self.assertEqual(trip.state, "in_progress")
        trip.write({
            "actual_arrival_at": trip.actual_departure_at,
        })
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                trip.action_complete_trip()
        self.assertEqual(trip.state, "in_progress")

    def test_cannot_directly_write_state_or_audit_fields(self):
        trip = self._trip()
        for vals in (
            {"state": "ready"},
            {"state": "draft"},
            {"ready_at": fields.Datetime.now()},
            {"cancelled_by_id": self.env.user.id},
            {"cancellation_reason": "Forged"},
        ):
            with self.assertRaises(UserError):
                trip.sudo().write(vals)
        with self.assertRaises(ValidationError):
            self.env["taj.transport.trip"].create({
                "sale_line_id": self.line.id,
                "state": "ready",
            })
        self.assertEqual(trip.state, "draft")

    def test_transitions_are_ordered_not_repeatable(self):
        trip = self._trip()
        trip.action_mark_ready()
        with self.assertRaises(UserError):
            trip.action_mark_ready()
        with self.assertRaises(UserError):
            trip.action_complete_trip()
        trip.action_start_trip()
        with self.assertRaises(UserError):
            trip.action_start_trip()

    def test_draft_cancellation_reason_and_terminal_state(self):
        trip = self._trip(assignment=False, route=False)
        wizard_action = trip.action_open_cancel_trip_wizard()
        self.assertEqual(wizard_action["res_model"], "taj.transport.cancel.trip.wizard")
        self._cancel_wizard(trip, reason="Customer cancelled booking").action_confirm_cancellation()
        self.assertEqual(trip.state, "cancelled")
        self.assertTrue(trip.cancelled_at)
        self.assertEqual(trip.cancelled_by_id, self.env.user)
        self.assertEqual(trip.cancellation_reason, "Customer cancelled booking")
        self.assertIn(
            "Customer cancelled booking", " ".join(trip.message_ids.mapped("body")),
        )
        with self.assertRaises(UserError):
            trip.action_mark_ready()
        with self.assertRaises(UserError):
            trip.action_open_cancel_trip_wizard()
        with self.assertRaises(UserError):
            trip.write({"origin_location": "Tampered"})

    def test_cancel_ready_releases_assignment_and_keeps_last_resource(self):
        trip = self._trip()
        trip.action_mark_ready()
        assignment = trip.current_assignment_id
        self._cancel_wizard(trip).action_confirm_cancellation()
        assignment.invalidate_recordset()
        trip.invalidate_recordset()
        self.assertEqual(trip.state, "cancelled")
        self.assertTrue(assignment.effective_to)
        self.assertFalse(trip.current_assignment_id)
        self.assertEqual(trip.last_vehicle_id, self.truck)
        self.assertEqual(trip.initial_vehicle_id, self.truck)

    def test_cancel_future_dated_initial_assignment_does_not_reverse_period(self):
        trip = self._trip(assignment=False, route=False)
        effective_from = fields.Datetime.now() + timedelta(days=1)
        assignment = self.env["taj.transport.assignment"].create({
            "trip_id": trip.id,
            "vehicle_id": self.truck.id,
            "effective_from": effective_from,
        })
        self._cancel_wizard(trip).action_confirm_cancellation()
        assignment.invalidate_recordset()
        self.assertEqual(assignment.effective_to, effective_from)
        self.assertEqual(trip.state, "cancelled")

    def test_cancellation_reason_required_and_no_partial_close(self):
        trip = self._trip()
        assignment = trip.current_assignment_id
        wizard = self._cancel_wizard(trip, reason="   ")
        with self.assertRaises(ValidationError):
            wizard.action_confirm_cancellation()
        self.assertEqual(trip.state, "draft")
        assignment.invalidate_recordset()
        self.assertFalse(assignment.effective_to)

    def test_non_transport_user_cannot_trigger_lifecycle_actions(self):
        trip = self._trip()
        outsider = new_test_user(
            self.env, login="trip_lifecycle_unprivileged",
            groups="base.group_user",
        )
        outsider.company_ids = self.env.company
        outsider.company_id = self.env.company
        with self.assertRaises(AccessError):
            trip.with_user(outsider).action_mark_ready()
        with self.assertRaises(AccessError):
            trip.with_user(outsider).action_open_cancel_trip_wizard()
        self.assertEqual(trip.state, "draft")

    def test_in_progress_cancellation_only_manager(self):
        trip = self._started_trip()
        original = trip.current_assignment_id
        dispatcher = new_test_user(
            self.env, login="trip_lifecycle_dispatcher",
            groups="taj_transport_operations.group_transport_user",
        )
        dispatcher.company_ids = self.env.company
        dispatcher.company_id = self.env.company
        with self.assertRaises(AccessError):
            trip.with_user(dispatcher).action_open_cancel_trip_wizard()
        with self.assertRaises(AccessError):
            self._cancel_wizard(
                trip, reason="Unapproved in-progress cancellation",
                user=dispatcher,
            ).action_confirm_cancellation()
        self.assertEqual(trip.state, "in_progress")
        self.assertFalse(original.effective_to)

        manager = new_test_user(
            self.env, login="trip_lifecycle_manager",
            groups="taj_transport_operations.group_transport_manager",
        )
        manager.company_ids = self.env.company
        manager.company_id = self.env.company
        self._cancel_wizard(
            trip, reason="Road closed during transport", user=manager,
        ).action_confirm_cancellation()
        self.assertEqual(trip.state, "cancelled")
        self.assertEqual(trip.cancelled_by_id, manager)
        self.assertTrue(original.effective_to)

    def test_completed_and_cancelled_trips_cannot_edit_or_reopen(self):
        trip = self._started_trip()
        trip.action_complete_trip()
        with self.assertRaises(UserError):
            trip.action_open_replace_assignment_wizard()
        with self.assertRaises(UserError):
            trip._cancel_with_reason("Attempt after completion")
        with self.assertRaises(UserError):
            trip.action_mark_ready()
        with self.assertRaises(UserError):
            trip.write({"transport_reference": "Tamper attempt"})
        with self.assertRaises(UserError):
            trip.write({"actual_arrival_at": fields.Datetime.now()})
        self.assertEqual(trip.state, "completed")

    def test_replace_resources_while_ready_and_in_progress(self):
        trip = self._trip()
        trip.action_mark_ready()
        original = trip.current_assignment_id
        action = trip.action_open_replace_assignment_wizard()
        first = self.env[
            "taj.transport.replace.assignment.wizard"
        ].with_context(**action["context"]).create({
            "trip_id": trip.id,
            "expected_assignment_id": original.id,
            "vehicle_id": self.truck_two.id,
            "effective_from": fields.Datetime.now() - timedelta(hours=3),
            "reason": "Change before departure",
        })
        first.action_replace_resources()
        self.assertEqual(trip.current_vehicle_id, self.truck_two)

        trip.write({
            "actual_departure_at": fields.Datetime.now() - timedelta(hours=2),
        })
        trip.action_start_trip()
        action = trip.action_open_replace_assignment_wizard()
        second = self.env[
            "taj.transport.replace.assignment.wizard"
        ].with_context(**action["context"]).create({
            "trip_id": trip.id,
            "expected_assignment_id": trip.current_assignment_id.id,
            "driver_id": self.customer.id,
            "effective_from": fields.Datetime.now() - timedelta(hours=1),
            "reason": "Driver handover",
        })
        second.action_replace_resources()
        self.assertEqual(trip.current_driver_id, self.customer)
        self.assertEqual(len(trip.assignment_ids), 3)
        trip.action_complete_trip()
        self.assertEqual(trip.last_vehicle_id, self.truck_two)
        self.assertEqual(trip.last_driver_id, self.customer)
        self.assertFalse(trip.current_assignment_id)

    def test_ready_replacement_cannot_remove_mandatory_driver(self):
        trip = self._trip()
        trip.action_mark_ready()
        current = trip.current_assignment_id
        action = trip.action_open_replace_assignment_wizard()
        wizard = self.env[
            "taj.transport.replace.assignment.wizard"
        ].with_context(**action["context"]).create({
            "trip_id": trip.id,
            "expected_assignment_id": current.id,
            "driver_id": False,
            "effective_from": fields.Datetime.now() - timedelta(hours=1),
            "reason": "Driver availability",
        })
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                wizard.action_replace_resources()
        current.invalidate_recordset()
        self.assertFalse(current.effective_to)
        self.assertEqual(trip.current_driver_id, self.driver)

    def test_in_progress_replacement_cannot_precede_actual_departure(self):
        trip = self._started_trip()
        current = trip.current_assignment_id
        action = trip.action_open_replace_assignment_wizard()
        wizard = self.env[
            "taj.transport.replace.assignment.wizard"
        ].with_context(**action["context"]).create({
            "trip_id": trip.id,
            "expected_assignment_id": current.id,
            "vehicle_id": self.truck_two.id,
            "effective_from": trip.actual_departure_at - timedelta(minutes=20),
            "reason": "Invalid retrospective handover",
        })
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                wizard.action_replace_resources()
        current.invalidate_recordset()
        self.assertFalse(current.effective_to)
        self.assertEqual(trip.current_vehicle_id, self.truck)

    def test_completed_or_cancelled_has_no_new_initial_assignment(self):
        trip = self._trip(assignment=False, route=False)
        self._cancel_wizard(trip).action_confirm_cancellation()
        with self.assertRaises(UserError):
            self.env["taj.transport.assignment"].create({
                "trip_id": trip.id, "vehicle_id": self.truck.id,
            })
        with self.assertRaises(UserError):
            trip.action_open_initial_assignment_wizard()

    def test_lifecycle_views_and_security(self):
        form = self.env.ref(
            "taj_transport_operations.view_transport_trip_form"
        )
        root = etree.fromstring(form.get_combined_arch())
        statusbar = root.xpath("//header/field[@name='state']")
        self.assertEqual(len(statusbar), 1)
        self.assertEqual(statusbar[0].get("readonly"), "1")
        self.assertIn("cancelled", statusbar[0].get("statusbar_visible"))
        for name in (
            "action_mark_ready", "action_start_trip",
            "action_complete_trip", "action_open_cancel_trip_wizard",
        ):
            self.assertTrue(
                root.xpath(f"//header/button[@name='{name}']"), name,
            )
        manager_cancel = root.xpath(
            "//header/button[@name='action_open_cancel_trip_wizard']"
            "[@groups='taj_transport_operations.group_transport_manager']"
        )
        self.assertEqual(len(manager_cancel), 1)
        self.assertEqual(
            len(root.xpath("//page[@name='trip_lifecycle']")), 1,
        )
        for name in ("ready_at", "started_at", "completed_at", "cancelled_at"):
            self.assertEqual(
                len(root.xpath(
                    f"//page[@name='trip_lifecycle']//field[@name='{name}']"
                )), 1,
            )
        wizard = self.env.ref(
            "taj_transport_operations.view_cancel_trip_wizard_form"
        )
        parsed = etree.fromstring(wizard.get_combined_arch())
        self.assertEqual(
            len(parsed.xpath("//button[@name='action_confirm_cancellation']")),
            1,
        )
        permission = self.env.ref(
            "taj_transport_operations.access_taj_transport_cancel_wizard_user"
        )
        self.assertTrue(permission.perm_create)
        self.assertFalse(permission.perm_unlink)
