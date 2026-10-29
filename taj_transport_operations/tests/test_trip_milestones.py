"""Phase 3 acceptance tests: manual checkpoint workflow, audit and security.

Covers the operational half of Phase 3: recording arrival and departure
per checkpoint, the checkpoint status machine, the
manager-only audited corrections, the immutable event trail, dispatcher
permissions and multi-company isolation, and the interaction with the Phase 2
trip lifecycle (open checkpoints never block Ready / Start / Complete).

Section E holds the independent review regressions: forged contexts and forged
values cannot bypass the guards, the actor of an event always comes from the
environment, and checkpoint notes are closed once the trip is terminal.
"""

from datetime import timedelta

from freezegun import freeze_time
from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .phase3_case import DISPATCHER_GROUP, MANAGER_GROUP, TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestTripMilestones(TajTransportPhase3Case):
    """Manual checkpoint recording, corrections and permissions."""

    user_tag = "phase3_milestone"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _checkpoint_trip(self, tag, state="ready"):
        """A trip with the default three ordered checkpoints."""
        route = self._route("%s route" % tag)
        if state == "draft":
            return self._planned_trip(route, tag=tag)
        if state == "in_progress":
            return self._started_trip(route, tag=tag)
        return self._ready_trip(route, tag=tag)

    def _manager_milestone(self, milestone):
        return (
            self.env["taj.transport.milestone"]
            .with_user(self.manager)
            .browse(milestone.id)
        )

    # ------------------------------------------------------------------
    # Manual workflow
    # ------------------------------------------------------------------
    def test_checkpoints_cannot_be_recorded_before_the_trip_is_ready(self):
        trip = self._checkpoint_trip("DRAFTONLY", state="draft")
        milestone = self._milestone(trip, "Loading")
        self.assertEqual(trip.state, "draft")
        with self.assertRaises(UserError):
            self._record(milestone, "arrival")
        self.assertFalse(milestone.actual_arrival_at)
        self.assertEqual(milestone.state, "pending")

    def test_arrival_departure_completes_checkpoints(self):
        trip = self._checkpoint_trip("FLOW")
        loading = self._milestone(trip, "Loading")
        border = self._milestone(trip, "Kasumbalesa border")
        offloading = self._milestone(trip, "Offloading")
        now = fields.Datetime.now()
        action = self._record(loading, "arrival", event_at=now - timedelta(hours=3))
        self.assertEqual(action, {"type": "ir.actions.act_window_close"})
        self.assertEqual(loading.state, "arrived")
        self.assertEqual(loading.arrived_by_id, self.env.user)
        action = self._record(loading, "departure", event_at=now - timedelta(hours=2, minutes=30))
        self.assertEqual(action, {"type": "ir.actions.act_window_close"})
        self.assertEqual(loading.state, "departed")
        self.assertFalse(loading.confirmed_at)
        self._record(border, "arrival", event_at=now - timedelta(hours=2))
        self._record(border, "departure", event_at=now - timedelta(hours=1))
        self.assertEqual(border.state, "departed")
        self.assertEqual(offloading.state, "pending")
        self.assertEqual(trip.milestone_done_count, 2)
        self.assertEqual(trip.milestone_open_count, 1)
        self.assertAlmostEqual(trip.milestone_progress, 66.67, places=1)

    def test_departure_requires_a_recorded_arrival(self):
        trip = self._checkpoint_trip("NEEDARRIVAL")
        border = self._milestone(trip, "Kasumbalesa border")
        with self.assertRaises(UserError):
            self._record(border, "departure", event_at=fields.Datetime.now())
        self.assertFalse(border.actual_departure_at)
        self.assertEqual(border.state, "pending")

    def test_retired_confirmation_actions_are_refused(self):
        trip = self._checkpoint_trip("RETIRED")
        loading = self._milestone(trip, "Loading")
        self._record(loading, "arrival")
        with self.assertRaises(UserError):
            loading._confirm_checkpoint("Stale client")
        with self.assertRaises(UserError):
            loading.action_open_confirmation_wizard()
        self.assertFalse(loading.confirmed_at)
        self.assertEqual(loading.state, "arrived")

    def test_double_recording_is_refused(self):
        trip = self._checkpoint_trip("DOUBLE")
        border = self._milestone(trip, "Kasumbalesa border")
        moment = fields.Datetime.now() - timedelta(hours=2)
        self._record(border, "arrival", event_at=moment)
        with self.assertRaises(UserError):
            self._record(border, "arrival", event_at=moment + timedelta(minutes=5))
        self.assertAlmostEqual(
            border.actual_arrival_at,
            moment,
            delta=timedelta(seconds=1),
        )
        self._record(border, "departure", event_at=moment + timedelta(minutes=5))
        with self.assertRaises(UserError):
            self._record(border, "departure", event_at=moment + timedelta(minutes=10))

    def test_recorded_timestamps_cannot_be_in_the_future(self):
        trip = self._checkpoint_trip("FUTURE")
        border = self._milestone(trip, "Kasumbalesa border")
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._record(
                border,
                "arrival",
                event_at=fields.Datetime.now() + timedelta(days=1),
            )
        self.assertFalse(border.actual_arrival_at)

    def test_departure_cannot_precede_arrival(self):
        trip = self._checkpoint_trip("REVERSED")
        border = self._milestone(trip, "Kasumbalesa border")
        moment = fields.Datetime.now() - timedelta(hours=2)
        self._record(border, "arrival", event_at=moment)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._record(
                border,
                "departure",
                event_at=moment - timedelta(minutes=30),
            )
        self.assertFalse(border.actual_departure_at)

    def test_checkpoints_follow_the_route_order(self):
        trip = self._checkpoint_trip("ORDER")
        loading = self._milestone(trip, "Loading")
        border = self._milestone(trip, "Kasumbalesa border")
        now = fields.Datetime.now()
        self._record(border, "arrival", event_at=now - timedelta(hours=1))
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._record(loading, "arrival", event_at=now)
        self.assertFalse(loading.actual_arrival_at)
        # An earlier time for the earlier checkpoint is accepted.
        self._record(loading, "arrival", event_at=now - timedelta(hours=3))
        self.assertTrue(loading.actual_arrival_at)

    def test_checkpoints_cannot_be_recorded_after_the_trip_arrival(self):
        trip = self._checkpoint_trip("AFTERTRIP", state="in_progress")
        border = self._milestone(trip, "Kasumbalesa border")
        trip.write(
            {
                "actual_arrival_at": fields.Datetime.now() - timedelta(minutes=30),
            }
        )
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._record(border, "arrival", event_at=fields.Datetime.now())
        self.assertFalse(border.actual_arrival_at)

    def test_open_checkpoints_never_block_the_trip_lifecycle(self):
        trip = self._checkpoint_trip("BLOCKING")
        self.assertEqual(trip.milestone_open_count, 3)
        # Ready and Start are unaffected by untouched checkpoints.
        self.assertEqual(trip.state, "ready")
        trip.write(
            {
                "actual_departure_at": fields.Datetime.now() - timedelta(hours=1),
            }
        )
        trip.action_start_trip()
        self.assertEqual(trip.state, "in_progress")
        # Completion reports the gap instead of refusing the transition.
        trip.action_complete_trip()
        self.assertEqual(trip.state, "completed")
        self.assertEqual(trip.milestone_open_count, 3)
        bodies = " ".join(trip.message_ids.mapped("body"))
        self.assertIn("still open", bodies)
        self.assertIn("Loading", bodies)
        self.assertIn("Offloading", bodies)

    # ------------------------------------------------------------------
    # Direct write / delete protection
    # ------------------------------------------------------------------
    def test_direct_writes_are_rejected_but_notes_stay_editable(self):
        trip = self._checkpoint_trip("DIRECTWRITE")
        border = self._milestone(trip, "Kasumbalesa border")
        with self.assertRaises(UserError) as error:
            border.write({"state": "completed"})
        self.assertIn("state", str(error.exception))
        with self.assertRaises(UserError):
            border.write({"actual_arrival_at": fields.Datetime.now()})
        with self.assertRaises(UserError):
            border.write({"name": "Renamed by hand"})
        self.assertTrue(border.write({"notes": "Waiting for border clearance"}))
        border.invalidate_recordset()
        self.assertEqual(border.notes, "Waiting for border clearance")
        self.assertEqual(border.state, "pending")
        self.assertEqual(border.name, "Kasumbalesa border")

    def test_milestones_cannot_be_deleted(self):
        trip = self._checkpoint_trip("NODELETE")
        border = self._milestone(trip, "Kasumbalesa border")
        with self.assertRaises(UserError):
            border.unlink()
        self.assertEqual(len(trip.milestone_ids), 3)

    # ------------------------------------------------------------------
    # Skip workflow (manager only)
    # ------------------------------------------------------------------
    def test_skip_is_manager_only_and_excluded_from_progress(self):
        trip = self._checkpoint_trip("SKIP")
        offloading = self._milestone(trip, "Offloading")
        with self.assertRaises(AccessError):
            offloading.with_user(self.dispatcher)._skip_checkpoint(
                "Not applicable",
            )
        self.assertEqual(offloading.state, "pending")
        reason = "Customer collected the goods directly at the border"
        action = self._record(offloading, "skip", user=self.manager, reason=reason)
        self.assertEqual(action, {"type": "ir.actions.act_window_close"})
        offloading.invalidate_recordset()
        self.assertEqual(offloading.state, "skipped")
        self.assertEqual(trip.milestone_count, 3)
        self.assertEqual(trip.milestone_open_count, 2)
        self.assertEqual(trip.milestone_done_count, 0)
        self.assertEqual(trip.milestone_progress, 0.0)
        events = self._events(offloading, "skipped")
        self.assertEqual(len(events), 1)
        self.assertEqual(events.reason, reason)
        self.assertEqual(events.user_id, self.manager)

    def test_skip_requires_a_reason_and_recorded_events_are_protected(self):
        trip = self._checkpoint_trip("SKIPREASON")
        border = self._milestone(trip, "Kasumbalesa border")
        with self.assertRaises(ValidationError):
            self._manager_milestone(border)._skip_checkpoint("   ")
        self.assertEqual(border.state, "pending")
        self._record(
            border,
            "arrival",
            event_at=fields.Datetime.now() - timedelta(hours=1),
        )
        with self.assertRaises(UserError):
            self._manager_milestone(border)._skip_checkpoint("Too late to skip")
        border.invalidate_recordset()
        self.assertEqual(border.state, "arrived")
        self.assertTrue(border.actual_arrival_at)

    # ------------------------------------------------------------------
    # Manager corrections
    # ------------------------------------------------------------------
    def test_manager_correction_audits_before_and_after_values(self):
        trip = self._checkpoint_trip("CORRECTION")
        border = self._milestone(trip, "Kasumbalesa border")
        recorded = fields.Datetime.now() - timedelta(hours=4)
        corrected = recorded + timedelta(minutes=45)
        self._record(border, "arrival", event_at=recorded)
        reason = "Border log showed the truck crossed 45 minutes later"
        self.assertTrue(
            self._manager_milestone(border)._correct_timestamp(
                "actual_arrival_at",
                corrected,
                reason,
            ),
        )
        border.invalidate_recordset()
        self.assertAlmostEqual(
            border.actual_arrival_at,
            corrected,
            delta=timedelta(seconds=1),
        )
        self.assertEqual(border.arrived_by_id, self.manager)
        events = self._events(border, "correction")
        self.assertEqual(len(events), 1)
        self.assertEqual(events.field_name, "actual_arrival_at")
        self.assertEqual(events.reason, reason)
        self.assertEqual(events.user_id, self.manager)
        self.assertEqual(events.old_value, fields.Datetime.to_string(recorded))
        self.assertEqual(events.new_value, fields.Datetime.to_string(corrected))
        bodies = " ".join(trip.message_ids.mapped("body"))
        self.assertIn(reason, bodies)

    def test_correction_refuses_a_missing_reason_or_an_identical_value(self):
        trip = self._checkpoint_trip("CORRECTION2")
        border = self._milestone(trip, "Kasumbalesa border")
        recorded = fields.Datetime.now() - timedelta(hours=2)
        self._record(border, "arrival", event_at=recorded)
        manager_milestone = self._manager_milestone(border)
        with self.assertRaises(ValidationError):
            manager_milestone._correct_timestamp(
                "actual_arrival_at",
                recorded + timedelta(minutes=5),
                "   ",
            )
        with self.assertRaises(UserError):
            manager_milestone._correct_timestamp(
                "actual_arrival_at",
                recorded,
                "No real change",
            )
        with self.assertRaises(ValidationError):
            manager_milestone._correct_timestamp("state", "completed", "Wrong field")
        self.assertFalse(self._events(border, "correction"))
        self.assertTrue(self._events(border, "arrival"))

    def test_correction_revalidates_the_trip_timeline(self):
        trip = self._checkpoint_trip("CORRECTION3")
        loading = self._milestone(trip, "Loading")
        border = self._milestone(trip, "Kasumbalesa border")
        now = fields.Datetime.now()
        self._record(loading, "arrival", event_at=now - timedelta(hours=3))
        self._record(border, "arrival", event_at=now - timedelta(hours=2))
        manager_milestone = self._manager_milestone(border)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            manager_milestone._correct_timestamp(
                "actual_arrival_at",
                now - timedelta(hours=5),
                "Too early",
            )
        self._record(border, "departure", event_at=now - timedelta(hours=1))
        with self.assertRaises(UserError):
            manager_milestone._correct_timestamp(
                "actual_arrival_at",
                False,
                "Cleared by mistake",
            )
        self.assertTrue(
            manager_milestone._correct_timestamp(
                "actual_departure_at",
                False,
                "Departure was never recorded",
            ),
        )
        self.assertTrue(
            manager_milestone._correct_timestamp(
                "actual_arrival_at",
                False,
                "Arrival was never recorded",
            ),
        )
        border.invalidate_recordset()
        self.assertFalse(border.actual_arrival_at)
        self.assertFalse(border.actual_departure_at)
        self.assertFalse(border.confirmed_at)
        self.assertEqual(border.state, "pending")
        self.assertEqual(trip.milestone_open_count, 3)

    def test_dispatcher_cannot_correct_recorded_timestamps(self):
        trip = self._checkpoint_trip("CORRECTION4")
        border = self._milestone(trip, "Kasumbalesa border")
        recorded = fields.Datetime.now() - timedelta(hours=1)
        corrected = recorded + timedelta(minutes=10)
        self._record(border, "arrival", event_at=recorded)
        with self.assertRaises(AccessError):
            border.with_user(self.dispatcher)._correct_timestamp(
                "actual_arrival_at",
                corrected,
                "Dispatcher correction",
            )
        with self.assertRaises(AccessError):
            border.with_user(self.dispatcher).action_open_correction_wizard()
        with self.assertRaises(AccessError):
            self.env["taj.transport.milestone.correction.wizard"].with_user(
                self.dispatcher
            ).create(
                {
                    "milestone_id": border.id,
                    "field_name": "actual_arrival_at",
                    "new_value": corrected,
                    "reason": "Dispatcher correction",
                }
            )
        # The manager uses the same wizard through the audited entry point.
        wizard = (
            self.env["taj.transport.milestone.correction.wizard"]
            .with_user(self.manager)
            .create(
                {
                    "milestone_id": border.id,
                    "field_name": "actual_arrival_at",
                    "new_value": corrected,
                    "reason": "Gate log correction",
                }
            )
        )
        self.assertAlmostEqual(
            wizard.current_value,
            recorded,
            delta=timedelta(seconds=1),
        )
        action = wizard.action_apply_correction()
        self.assertEqual(action, {"type": "ir.actions.act_window_close"})
        border.invalidate_recordset()
        self.assertAlmostEqual(
            border.actual_arrival_at,
            corrected,
            delta=timedelta(seconds=1),
        )

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------
    def test_events_record_the_actor_and_are_immutable(self):
        trip = self._checkpoint_trip("AUDIT")
        border = self._milestone(trip, "Kasumbalesa border")
        moment = fields.Datetime.now() - timedelta(hours=1)
        self._record(
            border,
            "arrival",
            user=self.dispatcher,
            event_at=moment,
            note="Gate entry recorded at the border post",
        )
        border.invalidate_recordset()
        arrivals = self._events(border, "arrival")
        self.assertEqual(len(arrivals), 1)
        self.assertEqual(arrivals.user_id, self.dispatcher)
        self.assertEqual(arrivals.source, "manual")
        self.assertAlmostEqual(
            arrivals.occurred_at,
            moment,
            delta=timedelta(seconds=1),
        )
        self.assertTrue(arrivals.recorded_at)
        self.assertEqual(
            arrivals.note,
            "Gate entry recorded at the border post",
        )
        self.assertEqual(border.actual_arrival_at, arrivals.occurred_at)
        with self.assertRaises(UserError):
            arrivals.write({"note": "Rewritten history"})
        with self.assertRaises(UserError):
            arrivals.unlink()
        self.assertEqual(len(self._events(border, "arrival")), 1)

    # ------------------------------------------------------------------
    # Terminal trip states
    # ------------------------------------------------------------------
    def test_completed_trip_requires_a_manager_correction(self):
        trip = self._checkpoint_trip("COMPLETED", state="in_progress")
        border = self._milestone(trip, "Kasumbalesa border")
        self._record(
            border,
            "arrival",
            event_at=fields.Datetime.now() - timedelta(hours=2),
        )
        trip.action_complete_trip()
        self.assertEqual(trip.state, "completed")
        with self.assertRaises(UserError):
            self._record(
                border,
                "departure",
                event_at=fields.Datetime.now() - timedelta(hours=1),
            )
        with self.assertRaises(UserError):
            trip._add_manual_milestone({"name": "Too late checkpoint"})
        corrected = fields.Datetime.now() - timedelta(hours=3)
        self.assertTrue(
            self._manager_milestone(border)._correct_timestamp(
                "actual_arrival_at",
                corrected,
                "Gate log was corrected after the trip was completed",
            ),
        )
        border.invalidate_recordset()
        self.assertAlmostEqual(
            border.actual_arrival_at,
            corrected,
            delta=timedelta(seconds=1),
        )

    def test_cancelled_trip_refuses_recording_and_correction(self):
        trip = self._checkpoint_trip("CANCELLED")
        border = self._milestone(trip, "Kasumbalesa border")
        self._cancel_trip(trip)
        self.assertEqual(trip.state, "cancelled")
        with self.assertRaises(UserError):
            self._record(border, "arrival")
        with self.assertRaises(UserError):
            trip._add_manual_milestone({"name": "Late checkpoint"})
        with self.assertRaises(UserError):
            self._manager_milestone(border)._correct_timestamp(
                "actual_arrival_at",
                fields.Datetime.now() - timedelta(hours=1),
                "Late correction",
            )
        self.assertEqual(border.state, "pending")

    # ------------------------------------------------------------------
    # Dispatcher permissions
    # ------------------------------------------------------------------
    def test_dispatcher_records_events_of_its_own_trip(self):
        trip = self._checkpoint_trip("DISPATCHER")
        border = self._milestone(trip, "Kasumbalesa border")
        dispatcher_trip = trip.with_user(self.dispatcher)
        self.assertEqual(len(dispatcher_trip.milestone_ids), 3)
        dispatcher_border = dispatcher_trip.milestone_ids.filtered(
            lambda milestone: milestone.name == "Kasumbalesa border",
        )
        self._record(
            dispatcher_border,
            "arrival",
            user=self.dispatcher,
            event_at=fields.Datetime.now() - timedelta(hours=1),
        )
        self.assertTrue(
            self._record(
                dispatcher_border,
                "departure",
                user=self.dispatcher,
                event_at=fields.Datetime.now() - timedelta(minutes=30),
            )
        )
        border.invalidate_recordset()
        self.assertEqual(border.state, "departed")
        self.assertEqual(border.arrived_by_id, self.dispatcher)
        self.assertEqual(border.departed_by_id, self.dispatcher)

    def test_dispatcher_cannot_create_delete_or_skip_checkpoints(self):
        trip = self._checkpoint_trip("DISPATCHERACL")
        border = self._milestone(trip, "Kasumbalesa border")
        dispatcher_border = (
            self.env["taj.transport.milestone"]
            .with_user(self.dispatcher)
            .browse(border.id)
        )
        self.assertTrue(dispatcher_border.write({"notes": "Waiting"}))
        with self.assertRaises(AccessError):
            dispatcher_border.unlink()
        with self.assertRaises(AccessError):
            dispatcher_border._skip_checkpoint("Dispatcher cannot skip")
        with self.assertRaises(AccessError):
            self.env["taj.transport.milestone"].with_user(self.dispatcher).create(
                {
                    "trip_id": trip.id,
                    "name": "Invented checkpoint",
                }
            )
        with self.assertRaises(AccessError):
            self.env["taj.transport.milestone.event"].with_user(self.dispatcher).create(
                {
                    "milestone_id": border.id,
                    "event_type": "arrival",
                }
            )
        self.assertEqual(len(trip.milestone_ids), 3)

    def test_add_checkpoint_wizard_records_an_operational_variance(self):
        trip = self._checkpoint_trip("ADDWIZARD")
        wizard = (
            self.env["taj.transport.milestone.add.wizard"]
            .with_user(self.dispatcher)
            .create(
                {
                    "trip_id": trip.id,
                    "name": "Weighbridge stop",
                    "milestone_type_id": self.type_border.id,
                    "planned_offset_hours": 6.0,
                    "notes": "Mandatory weighbridge before the border",
                }
            )
        )
        wizard.action_add_checkpoint()
        trip.invalidate_recordset()
        added = self._milestone(trip, "Weighbridge stop")
        self.assertEqual(added.state, "pending")
        self.assertEqual(added.sequence, 40)
        self.assertEqual(added.type_name, "Border Crossing")
        self.assertFalse(added.requires_confirmation)
        self.assertEqual(added.company_id, trip.company_id)
        self.assertFalse(added.template_line_id)
        self.assertEqual(trip.milestone_count, 4)

    # ------------------------------------------------------------------
    # Snapshot semantics
    # ------------------------------------------------------------------
    def test_checkpoint_type_snapshot_survives_catalog_renames(self):
        trip = self._checkpoint_trip("TYPENAME")
        border = self._milestone(trip, "Kasumbalesa border")
        self.assertEqual(border.type_name, "Border Crossing")
        self.type_border.write({"name": "Border Crossing (renamed)"})
        border.invalidate_recordset()
        self.assertEqual(border.type_name, "Border Crossing")
        self.assertEqual(
            border.milestone_type_id.name,
            "Border Crossing (renamed)",
        )

    def test_checkpoint_type_catalog_can_be_archived(self):
        trip = self._checkpoint_trip("TYPEARCHIVE")
        border = self._milestone(trip, "Kasumbalesa border")
        self.type_border.write({"active": False})
        border.invalidate_recordset()
        self.assertEqual(border.type_name, "Border Crossing")
        self.assertEqual(border.milestone_type_id, self.type_border)

    # ------------------------------------------------------------------
    # User interface wiring
    # ------------------------------------------------------------------
    def test_trip_form_exposes_the_checkpoint_workspace(self):
        form = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_transport_trip_form"
            ).get_combined_arch()
        )
        tab = form.xpath("//page[@name='trip_milestones']")
        self.assertEqual(len(tab), 1)
        self.assertEqual(len(tab[0].xpath(".//field[@name='milestone_ids']")), 1)
        for button in (
            "action_generate_milestones",
            "action_open_regenerate_milestones_wizard",
            "action_open_add_milestone_wizard",
        ):
            self.assertEqual(
                len(tab[0].xpath(".//button[@name='%s']" % button)),
                1,
                button,
            )
        listed = [
            node.get("name")
            for node in tab[0].xpath(".//field[@name='milestone_ids']//field")
        ]
        for field_name in (
            "name",
            "planned_arrival_at",
            "actual_arrival_at",
            "actual_departure_at",
            "state",
        ):
            self.assertIn(field_name, listed, field_name)
        self.assertEqual(
            len(form.xpath("//button[@name='action_open_trip_milestones']")),
            1,
        )
        self.assertEqual(
            len(
                form.xpath(
                    "//page[@name='route_timing']//field[@name='origin_location']"
                )
            ),
            1,
        )

    def test_milestone_form_wires_the_workflow_and_manager_only_actions(self):
        form = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_transport_milestone_form"
            ).get_combined_arch()
        )
        for button in (
            "action_open_arrival_wizard",
            "action_open_departure_wizard",
            "action_open_correction_wizard",
            "action_open_skip_wizard",
        ):
            self.assertEqual(
                len(form.xpath("//button[@name='%s']" % button)),
                1,
                button,
            )
        for button in (
            "action_open_correction_wizard",
            "action_open_skip_wizard",
        ):
            self.assertEqual(
                form.xpath("//button[@name='%s']" % button)[0].get("groups"),
                MANAGER_GROUP,
                button,
            )
        history = [
            node.get("name") for node in form.xpath("//field[@name='event_ids']//field")
        ]
        for field_name in (
            "recorded_at",
            "event_type",
            "source",
            "occurred_at",
            "user_id",
            "old_value",
            "new_value",
            "reason",
        ):
            self.assertIn(field_name, history, field_name)
        self.assertEqual(len(form.xpath("//field[@name='notes']")), 1)
        self.assertEqual(
            len(form.xpath("//field[@name='notes']")[0].get("readonly") or ""),
            0,
        )

    def test_milestone_menu_and_search_are_available_to_dispatchers(self):
        menu = self.env.ref("taj_transport_operations.menu_transport_milestones")
        self.assertIn(self.env.ref(DISPATCHER_GROUP), menu.group_ids)
        action = self.env.ref("taj_transport_operations.action_transport_milestone")
        self.assertEqual(action.res_model, "taj.transport.milestone")
        search = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_transport_milestone_search"
            ).get_combined_arch()
        )
        self.assertTrue(search.xpath("//filter[@name='group_state']"))
        self.assertTrue(search.xpath("//filter[@name='open']"))

    def _replace_grouping_truck(self, trip, effective_from):
        truck = self.env["fleet.vehicle"].create({
            "model_id": self.truck_model.id,
            "license_plate": "MILESTONE-GROUP-REPLACEMENT",
        })
        self.env["taj.transport.replace.assignment.wizard"].create({
            "trip_id": trip.id,
            "expected_assignment_id": trip.current_assignment_id.id,
            "vehicle_id": truck.id,
            "driver_id": self.driver.id,
            "effective_from": effective_from,
            "reason": "Replace truck during checkpoint operations",
        }).action_replace_resources()
        return truck

    def test_truck_grouping_keeps_completed_work_under_its_departure_truck(self):
        trip = self._checkpoint_trip("TRUCKGROUP")
        loading = self._milestone(trip, "Loading")
        now = fields.Datetime.now()
        self._record(loading, "arrival", event_at=now - timedelta(hours=3))
        self._record(loading, "departure", event_at=now - timedelta(hours=2))
        truck = self._replace_grouping_truck(trip, now - timedelta(hours=1))
        self.assertEqual(loading.vehicle_id, self.truck)
        self.assertEqual(self._milestone(trip, "Offloading").vehicle_id, truck)
        groups = self.env["taj.transport.milestone"]._read_group(
            [("trip_id", "=", trip.id)], ["vehicle_id", "trip_id"], ["__count"],
        )
        self.assertEqual(
            {(vehicle.id, parent.id): count for vehicle, parent, count in groups},
            {(self.truck.id, trip.id): 1, (truck.id, trip.id): 2},
        )

    def test_departure_correction_reassigns_truck_at_replacement_boundary(self):
        trip = self._checkpoint_trip("TRUCKCORRECTION")
        loading = self._milestone(trip, "Loading")
        now = fields.Datetime.now()
        self._record(loading, "arrival", event_at=now - timedelta(hours=3))
        self._record(loading, "departure", event_at=now - timedelta(hours=2))
        boundary = now - timedelta(hours=1)
        truck = self._replace_grouping_truck(trip, boundary)
        history = [(row.id, row.effective_from, row.effective_to)
                   for row in trip.assignment_ids]
        self._manager_milestone(loading)._correct_timestamp(
            "actual_departure_at", boundary, "Correct departure to swap time",
        )
        self.assertEqual(loading.vehicle_id, truck)
        self._manager_milestone(loading)._correct_timestamp(
            "actual_departure_at", now - timedelta(hours=2),
            "Restore departure before replacement",
        )
        self.assertEqual(loading.vehicle_id, self.truck)
        self._manager_milestone(loading)._correct_timestamp(
            "actual_departure_at", False, "Reopen the checkpoint",
        )
        self.assertEqual(loading.state, "arrived")
        self.assertEqual(loading.vehicle_id, truck)
        self.assertEqual(history, [(row.id, row.effective_from, row.effective_to)
                                   for row in trip.assignment_ids])

    def test_historical_truck_is_unassigned_when_event_precedes_history(self):
        trip = self._checkpoint_trip("UNKNOWNTRUCK")
        loading = self._milestone(trip, "Loading")
        now = fields.Datetime.now()
        self._record(loading, "arrival", event_at=now - timedelta(hours=6))
        self._record(loading, "departure", event_at=now - timedelta(hours=5))
        self.assertFalse(loading.vehicle_id)
        self.assertEqual(self._milestone(trip, "Offloading").vehicle_id, self.truck)

    def test_draft_truck_fallback_does_not_override_driver_only_assignment(self):
        route = self._route("Unassigned checkpoint grouping")
        line = self._sale_line("TRUCKFALLBACK", vehicle_id=self.truck.id)
        trip = self._trip(route, line=line)
        self.assertFalse(trip.assignment_ids)
        self.assertEqual(trip.milestone_ids.mapped("vehicle_id"), self.truck)
        self.env["taj.transport.assignment"].create({
            "trip_id": trip.id,
            "driver_id": self.driver.id,
        })
        self.assertFalse(trip.milestone_ids.mapped("vehicle_id"))
        unassigned = self._trip(route, tag="NOTRUCK")
        self.assertFalse(unassigned.milestone_ids.mapped("vehicle_id"))

    def test_closed_trip_keeps_truck_at_final_boundary_and_after_archiving(self):
        trip = self._checkpoint_trip("CLOSEDTRUCK", state="in_progress")
        loading = self._milestone(trip, "Loading")
        arrival = fields.Datetime.now() - timedelta(minutes=30)
        self._record(loading, "arrival", event_at=arrival - timedelta(minutes=10))
        self._record(loading, "departure", event_at=arrival)
        trip.write({"actual_arrival_at": arrival})
        trip.action_complete_trip()
        self.assertEqual(trip.milestone_ids.mapped("vehicle_id"), self.truck)
        self.truck.active = False
        self.assertEqual(loading.vehicle_id, self.truck)

    def test_skipped_milestone_keeps_truck_at_skip_time(self):
        trip = self._checkpoint_trip("SKIPPEDTRUCK")
        loading = self._milestone(trip, "Loading")
        now = fields.Datetime.now()
        with freeze_time(now - timedelta(hours=2)):
            self._record(loading, "skip", reason="Loading not applicable")
        truck = self._replace_grouping_truck(trip, now - timedelta(hours=1))
        self.assertEqual(loading.vehicle_id, self.truck)
        self.assertEqual(self._milestone(trip, "Offloading").vehicle_id, truck)

    def test_cancelling_trip_keeps_historical_and_last_truck_groups(self):
        trip = self._checkpoint_trip("CANCELLEDTRUCK")
        loading = self._milestone(trip, "Loading")
        now = fields.Datetime.now()
        self._record(loading, "arrival", event_at=now - timedelta(hours=3))
        self._record(loading, "departure", event_at=now - timedelta(hours=2))
        truck = self._replace_grouping_truck(trip, now - timedelta(hours=1))
        self._cancel_trip(trip)
        self.assertEqual(loading.vehicle_id, self.truck)
        self.assertEqual(self._milestone(trip, "Offloading").vehicle_id, truck)

    # ------------------------------------------------------------------
    # E. Independent review regressions: forged context, forged values and
    #    the closure of checkpoint notes
    # ------------------------------------------------------------------
    def test_forged_context_cannot_bypass_the_checkpoint_guards(self):
        trip = self._checkpoint_trip("FORGED")
        border = self._milestone(trip, "Kasumbalesa border")
        moment = fields.Datetime.now() - timedelta(hours=1)
        self._record(border, "arrival", event_at=moment)
        forged = {
            "active_test": False,
            "allow_checkpoint_write": True,
            "taj_correction": True,
            "sudo": True,
        }
        # A dispatcher cannot record a value by writing the field, whatever
        # the context claims: the guard lives in the model.
        for values in (
            {"actual_arrival_at": moment + timedelta(hours=1)},
            {"state": "completed"},
            {"arrived_by_id": self.dispatcher.id},
        ):
            with (
                self.assert_raises_any(AccessError, UserError),
                self.env.cr.savepoint(),
            ):
                border.with_user(self.dispatcher).with_context(**forged).write(values)
        # Neither can a manager: a recorded time only moves through the
        # reasoned correction workflow.
        for values in (
            {"actual_arrival_at": moment},
            {"confirmed_at": moment},
            {"type_name": "Forged snapshot"},
            {"template_line_id": False},
            {"arrived_by_id": self.manager.id},
        ):
            with (
                self.assert_raises_any(AccessError, UserError),
                self.env.cr.savepoint(),
            ):
                self._manager_milestone(border).with_context(**forged).write(values)
        # The records that carry the history stay server-managed.
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            border.with_user(self.dispatcher).with_context(**forged).unlink()
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            border.with_user(self.manager).with_context(**forged).unlink()
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            (
                self.env["taj.transport.milestone"]
                .with_user(self.dispatcher)
                .with_context(**forged)
                .create({"trip_id": trip.id, "name": "Forged checkpoint"})
            )
        audit = self._events(border, "arrival")
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            (
                self.env["taj.transport.milestone.event"]
                .with_user(self.dispatcher)
                .with_context(**forged)
                .create(
                    {
                        "milestone_id": border.id,
                        "event_type": "departure",
                    }
                )
            )
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            audit.with_user(self.manager).with_context(**forged).unlink()
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            audit.with_user(self.manager).with_context(**forged).write(
                {"occurred_at": moment - timedelta(days=1)}
            )
        # A dispatcher cannot use the manager correction either.
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            border.with_user(self.dispatcher).with_context(**forged)._correct_timestamp(
                "actual_arrival_at",
                moment + timedelta(hours=2),
                "forged correction",
            )
        border.invalidate_recordset()
        self.assertAlmostEqual(
            border.actual_arrival_at,
            moment,
            delta=timedelta(seconds=1),
        )
        self.assertEqual(border.arrived_by_id, self.env.user)
        self.assertEqual(border.state, "arrived")
        self.assertEqual(len(audit), 1)

    def test_manual_checkpoint_rejects_injected_values(self):
        trip = self._checkpoint_trip("INJECT")
        template_line = trip.milestone_ids[0].template_line_id
        before = len(trip.milestone_ids)
        rejected = (
            {"name": "Forged", "state": "completed"},
            {"name": "Forged", "actual_arrival_at": fields.Datetime.now()},
            {"name": "Forged", "confirmed_at": fields.Datetime.now()},
            {"name": "Forged", "type_name": "Forged snapshot"},
            {"name": "Forged", "template_line_id": template_line.id},
            {"name": "Forged", "route_id": trip.route_id.id},
            {"name": "Forged", "arrived_by_id": self.manager.id},
        )
        for values in rejected:
            with self.assertRaises(UserError) as error, self.env.cr.savepoint():
                trip._add_manual_milestone(values)
            self.assertIn("workflow decides", str(error.exception))
        trip.invalidate_recordset()
        self.assertEqual(len(trip.milestone_ids), before)
        # The documented planning values still work, and they are the only
        # values the new checkpoint receives.
        manual = trip._add_manual_milestone(
            {
                "name": "Weighbridge stop",
                "milestone_type_id": self.type_border.id,
                "planned_offset_hours": 3.5,
                "notes": "Mandatory weighbridge",
            }
        )
        self.assertEqual(manual.state, "pending")
        self.assertFalse(manual.requires_confirmation)
        self.assertFalse(manual.template_line_id)
        self.assertFalse(manual.actual_arrival_at)
        self.assertEqual(manual.type_name, self.type_border.name)
        trip.invalidate_recordset()
        self.assertEqual(len(trip.milestone_ids), before + 1)

    def test_notes_are_closed_when_the_trip_is_terminal(self):
        route = self._route("Notes route")
        ready = self._ready_trip(route, tag="NOTES1")
        self.assertTrue(
            self._milestone(ready, "Loading").write(
                {"notes": "Waiting for the gate pass"}
            )
        )
        started = self._started_trip(route, tag="NOTES2")
        self.assertTrue(
            self._milestone(started, "Loading").write({"notes": "In transit"})
        )
        completed = self._ready_trip(route, tag="NOTES3")
        closed = self._milestone(completed, "Loading")
        self.assertTrue(closed.write({"notes": "Loaded 24 pallets"}))
        completed.write(
            {"actual_departure_at": fields.Datetime.now() - timedelta(hours=3)}
        )
        completed.action_start_trip()
        completed.action_complete_trip()
        completed.invalidate_recordset()
        self.assertEqual(completed.state, "completed")
        for user in (self.dispatcher, self.manager):
            with (
                self.assert_raises_any(AccessError, UserError),
                self.env.cr.savepoint(),
            ):
                closed.with_user(user).write({"notes": "Changed after completion"})
        closed.invalidate_recordset()
        self.assertEqual(closed.notes, "Loaded 24 pallets")
        cancelled = self._cancel_trip(self._ready_trip(route, tag="NOTES4"))
        cancelled_milestone = self._milestone(cancelled, "Loading")
        with self.assert_raises_any(AccessError, UserError), self.env.cr.savepoint():
            cancelled_milestone.with_user(self.manager).write(
                {"notes": "Changed after cancellation"}
            )
        cancelled_milestone.invalidate_recordset()
        self.assertFalse(cancelled_milestone.notes)

    def test_recording_takes_the_actor_from_the_environment(self):
        trip = self._checkpoint_trip("ACTOR")
        border = self._milestone(trip, "Kasumbalesa border")
        moment = fields.Datetime.now() - timedelta(minutes=30)
        self._record(border, "arrival", user=self.dispatcher, event_at=moment)
        border.invalidate_recordset()
        arrivals = self._events(border, "arrival")
        self.assertEqual(len(arrivals), 1)
        self.assertEqual(arrivals.user_id, self.dispatcher)
        self.assertEqual(border.arrived_by_id, self.dispatcher)
        self.assertNotEqual(arrivals.user_id, self.env.user)
        self.assertAlmostEqual(
            arrivals.occurred_at,
            moment,
            delta=timedelta(seconds=1),
        )

    def test_recording_wizards_cannot_express_protected_values(self):
        protected = (
            "user_id",
            "arrived_by_id",
            "departed_by_id",
            "confirmed_by_id",
            "state",
            "actual_arrival_at",
            "actual_departure_at",
            "confirmed_at",
            "type_name",
            "template_line_id",
            "event_ids",
        )
        for model_name in (
            "taj.transport.milestone.action.wizard",
            "taj.transport.milestone.add.wizard",
            "taj.transport.regenerate.milestones.wizard",
            "taj.transport.milestone.correction.wizard",
        ):
            available = self.env[model_name]._fields
            for name in protected:
                self.assertNotIn(
                    name,
                    available,
                    "%s must not let a client post %s" % (model_name, name),
                )

    # ------------------------------------------------------------------
    # F. Confirmation integrity under manager corrections
    # ------------------------------------------------------------------
    def _confirmed_loading(self, tag, with_departure=True):
        """Simulate a confirmation saved before arrival/departure simplification."""
        trip = self._checkpoint_trip(tag)
        loading = self._milestone(trip, "Loading")
        now = fields.Datetime.now()
        self._record(loading, "arrival", event_at=now - timedelta(hours=4))
        if with_departure:
            self._record(loading, "departure", event_at=now - timedelta(hours=2))
        self.env.cr.execute(
            "UPDATE taj_transport_milestone SET confirmed_at = %s, "
            "confirmed_by_id = %s, confirmation_note = %s, state = 'completed' "
            "WHERE id = %s",
            (now, self.env.user.id, "Loading completed", loading.id),
        )
        loading.invalidate_recordset()
        self.env["taj.transport.milestone.event"].sudo().create({
            "milestone_id": loading.id,
            "event_type": "confirmation",
            "user_id": self.env.user.id,
            "occurred_at": now,
            "note": "Loading completed",
        })
        return trip, loading

    def test_correcting_a_departure_keeps_a_valid_confirmation(self):
        """Changing a recorded time never withdraws the business confirmation."""
        _trip, loading = self._confirmed_loading("CONFKEEP")
        confirmed_at = loading.confirmed_at
        confirmed_by = loading.confirmed_by_id
        manager_milestone = self._manager_milestone(loading)
        corrected = loading.actual_departure_at - timedelta(minutes=30)
        self.assertTrue(
            manager_milestone._correct_timestamp(
                "actual_departure_at",
                corrected,
                "The gate paper log shows an earlier departure",
            )
        )
        loading.invalidate_recordset()
        self.assertAlmostEqual(
            loading.actual_departure_at,
            corrected,
            delta=timedelta(seconds=1),
        )
        # The confirmation is still the recorded one, untouched.
        self.assertAlmostEqual(
            loading.confirmed_at,
            confirmed_at,
            delta=timedelta(seconds=1),
        )
        self.assertEqual(loading.confirmed_by_id, confirmed_by)
        self.assertEqual(loading.confirmation_note, "Loading completed")
        self.assertEqual(loading.state, "departed")
        self.assertFalse(self._events(loading, "confirmation_cancelled"))
        self.assertEqual(
            len(self._events(loading, "confirmation")),
            1,
            "The original confirmation event must stay in the trail",
        )

    def test_clearing_a_departure_keeps_a_supported_confirmation(self):
        """Clearing departure reopens the checkpoint and retains its history."""
        trip, loading = self._confirmed_loading("CONFKEEP2")
        confirmed_at = loading.confirmed_at
        arrival = loading.actual_arrival_at
        departure = loading.actual_departure_at
        self.assertTrue(
            self._manager_milestone(loading)._correct_timestamp(
                "actual_departure_at",
                False,
                "The departure was never recorded, the truck is still on site",
            )
        )
        loading.invalidate_recordset()
        self.assertFalse(loading.actual_departure_at)
        self.assertAlmostEqual(
            loading.actual_arrival_at,
            arrival,
            delta=timedelta(seconds=1),
        )
        # Historical confirmation evidence stays independent of movement.
        self.assertAlmostEqual(
            loading.confirmed_at,
            confirmed_at,
            delta=timedelta(seconds=1),
        )
        self.assertEqual(loading.confirmation_note, "Loading completed")
        self.assertEqual(loading.state, "arrived")
        self.assertFalse(self._events(loading, "confirmation_cancelled"))
        correction = self._events(loading, "correction")
        self.assertEqual(len(correction), 1)
        self.assertEqual(correction.field_name, "actual_departure_at")
        # The cleared departure is reported before/after like any correction.
        self.assertEqual(correction.old_value, fields.Datetime.to_string(departure))
        self.assertFalse(correction.new_value)
        trip.invalidate_recordset()
        self.assertEqual(trip.milestone_done_count, 0)

    def test_editing_legacy_arrival_preserves_confirmation_history(self):
        _trip, loading = self._confirmed_loading("CONFHISTORY", with_departure=False)
        confirmed_at = loading.confirmed_at
        self._manager_milestone(loading)._correct_timestamp(
            "actual_arrival_at", False, "Arrival belonged to another checkpoint",
        )
        loading.invalidate_recordset()
        self.assertFalse(loading.actual_arrival_at)
        self.assertEqual(loading.state, "pending")
        self.assertEqual(loading.confirmed_at, confirmed_at)
        self.assertEqual(len(self._events(loading, "confirmation")), 1)
        self.assertEqual(len(self._events(loading, "correction")), 1)

    def test_historical_confirmation_cannot_be_cancelled(self):
        _trip, loading = self._confirmed_loading("CONFREADONLY", with_departure=False)
        confirmed_at = loading.confirmed_at
        with self.assertRaises(UserError):
            self._manager_milestone(loading)._cancel_confirmation("Retired action")
        loading.invalidate_recordset()
        self.assertEqual(loading.confirmed_at, confirmed_at)
        self.assertFalse(self._events(loading, "confirmation_cancelled"))

    def test_retired_confirmation_dialog_is_not_available(self):
        _trip, loading = self._confirmed_loading("CONFRETIRED", with_departure=False)
        for user in (self.dispatcher, self.manager):
            with self.assertRaises(UserError):
                loading.with_user(user).action_open_cancel_confirmation_wizard()
        self.assertTrue(loading.confirmed_at)

    # ------------------------------------------------------------------
    # G. Progress semantics
    # ------------------------------------------------------------------
    def test_progress_counts_departures_and_reopens_after_edit(self):
        trip = self._checkpoint_trip("PROGRESS")
        loading = self._milestone(trip, "Loading")
        border = self._milestone(trip, "Kasumbalesa border")
        offloading = self._milestone(trip, "Offloading")
        now = fields.Datetime.now()
        self._record(loading, "arrival", event_at=now - timedelta(hours=4))
        self._record(border, "arrival", event_at=now - timedelta(hours=2))
        self.assertEqual(loading.state, "arrived")
        self.assertEqual(trip.milestone_done_count, 0)
        self._record(loading, "departure", event_at=now - timedelta(hours=3))
        self._record(border, "departure", event_at=now - timedelta(hours=1))
        self.assertEqual(trip.milestone_done_count, 2)
        self.assertAlmostEqual(trip.milestone_progress, 66.67, places=1)
        self._record(offloading, "skip", user=self.manager, reason="Not applicable")
        self.assertAlmostEqual(trip.milestone_progress, 100.0)
        self._manager_milestone(loading)._correct_timestamp(
            "actual_departure_at", False, "Departure was entered by mistake",
        )
        trip.invalidate_recordset()
        self.assertEqual(loading.state, "arrived")
        self.assertEqual(trip.milestone_done_count, 1)
        self.assertEqual(trip.milestone_open_count, 1)
        self.assertAlmostEqual(trip.milestone_progress, 50.0)
