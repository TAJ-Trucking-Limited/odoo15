"""Selected milestone list actions reuse the trip and checkpoint workflows."""
from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .phase3_case import TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestMilestoneSelectionActions(TajTransportPhase3Case):
    user_tag = "milestone_selection"

    def _milestones(self, tag="SELECT", state="ready"):
        route = self._route(tag + " route")
        trip = self._ready_trip(route, tag=tag) if state == "ready" else self._planned_trip(route, tag=tag)
        return trip.milestone_ids.with_user(self.dispatcher)

    def test_list_has_three_selection_only_header_buttons(self):
        view = self.env.ref("taj_transport_operations.view_transport_milestone_list")
        root = etree.fromstring(view.get_combined_arch())
        buttons = root.xpath("./header/button")
        self.assertEqual([button.get("name") for button in buttons], [
            "action_open_trip", "action_open_next_event_wizard", "action_open_record_activity",
        ])
        self.assertTrue(all(button.get("type") == "object" for button in buttons))
        self.assertTrue(all(button.get("display") != "always" for button in buttons))

    def test_open_trip_accepts_multiple_milestones_on_one_trip(self):
        selected = self._milestones()[:2]
        action = selected.action_open_trip()
        self.assertEqual(action["res_model"], "taj.transport.trip")
        self.assertEqual(action["res_id"], selected.trip_id.id)
        self.assertEqual(action["target"], "current")

    def test_record_activity_targets_the_selected_trip(self):
        selected = self._milestones()[:2]
        trip = selected.trip_id
        action = selected.action_open_record_activity()
        self.assertEqual(action["res_model"], "taj.transport.trip.activity.wizard")
        self.assertEqual(action["context"]["default_trip_id"], trip.id)
        self.assertFalse(trip.trip_activity_ids)
        self.env[action["res_model"]].with_user(self.dispatcher).with_context(
            action["context"]
        ).create({"description": "Waiting at checkpoint"}).action_submit()
        self.assertEqual(trip.trip_activity_ids.description, "Waiting at checkpoint")
        self.assertEqual(trip.trip_activity_ids.user_id, self.dispatcher)
        self.assertTrue(all(m.state == "pending" for m in selected))

    def test_event_button_switches_from_arrival_to_departure(self):
        milestone = self._milestones()[:1]
        for kind, expected_state in (("arrival", "arrived"), ("departure", "departed")):
            action = milestone.action_open_next_event_wizard()
            self.assertEqual(action["context"]["default_action_type"], kind)
            self.assertEqual(action["context"]["default_milestone_id"], milestone.id)
            self.env[action["res_model"]].with_user(self.dispatcher).with_context(
                action["context"]
            ).create({}).action_confirm()
            self.assertEqual(milestone.state, expected_state)
        self.assertEqual(set(milestone.event_ids.mapped("event_type")), {"arrival", "departure"})
        with self.assertRaisesRegex(UserError, "finished"):
            milestone.action_open_next_event_wizard()

    def test_event_button_rejects_multiple_or_empty_selection(self):
        milestones = self._milestones()
        for selected in (milestones[:2], milestones[:0]):
            with self.assertRaisesRegex(UserError, "one milestone"):
                selected.action_open_next_event_wizard()
        self.assertFalse(milestones.event_ids)

    def test_trip_actions_reject_mixed_trips_and_empty_selection(self):
        milestones = self._milestones("FIRST")[:1] | self._milestones("SECOND")[:1]
        for selected in (milestones, milestones[:0]):
            for name in ("action_open_trip", "action_open_record_activity"):
                with self.assertRaisesRegex(UserError, "one trip"):
                    getattr(selected, name)()

    def test_event_button_keeps_draft_and_skipped_guards(self):
        milestone = self._milestones(state="draft")[:1]
        with self.assertRaisesRegex(UserError, "Ready"):
            milestone.action_open_next_event_wizard()
        skipped = self._milestones("SKIPPED")[:1]
        skipped.with_user(self.manager)._skip_checkpoint("Not required")
        with self.assertRaisesRegex(UserError, "finished"):
            skipped.action_open_next_event_wizard()

    def test_closed_trip_guards_are_retained(self):
        selected = self._milestones(state="draft")[:1]
        self._cancel_trip(selected.trip_id)
        with self.assertRaises(UserError):
            selected.action_open_next_event_wizard()
        with self.assertRaises(UserError):
            selected.action_open_record_activity()
        self.assertEqual(selected.action_open_trip()["res_id"], selected.trip_id.id)

    def test_selection_buttons_enforce_milestone_access(self):
        milestone = self._milestones()[:1]
        outsider = new_test_user(self.env, login="milestone_selection_outsider", groups="base.group_user")
        for name in ("action_open_trip", "action_open_record_activity", "action_open_next_event_wizard"):
            with self.assertRaises(AccessError):
                getattr(milestone.with_user(outsider), name)()
