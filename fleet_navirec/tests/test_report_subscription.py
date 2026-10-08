from datetime import datetime
from unittest.mock import patch
import base64
import io
import re
import zipfile
from xml.etree import ElementTree

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestNavirecReportSubscription(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env["fleet.vehicle.model.brand"].create(
            {
                "name": "Tracking Test Brand",
            }
        )
        cls.model = cls.env["fleet.vehicle.model"].create(
            {
                "name": "Tracking Test Model",
                "brand_id": cls.brand.id,
                "vehicle_type": "car",
            }
        )
        cls.vehicle_a = cls.env["fleet.vehicle"].create(
            {
                "model_id": cls.model.id,
                "license_plate": "T000-AAA",
            }
        )
        cls.vehicle_b = cls.env["fleet.vehicle"].create(
            {
                "model_id": cls.model.id,
                "license_plate": "T000-BBB",
            }
        )
        cls.client = cls.env["res.partner"].create(
            {
                "name": "Client & One",
                "email": "client@example.com",
            }
        )

    def _create_subscription(self, **overrides):
        vals = {
            "name": "Client One Tracking",
            "partner_id": self.client.id,
            "email_to": "ops@example.com",
            "timezone": "Africa/Dar_es_Salaam",
            "send_time_1": "08:00",
            "send_time_2": "13:00",
            "send_time_3": "18:00",
            "vehicle_ids": [Command.set([self.vehicle_a.id])],
        }
        vals.update(overrides)
        return self.env["fleet.navirec.report.subscription"].create(vals)

    def test_next_send_uses_client_timezone(self):
        subscription = self._create_subscription()
        subscription.active = True

        next_send = subscription._get_next_send_at(datetime(2026, 9, 25, 9, 30, 0))

        self.assertEqual(
            next_send,
            datetime(2026, 9, 25, 10, 0, 0),
        )

    def test_after_last_slot_rolls_to_next_day(self):
        subscription = self._create_subscription()
        subscription.active = True

        next_send = subscription._get_next_send_at(datetime(2026, 9, 25, 16, 0, 0))

        self.assertEqual(
            next_send,
            datetime(2026, 9, 26, 5, 0, 0),
        )

    def test_active_subscription_requires_vehicle(self):
        subscription = self._create_subscription(vehicle_ids=[Command.clear()])

        with self.assertRaises(ValidationError):
            subscription.active = True

    def test_three_send_times_must_be_unique(self):
        with self.assertRaises(ValidationError):
            self._create_subscription(
                send_time_2="08:00",
            )

    def test_report_only_contains_subscription_vehicles(self):
        self.vehicle_a._write_navirec_state(
            {
                "time": "2026-09-25T10:00:00Z",
                "location": {"coordinates": [39.2, -6.8]},
                "speed": 50.0,
                "ignition": True,
                "total_distance": 100000.0,
            }
        )
        self.vehicle_b._write_navirec_state(
            {
                "time": "2026-09-25T10:00:00Z",
                "location": {"coordinates": [40.0, -7.0]},
                "speed": 10.0,
                "ignition": True,
                "total_distance": 200000.0,
            }
        )
        subscription = self._create_subscription()

        html = subscription._render_report_html()

        self.assertIn("T000-AAA", html)
        self.assertNotIn("T000-BBB", html)
        self.assertIn("Client &amp; One", html)

    def test_configurable_columns_are_respected(self):
        subscription = self._create_subscription(
            field_speed=False,
            field_odometer=False,
            field_coordinates=True,
        )

        html = subscription._render_report_html()

        self.assertNotIn(">Speed<", html)
        self.assertNotIn(">Odometer<", html)
        self.assertIn(">Coordinates<", html)

    def test_missing_telemetry_is_rendered_as_not_available(self):
        subscription = self._create_subscription()

        html = subscription._render_report_html()

        self.assertIn("Not available", html)

    def test_failed_scheduled_delivery_is_logged_and_retried(self):
        subscription = self._create_subscription(active=True)
        due_at = fields.Datetime.now()
        subscription.with_context(fleet_navirec_skip_schedule_refresh=True).write(
            {"next_send_at": due_at}
        )

        model_type = type(subscription)
        with patch.object(
            model_type,
            "_send_tracking_email",
            side_effect=RuntimeError("SMTP unavailable"),
        ):
            self.env["fleet.navirec.report.subscription"]._cron_send_due_reports()

        subscription.invalidate_recordset()
        self.assertIn("SMTP unavailable", subscription.last_error)
        self.assertTrue(subscription.last_error_at)
        self.assertFalse(subscription.last_sent_at)
        self.assertGreater(subscription.next_send_at, due_at)

    def test_successful_scheduled_delivery_advances_schedule(self):
        subscription = self._create_subscription(active=True)
        due_at = fields.Datetime.now()
        subscription.with_context(fleet_navirec_skip_schedule_refresh=True).write(
            {"next_send_at": due_at}
        )

        model_type = type(subscription)
        with patch.object(
            model_type,
            "_send_tracking_email",
            return_value=True,
        ):
            self.env["fleet.navirec.report.subscription"]._cron_send_due_reports()

        subscription.invalidate_recordset()
        self.assertTrue(subscription.last_sent_at)
        self.assertFalse(subscription.last_error)
        self.assertGreater(subscription.next_send_at, due_at)

    def test_preview_action_uses_preview_wizard(self):
        subscription = self._create_subscription()

        action = subscription.action_preview_report()

        self.assertEqual(
            action["res_model"],
            "fleet.navirec.report.preview",
        )
        preview = self.env["fleet.navirec.report.preview"].browse(action["res_id"])
        self.assertEqual(preview.subscription_id, subscription)
        self.assertIn("Fleet Position Report", preview.body_html)

    def test_safety_columns_cannot_be_disabled(self):
        status_subscription = self._create_subscription()
        with self.env.cr.savepoint(), self.assertRaises(ValidationError):
            status_subscription.field_status = False

        gps_subscription = self._create_subscription(name="Second Safety Subscription")
        with self.env.cr.savepoint(), self.assertRaises(ValidationError):
            gps_subscription.field_last_gps = False

    def test_report_requires_identity_and_position(self):
        identity_subscription = self._create_subscription()
        with self.env.cr.savepoint(), self.assertRaises(ValidationError):
            identity_subscription.write(
                {
                    "field_vehicle": False,
                    "field_license_plate": False,
                }
            )

        position_subscription = self._create_subscription(
            name="Position Safety Subscription"
        )
        with self.env.cr.savepoint(), self.assertRaises(ValidationError):
            position_subscription.write(
                {
                    "field_location": False,
                    "field_coordinates": False,
                }
            )

    def test_manual_delivery_failure_is_persisted(self):
        subscription = self._create_subscription()
        model_type = type(subscription)
        with patch.object(
            model_type,
            "_send_tracking_email",
            side_effect=RuntimeError("Mail server unavailable"),
        ):
            action = subscription.action_send_now()

        subscription.invalidate_recordset()
        self.assertEqual(
            action["params"]["type"],
            "danger",
        )
        self.assertIn("Mail server unavailable", subscription.last_error)
        self.assertTrue(subscription.last_error_at)
        self.assertFalse(subscription.last_sent_at)

    def test_mail_send_requires_exception_mode_and_sent_state(self):
        subscription = self._create_subscription()
        self.env.company.partner_id.email = "sender@example.com"
        mail_type = type(self.env["mail.mail"])
        captured = {}

        def fake_send(mail, **kwargs):
            captured.update(kwargs)
            mail.write({"state": "sent"})
            return True

        with patch.object(mail_type, "send", new=fake_send):
            mail = subscription._send_tracking_email()

        self.assertTrue(captured.get("raise_exception"))
        self.assertEqual(mail.state, "sent")

    def test_unconfirmed_mail_state_is_treated_as_failure(self):
        subscription = self._create_subscription()
        self.env.company.partner_id.email = "sender@example.com"
        mail_type = type(self.env["mail.mail"])

        def fake_send(mail, **kwargs):
            mail.write({"state": "exception"})
            return True

        with patch.object(mail_type, "send", new=fake_send):
            with self.assertRaisesRegex(
                UserError,
                "did not confirm email delivery",
            ):
                subscription._send_tracking_email()

    def _xlsx_parts(self, subscription):
        content, extension = self.env["ir.actions.report"]._render_xlsx(
            self.env.ref("fleet_navirec.report_fleet_position_xlsx"),
            [subscription.id],
            {},
        )
        self.assertEqual(extension, "xlsx")
        archive = zipfile.ZipFile(io.BytesIO(content))
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode()
        shared_xml = archive.read("xl/sharedStrings.xml").decode()
        return sheet_xml, shared_xml

    def test_tracking_email_attaches_xlsx_and_keeps_html_body(self):
        subscription = self._create_subscription()
        self.env.company.partner_id.email = "sender@example.com"
        mail_type = type(self.env["mail.mail"])
        mails = []

        def fake_send(mail, **kwargs):
            mail.write({"state": "sent"})
            mails.append(mail)
            return True

        with patch.object(mail_type, "send", new=fake_send):
            action = subscription.action_send_now()

        self.assertEqual(action["params"]["type"], "success")
        self.assertTrue(subscription.last_sent_at)
        self.assertEqual(len(mails), 1)
        mail = mails[0]
        self.assertIn("<table", mail.body_html)
        self.assertEqual(len(mail.attachment_ids), 1)
        attachment = mail.attachment_ids[:1]
        self.assertEqual(
            attachment.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(attachment.res_model, subscription._name)
        self.assertEqual(attachment.res_id, subscription.id)
        self.assertRegex(
            attachment.name,
            r"^Fleet_Position_Client_One_\d{8}_\d{4}\.xlsx$",
        )
        raw = base64.b64decode(attachment.datas)
        self.assertTrue(raw.startswith(b"PK"))

    def test_xlsx_cells_are_text_and_true_zero_preserved(self):
        self.vehicle_a._write_navirec_state(
            {
                "time": "2026-09-25T10:00:00Z",
                "location": {"coordinates": [39.2, -6.8]},
                "speed": 0.0,
                "ignition": True,
                "total_distance": 0.0,
            },
            location_name="=1+1",
        )
        subscription = self._create_subscription()

        sheet_xml, shared_xml = self._xlsx_parts(subscription)

        # No formula cells: every value must be stored as text.
        self.assertNotIn("<f>", sheet_xml)
        self.assertNotIn("<f ", sheet_xml)
        self.assertIn("=1+1", shared_xml)
        self.assertIn("0.0 km/h", shared_xml)
        self.assertIn("0.0 km", shared_xml)

    def test_xlsx_accepts_subscription_names_invalid_for_excel(self):
        for name in ("Client / Morning", "Fleet: Syria", "'[Fleet]*?\\Night'"):
            with self.subTest(name=name):
                subscription = self._create_subscription(name=name)
                _sheet_xml, shared_xml = self._xlsx_parts(subscription)
                self.assertIn("T000-AAA", shared_xml)

    def test_xlsx_freezes_header_and_sizes_columns(self):
        subscription = self._create_subscription()
        location = "Nakonde Border Post, Zambia"
        self.vehicle_a.write({"navirec_location_name": location})
        sheet_xml, _shared_xml = self._xlsx_parts(subscription)
        root = ElementTree.fromstring(sheet_xml)
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        pane = root.find("s:sheetViews/s:sheetView/s:pane", ns)
        self.assertIsNotNone(pane)
        self.assertEqual(pane.get("state"), "frozen")
        self.assertEqual(pane.get("ySplit"), "1")
        position_column = [key for key, _label in subscription._report_columns()].index(
            "location"
        ) + 1
        widths = root.findall("s:cols/s:col", ns)
        self.assertTrue(
            any(
                int(column.get("min")) <= position_column <= int(column.get("max"))
                and float(column.get("width")) >= len(location)
                for column in widths
            )
        )

    def test_xlsx_only_contains_subscription_vehicles(self):
        client_two = self.env["res.partner"].create({"name": "Client Two"})
        subscription_one = self._create_subscription()
        subscription_two = self._create_subscription(
            partner_id=client_two.id,
            vehicle_ids=[Command.set([self.vehicle_b.id])],
        )
        for subscription, included, excluded in (
            (subscription_one, "T000-AAA", "T000-BBB"),
            (subscription_two, "T000-BBB", "T000-AAA"),
        ):
            with self.subTest(subscription=subscription.name):
                _sheet_xml, shared_xml = self._xlsx_parts(subscription)
                self.assertIn(included, shared_xml)
                self.assertNotIn(excluded, shared_xml)

    def test_xlsx_missing_telemetry_is_not_zero(self):
        subscription = self._create_subscription()
        _sheet_xml, shared_xml = self._xlsx_parts(subscription)
        self.assertIn("Not available", shared_xml)
        self.assertNotIn("0.0 km/h", shared_xml)
        self.assertNotIn(">0.0 km<", shared_xml)

    def test_empty_vehicle_selection_sends_no_mail_or_attachment(self):
        subscription = self._create_subscription(vehicle_ids=[Command.clear()])
        mail_count = self.env["mail.mail"].search_count([])
        attachment_count = self.env["ir.attachment"].search_count([])
        with self.assertRaisesRegex(UserError, "Add at least one tracked vehicle"):
            subscription._send_tracking_email()
        self.assertEqual(self.env["mail.mail"].search_count([]), mail_count)
        self.assertEqual(self.env["ir.attachment"].search_count([]), attachment_count)

    def test_successful_scheduled_delivery_attaches_xlsx(self):
        subscription = self._create_subscription(active=True)
        self.env.company.partner_id.email = "sender@example.com"
        due_at = fields.Datetime.now()
        subscription.with_context(fleet_navirec_skip_schedule_refresh=True).write(
            {"next_send_at": due_at}
        )
        mails = []

        def fake_send(mail, **kwargs):
            self.assertTrue(kwargs.get("raise_exception"))
            mail.write({"state": "sent"})
            mails.append(mail)
            return True

        with patch.object(type(self.env["mail.mail"]), "send", new=fake_send), patch.object(
            type(self.env["fleet.vehicle"]), "_cron_navirec_sync_states"
        ) as refresh:
            self.env["fleet.navirec.report.subscription"]._cron_send_due_reports()

        refresh.assert_called_once()
        self.assertEqual(len(mails), 1)
        self.assertEqual(len(mails[0].attachment_ids), 1)
        attachment = mails[0].attachment_ids
        self.assertEqual(attachment.res_id, subscription.id)
        self.assertEqual(
            attachment.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(attachment.datas))) as archive:
            strings = archive.read("xl/sharedStrings.xml").decode()
            self.assertIn("T000-AAA", strings)
            self.assertNotIn("T000-BBB", strings)
        self.assertTrue(subscription.last_sent_at)
        self.assertFalse(subscription.last_error)
        self.assertGreater(subscription.next_send_at, due_at)

    def test_xlsx_respects_columns_and_mandatory_freshness(self):
        subscription = self._create_subscription(
            field_speed=False,
            field_odometer=False,
            field_coordinates=True,
        )

        _sheet_xml, shared_xml = self._xlsx_parts(subscription)

        self.assertIn("Coordinates", shared_xml)
        self.assertIn("Navirec Status", shared_xml)
        self.assertIn("Last GPS", shared_xml)
        self.assertNotIn(">Speed<", shared_xml)
        self.assertNotIn(">Odometer<", shared_xml)

    def test_scheduled_xlsx_failure_sends_no_mail_and_retries(self):
        subscription = self._create_subscription(active=True)
        due_at = fields.Datetime.now()
        subscription.with_context(fleet_navirec_skip_schedule_refresh=True).write(
            {"next_send_at": due_at}
        )
        mail_count = self.env["mail.mail"].search_count([])

        model_type = type(subscription)
        with patch.object(
            model_type,
            "_render_tracking_xlsx",
            side_effect=RuntimeError("XLSX renderer unavailable"),
        ):
            self.env["fleet.navirec.report.subscription"]._cron_send_due_reports()

        subscription.invalidate_recordset()
        self.assertIn("XLSX renderer unavailable", subscription.last_error)
        self.assertTrue(subscription.last_error_at)
        self.assertFalse(subscription.last_sent_at)
        self.assertGreater(subscription.next_send_at, due_at)
        self.assertEqual(self.env["mail.mail"].search_count([]), mail_count)

    def test_manual_xlsx_failure_records_error_without_retry(self):
        subscription = self._create_subscription()
        next_send_at = subscription.next_send_at
        mail_count = self.env["mail.mail"].search_count([])

        model_type = type(subscription)
        with patch.object(
            model_type,
            "_render_tracking_xlsx",
            side_effect=RuntimeError("XLSX renderer unavailable"),
        ):
            action = subscription.action_send_now()

        subscription.invalidate_recordset()
        self.assertEqual(action["params"]["type"], "danger")
        self.assertIn("XLSX renderer unavailable", subscription.last_error)
        self.assertTrue(subscription.last_error_at)
        self.assertFalse(subscription.last_sent_at)
        self.assertEqual(subscription.next_send_at, next_send_at)
        self.assertEqual(self.env["mail.mail"].search_count([]), mail_count)

    def test_preview_download_xlsx_returns_report_action(self):
        subscription = self._create_subscription()
        action = subscription.action_preview_report()
        preview = self.env["fleet.navirec.report.preview"].browse(action["res_id"])

        download = preview.action_download_xlsx()

        self.assertEqual(download["type"], "ir.actions.report")
        self.assertEqual(download["report_name"], "fleet_navirec.fleet_position_xlsx")
