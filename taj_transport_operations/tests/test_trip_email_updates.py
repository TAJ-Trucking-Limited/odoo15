"""Customer trip report delivery and scheduling."""

from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

from freezegun import freeze_time

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .phase3_case import TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestTripEmailUpdates(TajTransportPhase3Case):
    user_tag = "email_updates"

    def _line_for(self, partner):
        order = self.env["sale.order"].create({"partner_id": partner.id})
        line = self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.product.id,
            "product_uom_qty": 1,
            "price_unit": 900,
        })
        order.action_confirm()
        return line

    def _report_mail(self, trip):
        self.assertEqual(len(trip.email_dispatch_ids), 1)
        mail = trip.email_dispatch_ids.mail_ids
        self.assertEqual(len(mail), 1)
        self.assertEqual(len(mail.attachment_ids), 1)
        return mail

    def test_manual_report_contains_only_customers_in_progress_trips(self):
        self.customer.write({"email": "ops@customer.example", "is_company": True})
        child = self.env["res.partner"].create({
            "name": "Branch contact",
            "parent_id": self.customer.id,
            "email": "branch@customer.example",
        })
        first = self._started_trip(
            route=self._route("Email report route"), line=self._line_for(self.customer),
        )
        second = self._started_trip(line=self._line_for(child))
        completed = self._completed_trip(line=self._line_for(self.customer))
        draft = self._trip(line=self._line_for(self.customer))
        cancelled = self._cancel_trip(self._planned_trip(line=self._line_for(self.customer)))
        other = self._started_trip()

        first.with_user(self.dispatcher).action_send_email_update_manual()

        mail = self._report_mail(first)
        self.assertEqual(mail.email_to, "ops@customer.example")
        self.assertEqual(self._report_mail(second), mail)
        self.assertEqual(first.with_user(self.dispatcher).email_dispatch_ids.status, "queued")
        excluded = completed | draft | cancelled | other
        self.assertFalse(excluded.email_dispatch_ids)
        with ZipFile(BytesIO(mail.attachment_ids.raw)) as workbook:
            strings = workbook.read("xl/sharedStrings.xml").decode()
            self.assertIn("xl/worksheets/sheet2.xml", workbook.namelist())
        self.assertIn(first.name, strings)
        self.assertIn(second.name, strings)
        self.assertIn("Kasumbalesa border", strings)
        for trip in excluded:
            self.assertNotIn(f">{trip.name}<", strings)

    def test_partner_recipients_are_validated_and_deduplicated(self):
        self.customer.email = "ops@customer.example"
        with self.assertRaises(ValidationError):
            self.customer.transport_report_additional_emails = "invalid, extra@customer.example"
        with self.assertRaises(ValidationError):
            self.customer.transport_report_additional_emails = "extra@customer.example\r\nother@example.com"
        self.customer.transport_report_additional_emails = (
            " OPS@CUSTOMER.EXAMPLE, extra@customer.example, EXTRA@CUSTOMER.EXAMPLE "
        )
        trip = self._started_trip(line=self._line_for(self.customer))
        trip.action_send_email_update_manual()
        mail = self._report_mail(trip)
        self.assertEqual(mail.email_to, "ops@customer.example")
        self.assertEqual(mail.email_cc, "extra@customer.example")

    def test_manual_report_requires_in_progress_trip_and_primary_email(self):
        draft = self._trip()
        with self.assertRaises(UserError):
            draft.action_send_email_update_manual()
        trip = self._started_trip()
        with self.assertRaises(UserError):
            trip.action_send_email_update_manual()
        self.assertFalse(trip.email_dispatch_ids)

    def test_global_schedule_queues_once_per_customer_and_slot(self):
        self.customer.email = "ops@customer.example"
        first = self._started_trip(line=self._line_for(self.customer))
        second = self._started_trip(line=self._line_for(self.customer))
        no_email = self._started_trip()
        other = self._started_trip()
        other.partner_id.email = "other@example.com"
        cron = self.env["taj.transport.trip"]._cron_send_due_trip_email_updates
        settings = self.env["taj.transport.settings"].with_user(self.manager)

        with freeze_time(datetime(2026, 11, 20, 5, 0)):
            with self.assertRaises(ValidationError):
                settings.create({"customer_report_send_times": "24:00"}).action_save()
            settings.create({
                "customer_report_send_times": "08:30, 16:00",
                "customer_report_timezone": "Africa/Dar_es_Salaam",
            }).action_save()
            cron()
            self.assertFalse((first | second | other).email_dispatch_ids)
        with freeze_time(datetime(2026, 11, 20, 5, 31)):
            cron()
            cron()
            self.assertEqual(self._report_mail(first), self._report_mail(second))
            self.assertNotEqual(self._report_mail(first), self._report_mail(other))
            self.assertFalse(no_email.email_dispatch_ids)
        with freeze_time(datetime(2026, 11, 20, 13, 0)):
            cron()
            self.assertEqual(len(first.email_dispatch_ids), 2)
            self.assertEqual(len(second.email_dispatch_ids), 2)
            self.assertEqual(len(other.email_dispatch_ids), 2)

        settings.create({"customer_report_send_times": False}).action_save()
        with freeze_time(datetime(2026, 11, 21, 5, 30)):
            cron()
            self.assertEqual(len(first.email_dispatch_ids), 2)
