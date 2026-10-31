"""Operational updates reset inactivity; alerts repeat without hourly spam."""
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .phase3_case import TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestTripActivity(TajTransportPhase3Case):
    user_tag = "trip_activity"

    def test_operational_history_preserves_odoo_activity_mixin(self):
        Trip = self.env["taj.transport.trip"]
        self.assertEqual(Trip._fields["activity_ids"].comodel_name, "mail.activity")
        self.assertEqual(Trip._fields["trip_activity_ids"].comodel_name, "taj.transport.trip.activity")
        related = Trip._fields["activity_type_id"].related
        path = related.split(".") if isinstance(related, str) else related
        self.assertEqual(tuple(path), ("activity_ids", "activity_type_id"))

    def test_manual_submission_records_author_and_preserves_list_navigation(self):
        trip = self._started_trip(tag="UPDATE")
        wizard = self.env["taj.transport.trip.activity.wizard"].with_user(self.dispatcher).create({
            "trip_id": trip.id, "description": "  Waiting at border  ",
        })
        self.assertEqual(wizard.action_submit(), {"type": "ir.actions.act_window_close"})
        activity = trip.trip_activity_ids
        self.assertEqual(activity.description, "Waiting at border")
        self.assertEqual(activity.user_id, self.dispatcher)
        self.assertEqual(trip.last_activity_at, activity.recorded_at)
        with self.assertRaises(AccessError):
            activity.with_user(self.dispatcher).write({"description": "Changed"})

    def test_blank_submission_does_not_reset_clock(self):
        trip = self._started_trip(tag="BLANK")
        wizard = self.env["taj.transport.trip.activity.wizard"].create({
            "trip_id": trip.id, "description": "   ",
        })
        with self.assertRaises(ValidationError):
            wizard.action_submit()
        self.assertFalse(trip.last_activity_at)
        self.assertFalse(trip.trip_activity_ids)

    def test_arrival_and_departure_count_but_corrections_do_not(self):
        route = self._route("Activity movement route")
        trip = self._started_trip(route, tag="MOVEMENT")
        checkpoint = self._milestone(trip, "Loading")
        self._record(checkpoint, "arrival")
        self._record(checkpoint, "departure")
        self.assertEqual(set(trip.trip_activity_ids.mapped("activity_type")), {"arrival", "departure"})
        self.assertEqual(len(trip.trip_activity_ids), 2)
        self.assertTrue(all(trip.trip_activity_ids.mapped("source_event_id")))
        last = trip.last_activity_at
        checkpoint._correct_timestamp(
            "actual_arrival_at", checkpoint.actual_arrival_at - timedelta(seconds=1),
            "Verified timestamp",
        )
        self.assertEqual(trip.last_activity_at, last)
        self.assertEqual(len(trip.trip_activity_ids), 2)

    def test_alerts_only_for_in_progress_and_repeat_after_full_interval(self):
        self.company.write({
            "transport_inactivity_enabled": True,
            "transport_inactivity_hours": 24,
            "transport_inactivity_recipient": "operations@example.com",
        })
        trip = self._started_trip(tag="ALERT")
        draft = self._planned_trip(tag="DRAFTALERT")
        start = fields.Datetime.now()
        Mail = self.env["mail.mail"]
        baseline = Mail.search_count([("email_to", "=", "operations@example.com")])
        with freeze_time(start + timedelta(hours=24, minutes=1)):
            self.env["taj.transport.trip"]._cron_queue_inactivity_alerts()
            self.env["taj.transport.trip"]._cron_queue_inactivity_alerts()
            self.assertEqual(Mail.search_count([("email_to", "=", "operations@example.com")]), baseline + 1)
            self.assertTrue(trip.last_inactivity_alert_at)
            self.assertFalse(draft.last_inactivity_alert_at)
        with freeze_time(start + timedelta(hours=48, minutes=2)):
            self.env["taj.transport.trip"]._cron_queue_inactivity_alerts()
            self.assertEqual(Mail.search_count([("email_to", "=", "operations@example.com")]), baseline + 2)
            self.env["taj.transport.trip.activity.wizard"].create({
                "trip_id": trip.id, "description": "Cleared the border",
            }).action_submit()
        with freeze_time(start + timedelta(hours=49)):
            self.env["taj.transport.trip"]._cron_queue_inactivity_alerts()
            self.assertEqual(Mail.search_count([("email_to", "=", "operations@example.com")]), baseline + 2)

    def test_settings_reject_invalid_threshold_and_recipient(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.company.write({"transport_inactivity_hours": 0})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.company.write({"transport_inactivity_recipient": "not-an-email"})

    def test_activity_clock_cannot_be_forged_with_a_trip_edit(self):
        trip = self._started_trip(tag="CLOCK")
        with self.assertRaises(UserError):
            trip.write({"last_activity_at": fields.Datetime.now()})
        with self.assertRaises(UserError):
            trip.write({"last_inactivity_alert_at": fields.Datetime.now()})
        self.assertFalse(trip.last_activity_at)
