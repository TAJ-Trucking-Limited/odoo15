"""Tests for transport trip email updates, scheduling, validation, and audit."""

from datetime import timedelta
from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .phase3_case import TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestTripEmailUpdates(TajTransportPhase3Case):
    user_tag = "email_updates"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create unrelated commercial partner
        cls.other_customer = cls.env["res.partner"].create({
            "name": "Other Company Ltd",
            "email": "other@company.com",
        })

    def _child_contact(self, trip, name="Phase 3 Child Contact", email="contact@customer.com"):
        return self.env["res.partner"].create({
            "name": name,
            "parent_id": trip.partner_id.id,
            "email": email,
        })

    def test_recipient_validation(self):
        """Contacts must belong to the customer, emails must be valid, extras are normalized."""
        trip = self._planned_trip(tag="REC1")
        contact_child = self._child_contact(trip)

        # 1. Child contact of same customer is valid
        trip.write({"email_partner_ids": [(4, contact_child.id)]})
        self.assertIn(contact_child, trip.email_partner_ids)

        # 2. Contact of another company is rejected
        with self.assertRaises(ValidationError):
            trip.write({"email_partner_ids": [(4, self.other_customer.id)]})

        # 3. Contact without valid email is rejected
        invalid_contact = self.env["res.partner"].create({
            "name": "No Email Contact",
            "parent_id": trip.partner_id.id,
            "email": False,
        })
        with self.assertRaises(ValidationError):
            trip.write({"email_partner_ids": [(4, invalid_contact.id)]})

        # 4. Extra email with control characters is rejected
        with self.assertRaises(ValidationError):
            trip.write({"email_extra_recipients": "bad@test.com\r\ninjection@test.com"})

        # 5. Invalid extra email format is rejected
        with self.assertRaises(ValidationError):
            trip.write({"email_extra_recipients": "not-an-email, valid@test.com"})

        # 6. Valid comma-separated extra emails normalized and deduplicated
        trip.write({
            "email_partner_ids": [(6, 0, [contact_child.id])],
            "email_extra_recipients": "  extra1@test.com , CONTACT@CUSTOMER.COM, extra2@test.com  ",
        })
        recipients = trip._get_email_recipients()
        self.assertEqual(len(recipients), 3)
        self.assertEqual(recipients, ["contact@customer.com", "extra1@test.com", "extra2@test.com"])

        # 7. Enabling automated updates with no recipients is rejected
        empty_trip = self._planned_trip(tag="REC2")
        with self.assertRaises(ValidationError):
            empty_trip.write({"email_notification_enabled": True})

    def test_recipient_revalidation_at_dispatch_rejects_partial(self):
        """Changes to contacts after trip configuration must abort dispatch without partial sends."""
        trip = self._ready_trip(tag="REVAL1")
        contact_a = self._child_contact(trip, name="Contact A", email="contact.a@customer.com")
        contact_b = self._child_contact(trip, name="Contact B", email="contact.b@customer.com")
        trip.write({
            "email_partner_ids": [(6, 0, [contact_a.id, contact_b.id])],
        })

        # Case 1: Contact B's email is removed afterwards
        contact_b.write({"email": False})
        mail_count_before = self.env["mail.mail"].search_count([("res_id", "=", trip.id)])
        dispatch_count_before = len(trip.email_dispatch_ids)

        with self.assertRaises(ValidationError):
            trip.action_send_email_update_manual()

        # No mail.mail or dispatch records created; no partial send to Contact A
        self.assertEqual(self.env["mail.mail"].search_count([("res_id", "=", trip.id)]), mail_count_before)
        self.assertEqual(len(trip.email_dispatch_ids), dispatch_count_before)

        # Restore email and verify dispatch works
        contact_b.write({"email": "contact.b@customer.com"})
        trip.action_send_email_update_manual()
        self.assertEqual(len(trip.email_dispatch_ids), dispatch_count_before + 1)

        # Case 2: Contact B's company changes to an unrelated commercial entity
        contact_b.write({"parent_id": self.other_customer.id})
        with self.assertRaises(ValidationError):
            trip.action_send_email_update_manual()

    def test_interval_validation(self):
        """Interval must be finite and >= 0.25 hours."""
        trip = self._planned_trip(tag="INT1")
        with self.assertRaises(ValidationError):
            trip.write({"email_interval_hours": 0.1})
        with self.assertRaises(ValidationError):
            trip.write({"email_interval_hours": -2.0})

        trip.write({"email_interval_hours": 2.5})
        self.assertEqual(trip.email_interval_hours, 2.5)

    def test_system_schedule_fields_protected(self):
        """Ordinary users and RPC callers cannot modify schedule or failure fields, even with context flags."""
        trip = self._planned_trip(tag="PROT1")
        now = fields.Datetime.now()

        # 1. Direct write blocked
        with self.assertRaises(AccessError):
            trip.with_user(self.dispatcher).write({
                "email_next_send_at": now,
            })
        with self.assertRaises(AccessError):
            trip.with_user(self.dispatcher).write({
                "email_last_queued_at": now,
            })
        with self.assertRaises(AccessError):
            trip.with_user(self.dispatcher).write({
                "email_failure_count": 5,
            })

        # 2. Context spoofing with legacy flag 'system_trip_email_schedule_write' is blocked
        with self.assertRaises(AccessError):
            trip.with_user(self.dispatcher).with_context(system_trip_email_schedule_write=True).write({
                "email_next_send_at": now,
            })

        # 3. Context spoofing with arbitrary internal token string is blocked
        with self.assertRaises(AccessError):
            trip.with_user(self.dispatcher).with_context(_taj_internal_schedule_token="fake_token").write({
                "email_next_send_at": now,
            })

        # 4. Legitimate internal write via _write_email_schedule succeeds
        trip._write_email_schedule({"email_next_send_at": now + timedelta(hours=3)})
        self.assertEqual(trip.email_next_send_at, now + timedelta(hours=3))

    def test_schedule_lifecycle_transitions(self):
        """Clocks update accurately across Draft -> Ready -> In Progress -> Complete / Cancel."""
        now = fields.Datetime.now()
        trip = self._planned_trip(tag="SCHED1")
        contact_child = self._child_contact(trip)
        trip.write({
            "email_partner_ids": [(4, contact_child.id)],
            "email_interval_hours": 4.0,
            "email_notification_enabled": True,
        })
        # Enabling on Draft does not set next_send_at
        self.assertFalse(trip.email_next_send_at)

        # Transition to Ready keeps timer empty
        trip.action_mark_ready()
        self.assertFalse(trip.email_next_send_at)

        # Transition to In Progress sets next_send_at to now + 4 hours
        with freeze_time(now):
            trip.write({"actual_departure_at": now - timedelta(minutes=10)})
            trip.action_start_trip()
            self.assertEqual(trip.state, "in_progress")
            self.assertEqual(trip.email_next_send_at, now + timedelta(hours=4.0))

            # Changing interval recalculates next_send_at
            trip.write({"email_interval_hours": 6.0})
            self.assertEqual(trip.email_next_send_at, now + timedelta(hours=6.0))

            # Disabling notifications clears next_send_at
            trip.write({"email_notification_enabled": False})
            self.assertFalse(trip.email_next_send_at)

            # Re-enabling while in progress recalculates next_send_at
            trip.write({"email_notification_enabled": True})
            self.assertEqual(trip.email_next_send_at, now + timedelta(hours=6.0))

            # Completing trip clears next_send_at
            trip.write({"actual_arrival_at": now - timedelta(minutes=2)})
            trip.action_complete_trip()
            self.assertEqual(trip.state, "completed")
            self.assertFalse(trip.email_next_send_at)

    def test_manual_dispatch(self):
        """Manual dispatch queues one separate mail per recipient without leaking other addresses."""
        now = fields.Datetime.now()
        trip = self._ready_trip(tag="MAN1")
        contact_child = self._child_contact(trip)
        trip.write({
            "email_partner_ids": [(4, contact_child.id)],
            "email_extra_recipients": "ops@client.com",
        })

        # Manual send on Draft is forbidden
        draft_trip = self._planned_trip(tag="MAN_DRAFT")
        draft_contact = self._child_contact(draft_trip)
        draft_trip.write({"email_partner_ids": [(4, draft_contact.id)]})
        with self.assertRaises(UserError):
            draft_trip.action_send_email_update_manual()

        # Manual send on Ready succeeds
        with freeze_time(now):
            action = trip.with_user(self.dispatcher).action_send_email_update_manual()
            self.assertEqual(action.get("type"), "ir.actions.client")

            dispatches = trip.email_dispatch_ids
            self.assertEqual(len(dispatches), 1)
            dispatch = dispatches[0]
            self.assertEqual(dispatch.dispatch_type, "manual")
            self.assertEqual(dispatch.requested_by_id, self.dispatcher)
            self.assertEqual(dispatch.mail_count, 2)
            self.assertEqual(trip.email_last_queued_at, now)

            # Check mail records: exactly one per recipient, no CC leaking
            mails = dispatch.mail_ids
            self.assertEqual(len(mails), 2)
            self.assertEqual(set(mails.mapped("email_to")), {"contact@customer.com", "ops@client.com"})
            self.assertEqual(dispatch.status, "queued")

            # Audit record is immutable
            with self.assertRaises(AccessError):
                dispatch.with_user(self.dispatcher).write({"recipient_emails": "hacked@test.com"})
            with self.assertRaises(AccessError):
                dispatch.with_user(self.dispatcher).unlink()

    def test_manual_dispatch_resets_in_progress_schedule(self):
        """Manual send on an enabled in-progress trip advances next_send_at from queue time."""
        now = fields.Datetime.now()
        trip = self._started_trip(tag="MAN_SCHED")
        contact_child = self._child_contact(trip)
        trip.write({
            "email_partner_ids": [(4, contact_child.id)],
            "email_interval_hours": 3.0,
            "email_notification_enabled": True,
        })
        self.assertTrue(trip.email_next_send_at)

        with freeze_time(now + timedelta(hours=1)):
            dispatch_time = now + timedelta(hours=1)
            trip.action_send_email_update_manual()
            self.assertEqual(trip.email_last_queued_at, dispatch_time)
            self.assertEqual(trip.email_next_send_at, dispatch_time + timedelta(hours=3.0))

    def test_cron_dispatch_and_no_bursts(self):
        """Cron queues due in-progress trips once and sets next send from actual queue time."""
        now = fields.Datetime.now()
        trip = self._started_trip(tag="CRON1")
        contact_child = self._child_contact(trip)
        trip.write({
            "email_partner_ids": [(4, contact_child.id)],
            "email_interval_hours": 4.0,
            "email_notification_enabled": True,
        })
        scheduled_time = trip.email_next_send_at

        # At now + 2 hours: cron should NOT dispatch
        with freeze_time(now + timedelta(hours=2)):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()
            self.assertFalse(trip.email_dispatch_ids)
            self.assertEqual(trip.email_next_send_at, scheduled_time)

        # At now + 10 hours (delayed cron): dispatches exactly once, advances from queue time
        queue_time = now + timedelta(hours=10)
        with freeze_time(queue_time):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()
            self.assertEqual(len(trip.email_dispatch_ids), 1)
            self.assertEqual(trip.email_dispatch_ids[0].dispatch_type, "automatic")
            self.assertEqual(trip.email_last_queued_at, queue_time)
            self.assertEqual(trip.email_next_send_at, queue_time + timedelta(hours=4.0))

    def test_cron_savepoint_isolation_on_failure(self):
        """A failure on one trip in cron does not roll back previous successful dispatches."""
        now = fields.Datetime.now()

        # Trip 1: healthy trip
        trip1 = self._started_trip(tag="ISO1")
        contact1 = self._child_contact(trip1, name="Iso Contact 1", email="iso1@client.com")
        trip1.write({
            "email_partner_ids": [(4, contact1.id)],
            "email_interval_hours": 4.0,
            "email_notification_enabled": True,
        })
        trip1._write_email_schedule({"email_next_send_at": now - timedelta(minutes=5)})

        # Trip 2: invalid contact (email cleared after setup)
        trip2 = self._started_trip(tag="ISO2")
        contact2 = self._child_contact(trip2, name="Iso Contact 2", email="iso2@client.com")
        trip2.write({
            "email_partner_ids": [(4, contact2.id)],
            "email_interval_hours": 4.0,
            "email_notification_enabled": True,
        })
        contact2.write({"email": False})  # Invalidate recipient
        trip2._write_email_schedule({"email_next_send_at": now - timedelta(minutes=5)})

        with freeze_time(now):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()

        # Trip 1 must have succeeded completely
        self.assertEqual(len(trip1.email_dispatch_ids), 1)
        self.assertEqual(trip1.email_dispatch_ids[0].status, "queued")
        self.assertEqual(trip1.email_last_queued_at, now)
        self.assertEqual(trip1.email_next_send_at, now + timedelta(hours=4.0))

        # Trip 2 must have failed cleanly and backed off
        self.assertEqual(len(trip2.email_dispatch_ids), 0)
        self.assertEqual(trip2.email_failure_count, 1)
        self.assertTrue(trip2.email_last_error)
        self.assertIn("valid email address", trip2.email_last_error)
        self.assertTrue(trip2.email_next_send_at > now)

    def test_cron_retry_bounded_backoff_and_starvation(self):
        """Cron applies controlled backoff, prevents starvation, suspends after 3 failures, and recovers."""
        now = fields.Datetime.now()
        trip_fail = self._started_trip(tag="BACKOFF_FAIL")
        contact_fail = self._child_contact(trip_fail, name="Fail Contact", email="fail@client.com")
        trip_fail.write({
            "email_partner_ids": [(4, contact_fail.id)],
            "email_interval_hours": 2.0,
            "email_notification_enabled": True,
        })
        contact_fail.write({"email": False})
        trip_fail._write_email_schedule({"email_next_send_at": now - timedelta(minutes=5)})

        # Attempt 1: Fails, failure_count = 1, backoff = 2h
        with freeze_time(now):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()
            self.assertEqual(trip_fail.email_failure_count, 1)
            self.assertEqual(trip_fail.email_next_send_at, now + timedelta(hours=2.0))

        # Attempt 2: After 2 hours, fails again, failure_count = 2, backoff = 4h
        time2 = now + timedelta(hours=2.0)
        with freeze_time(time2):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()
            self.assertEqual(trip_fail.email_failure_count, 2)
            self.assertEqual(trip_fail.email_next_send_at, time2 + timedelta(hours=4.0))

        # Attempt 3: After 4 hours, fails 3rd time, suspended (next_send_at = False)
        time3 = time2 + timedelta(hours=4.0)
        with freeze_time(time3):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()
            self.assertEqual(trip_fail.email_failure_count, 3)
            self.assertFalse(trip_fail.email_next_send_at)

        # Batch fairness: A healthy trip is processed without being starved by suspended trip
        trip_healthy = self._started_trip(tag="HEALTHY1")
        contact_healthy = self._child_contact(trip_healthy, name="Healthy Contact", email="healthy@client.com")
        trip_healthy.write({
            "email_partner_ids": [(4, contact_healthy.id)],
            "email_interval_hours": 1.0,
            "email_notification_enabled": True,
        })
        time4 = time3 + timedelta(hours=1.0)
        trip_healthy._write_email_schedule({"email_next_send_at": time4})

        with freeze_time(time4):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()
            self.assertEqual(len(trip_healthy.email_dispatch_ids), 1)

        # Recovery: User updates the invalid contact's email address
        contact_fail.write({"email": "recovered@client.com"})
        trip_fail.write({"email_partner_ids": [(6, 0, [contact_fail.id])]})
        self.assertEqual(trip_fail.email_failure_count, 0)
        self.assertFalse(trip_fail.email_last_error)
        self.assertTrue(trip_fail.email_next_send_at)

    def test_cron_fairness_with_bounded_batch(self):
        """A failed oldest due trip cannot permanently starve later due trips."""
        now = fields.Datetime.now()
        failing = self._started_trip(tag="FAIR_FAIL")
        healthy = self._started_trip(tag="FAIR_OK")
        failing_contact = self._child_contact(
            failing, name="Fairness Bad", email="fail@client.test",
        )
        healthy_contact = self._child_contact(
            healthy, name="Fairness Good", email="good@client.test",
        )
        failing.write({
            "email_partner_ids": [(4, failing_contact.id)],
            "email_notification_enabled": True,
        })
        healthy.write({
            "email_partner_ids": [(4, healthy_contact.id)],
            "email_notification_enabled": True,
        })
        failing_contact.write({"email": False})
        failing._write_email_schedule({
            "email_next_send_at": now - timedelta(minutes=2),
        })
        healthy._write_email_schedule({
            "email_next_send_at": now - timedelta(minutes=1),
        })

        # A one-record batch is the minimal equivalent of a full 50-record
        # failing batch. Backoff must move the failed trip past the healthy
        # trip so the *next* cron run picks up the waiting work.
        with freeze_time(now):
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates(
                batch_size=1,
            )
            self.assertEqual(failing.email_failure_count, 1)
            self.assertFalse(failing.email_dispatch_ids)
            self.assertFalse(healthy.email_dispatch_ids)
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates(
                batch_size=1,
            )
            self.assertEqual(len(healthy.email_dispatch_ids), 1)

    def test_timezone_consistency(self):
        """Email timestamps and footer label consistently use the trip company timezone."""
        trip = self._ready_trip(tag="TZ1")
        contact = self._child_contact(trip, email="tz@client.com")
        trip.write({"email_partner_ids": [(4, contact.id)]})

        # Set company timezone to Asia/Riyadh (+03:00)
        trip.company_id.partner_id.write({"tz": "Asia/Riyadh"})
        # Executing user in America/New_York (-05:00)
        self.env.user.write({"tz": "America/New_York"})

        utc_time = fields.Datetime.to_datetime("2026-06-15 12:00:00")
        formatted = trip._format_datetime_for_email(utc_time)
        # 12:00 UTC in Asia/Riyadh (+3) is 15:00
        self.assertIn("2026-06-15 15:00", formatted)
        self.assertIn("Asia/Riyadh", formatted)

        # In email HTML: both footer label and timestamp use Asia/Riyadh
        html = trip._render_trip_email_html()
        self.assertIn("All times displayed in Asia/Riyadh.", html)

    def test_child_contact_customer_order(self):
        """Sales Order with child contact customer uses parent commercial entity for contacts."""
        # Create parent company and child branches
        parent_customer = self.env["res.partner"].create({
            "name": "Global Logistics Corp",
            "is_company": True,
        })
        branch_delivery = self.env["res.partner"].create({
            "name": "Branch Delivery Port",
            "parent_id": parent_customer.id,
            "email": "branch@globallogistics.com",
        })
        finance_contact = self.env["res.partner"].create({
            "name": "HQ Finance Officer",
            "parent_id": parent_customer.id,
            "email": "finance@globallogistics.com",
        })

        # Sales Order booked for the child contact
        order = self.env["sale.order"].create({"partner_id": branch_delivery.id})
        line = self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.product.id,
            "name": "Freight",
            "product_uom_qty": 1,
            "price_unit": 100,
        })
        order.action_confirm()

        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": line.id,
            "origin_location": "Port A",
            "destination_location": "Warehouse B",
        })

        # Commercial partner is correctly resolved to parent company
        self.assertEqual(trip.commercial_partner_id, parent_customer)

        # Sibling child contact (finance_contact) is valid for email updates
        trip.write({
            "email_partner_ids": [(6, 0, [finance_contact.id])],
            "email_notification_enabled": True,
        })
        recipients = trip._get_email_recipients()
        self.assertEqual(recipients, ["finance@globallogistics.com"])

    def test_html_rendering_cargo_and_xss_escaping(self):
        """Both cargo type and description are rendered, and all dynamic values are escaped."""
        route = self._route("Escaping Route")
        # Set checkpoint name with special characters on route template before trip generation
        route.line_ids.sorted("sequence")[0].write({"name": "Weighbridge <Checkpoint #1>"})
        sale_line = self._sale_line(
            tag="HTML_SEC", vehicle_id=False, container_num="CONT<123>&",
        )
        trip = self._ready_trip(route=route, tag="HTML_SEC", line=sale_line)

        cargo_type = self.env["taj.transport.cargo.type"].create({"name": "Dangerous Goods"})
        trip.write({
            "cargo_type_id": cargo_type.id,
            "cargo_description": "Flammable Gas Cylinders <Class 2>",
            "origin_location": "Dar <Port>",
            "destination_location": "Kigoma <Hub>",
        })

        m1 = trip.milestone_ids.sorted("sequence")[0]
        arrival_time = fields.Datetime.now() - timedelta(minutes=30)
        m1._record_arrival(arrival_time, self.dispatcher)

        html = trip._render_trip_email_html()

        # Both cargo type and description are rendered
        self.assertIn("Dangerous Goods - Flammable Gas Cylinders", html)

        # XSS characters are strictly escaped
        self.assertIn("&lt;Class 2&gt;", html)
        self.assertNotIn("<Class 2>", html)
        self.assertIn("CONT&lt;123&gt;&amp;", html)
        self.assertNotIn("CONT<123>&", html)
        self.assertIn("Dar &lt;Port&gt;", html)
        self.assertNotIn("Dar <Port>", html)
        self.assertIn("Weighbridge &lt;Checkpoint #1&gt;", html)
        self.assertNotIn("Weighbridge <Checkpoint #1>", html)

    def test_concurrent_manual_and_cron_no_duplicate(self):
        """Concurrent manual dispatch and cron execution do not produce duplicate emails."""
        now = fields.Datetime.now()
        trip = self._started_trip(tag="CONCUR1")
        contact = self._child_contact(trip, email="concur@client.com")
        trip.write({
            "email_partner_ids": [(4, contact.id)],
            "email_interval_hours": 3.0,
            "email_notification_enabled": True,
        })
        trip._write_email_schedule({"email_next_send_at": now})

        with freeze_time(now):
            # 1. Manual dispatch executes
            trip.action_send_email_update_manual()
            self.assertEqual(len(trip.email_dispatch_ids), 1)
            self.assertEqual(trip.email_dispatch_ids[0].dispatch_type, "manual")
            # Schedule timer was advanced to now + 3 hours
            self.assertEqual(trip.email_next_send_at, now + timedelta(hours=3.0))

            # 2. Cron runs immediately at the exact same wall-clock timestamp
            self.env["taj.transport.trip"]._cron_send_due_trip_email_updates()
            # No duplicate dispatch created; total remains 1
            self.assertEqual(len(trip.email_dispatch_ids), 1)
