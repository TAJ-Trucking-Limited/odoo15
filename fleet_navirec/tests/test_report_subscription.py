from datetime import datetime, timedelta
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
        cls.service_product = cls.env["product.product"].create(
            {
                "name": "Tracking report transport service",
                "type": "service",
                "list_price": 100,
            }
        )
        cls.truck_category = cls.env.ref(
            "taj_transport_operations.fleet_category_truck"
        )
        cls.trailer_category = cls.env.ref(
            "taj_transport_operations.fleet_category_trailer"
        )

    def _create_subscription(self, **overrides):
        active = overrides.pop("active", False)
        vals = {
            "name": "Client One Tracking",
            "partner_id": self.client.id,
            "email_to": "ops@example.com",
            "timezone": "Africa/Dar_es_Salaam",
            "send_time_ids": [
                Command.create({"time": "08:00"}),
                Command.create({"time": "13:00"}),
                Command.create({"time": "18:00"}),
            ],
            "vehicle_ids": [Command.set([self.vehicle_a.id])],
        }
        vals.update(overrides)
        subscription = self.env["fleet.navirec.report.subscription"].create(vals)
        if active:
            subscription.active = True
        return subscription

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

    def test_select_all_excludes_trailers_without_devices(self):
        category = self.env.ref(
            "taj_transport_operations.fleet_category_trailer", raise_if_not_found=False
        )
        category = (
            category
            or self.env["fleet.vehicle.model.category"].search(
                [
                    ("name", "=ilike", "Trailer"),
                ],
                limit=1,
            )
            or self.env["fleet.vehicle.model.category"].create({"name": "Trailer"})
        )
        trailer = self.env["fleet.vehicle"].create(
            {
                "model_id": self.model.id,
                "category_id": category.id,
                "license_plate": "REPORT-TRAILER",
            }
        )
        subscription = self._create_subscription()
        subscription.action_select_all_vehicles()
        self.assertIn(self.vehicle_a, subscription.vehicle_ids)
        self.assertNotIn(trailer, subscription.vehicle_ids)
        trailer.navirec_tracking_required = True
        subscription.action_select_all_vehicles()
        self.assertIn(trailer, subscription.vehicle_ids)
        trailer.navirec_tracking_required = False
        trailer.navirec_uuid = "trailer-device"
        subscription.action_select_all_vehicles()
        self.assertIn(trailer, subscription.vehicle_ids)

    def test_active_subscription_requires_vehicle(self):
        subscription = self._create_subscription(vehicle_ids=[Command.clear()])

        with self.assertRaises(ValidationError):
            subscription.active = True

    def test_duplicate_send_times_are_rejected(self):
        with self.assertRaises(ValidationError):
            self._create_subscription(
                send_time_ids=[
                    Command.create({"time": "08:00"}),
                    Command.create({"time": "8:00"}),
                ],
            )

    def test_active_subscription_requires_send_time(self):
        subscription = self._create_subscription(active=True)

        with self.assertRaises(ValidationError):
            subscription.write({"send_time_ids": [Command.clear()]})

    def test_any_number_of_send_times_is_supported(self):
        subscription = self._create_subscription(
            send_time_ids=[
                Command.create({"time": "06:00"}),
                Command.create({"time": "10:00"}),
                Command.create({"time": "14:00"}),
                Command.create({"time": "20:00"}),
            ],
            active=True,
        )

        next_send = subscription._get_next_send_at(datetime(2026, 9, 25, 12, 0, 0))

        self.assertEqual(next_send, datetime(2026, 9, 25, 17, 0, 0))

    def test_editing_send_time_refreshes_next_send_at(self):
        subscription = self._create_subscription(
            send_time_ids=[Command.create({"time": "08:00"})],
            active=True,
        )
        next_before = subscription.next_send_at

        subscription.send_time_ids.write({"time": "23:00"})
        subscription.invalidate_recordset()

        self.assertNotEqual(subscription.next_send_at, next_before)

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

        with (
            patch.object(type(self.env["mail.mail"]), "send", new=fake_send),
            patch.object(
                type(self.env["fleet.vehicle"]), "_cron_navirec_sync_states"
            ) as refresh,
        ):
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

    # ------------------------------------------------------------------
    # Current trip resources in tracking reports
    # ------------------------------------------------------------------
    def _create_truck(self, plate, driver=None, default_trailer=None, **vals):
        values = {
            "model_id": self.model.id,
            "license_plate": plate,
            "category_id": self.truck_category.id,
            **vals,
        }
        if driver:
            values["driver_id"] = driver.id
        if default_trailer:
            values["transport_default_trailer_id"] = default_trailer.id
        return self.env["fleet.vehicle"].create(values)

    def _create_trailer(self, plate, **vals):
        return self.env["fleet.vehicle"].create(
            {
                "model_id": self.model.id,
                "license_plate": plate,
                "category_id": self.trailer_category.id,
                **vals,
            }
        )

    def _started_trip(self, customer, truck, driver, trailer=None, company=None):
        models = {
            name: self.env[name]
            for name in (
                "sale.order",
                "sale.order.line",
                "taj.transport.trip",
                "taj.transport.assignment",
            )
        }
        if company:
            models = {
                name: model.with_company(company) for name, model in models.items()
            }
        order = models["sale.order"].create(
            {
                "partner_id": customer.id,
                "company_id": (company or self.env.company).id,
            }
        )
        line = models["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.service_product.id,
                "name": "Tracking report freight",
                "product_uom_qty": 1,
                "price_unit": 100,
            }
        )
        order.action_confirm()
        trip = models["taj.transport.trip"].create({"sale_line_id": line.id})
        now = fields.Datetime.now()
        trip.write(
            {
                "origin_location": "Dar es Salaam",
                "destination_location": "Lusaka",
                "planned_departure_at": now + timedelta(hours=2),
                "planned_arrival_at": now + timedelta(days=2),
            }
        )
        models["taj.transport.assignment"].create(
            {
                "trip_id": trip.id,
                "vehicle_id": truck.id,
                "driver_id": driver.id,
                "trailer_id": trailer.id if trailer else False,
                "effective_from": now - timedelta(hours=4),
            }
        )
        trip.action_mark_ready()
        trip.write({"actual_departure_at": now - timedelta(hours=1)})
        trip.action_start_trip()
        return trip

    def _row_for(self, subscription, vehicle):
        return next(
            row
            for row in subscription._prepare_report_rows()
            if row["license_plate"] == vehicle.license_plate
        )

    def test_license_plate_column_is_labelled_truck_number(self):
        subscription = self._create_subscription()

        self.assertEqual(
            self.env["fleet.navirec.report.subscription"]
            ._fields["field_license_plate"]
            .string,
            "Truck Number",
        )
        self.assertIn(("license_plate", "Truck Number"), subscription._report_columns())
        html = subscription._render_report_html()
        self.assertIn(">Truck Number<", html)
        self.assertNotIn(">License Plate<", html)

        with (
            self.env.cr.savepoint(),
            self.assertRaisesRegex(ValidationError, "Truck Number"),
        ):
            subscription.write({"field_vehicle": False, "field_license_plate": False})

    def test_resource_columns_default_enabled_and_configurable(self):
        subscription = self._create_subscription()
        for field_name in (
            "field_driver_name",
            "field_driver_number",
            "field_trailer",
            "field_trailer_number",
        ):
            with self.subTest(field=field_name):
                self.assertTrue(subscription[field_name])

        columns = dict(subscription._report_columns())
        self.assertEqual(columns["driver_name"], "Driver Name")
        self.assertEqual(columns["driver_number"], "Driver Number")
        self.assertEqual(columns["trailer"], "Trailer")
        self.assertEqual(columns["trailer_number"], "Trailer Number")

        subscription.write({"field_driver_name": False, "field_trailer_number": False})
        columns = dict(subscription._report_columns())
        self.assertNotIn("driver_name", columns)
        self.assertNotIn("trailer_number", columns)
        self.assertIn("driver_number", columns)
        self.assertIn("trailer", columns)

    def test_current_assignment_resources_not_fleet_defaults(self):
        fleet_driver = self.env["res.partner"].create(
            {
                "name": "Fleet Default Driver",
                "phone": "+255700000001",
            }
        )
        current_driver = self.env["res.partner"].create(
            {
                "name": "Current Trip Driver",
                "phone": "+255700000002",
            }
        )
        fleet_trailer = self._create_trailer("DEFAULT-TRAILER")
        current_trailer = self._create_trailer("CURRENT-TRAILER")
        truck = self._create_truck(
            "RESOURCE-TRUCK",
            driver=fleet_driver,
            default_trailer=fleet_trailer,
        )
        self._started_trip(self.client, truck, current_driver, trailer=current_trailer)
        subscription = self._create_subscription(vehicle_ids=[Command.set([truck.id])])

        row = self._row_for(subscription, truck)

        self.assertEqual(row["driver_name"], "Current Trip Driver")
        self.assertEqual(row["driver_number"], "+255700000002")
        self.assertEqual(row["trailer"], current_trailer.display_name)
        self.assertEqual(row["trailer_number"], "CURRENT-TRAILER")
        self.assertNotIn("Fleet Default Driver", row.values())
        self.assertNotIn("DEFAULT-TRAILER", row.values())

    def test_resources_populate_when_old_trip_columns_are_off(self):
        driver = self.env["res.partner"].create(
            {"name": "Scoped Driver", "phone": "+255700000003"}
        )
        trailer = self._create_trailer("SCOPED-TRAILER")
        truck = self._create_truck("SCOPED-TRUCK")
        self._started_trip(self.client, truck, driver, trailer=trailer)
        subscription = self._create_subscription(
            vehicle_ids=[Command.set([truck.id])],
            field_trip=False,
            field_customer=False,
            field_sale_order=False,
        )

        row = self._row_for(subscription, truck)
        html = subscription._render_report_html()

        self.assertEqual(row["driver_name"], "Scoped Driver")
        self.assertEqual(row["driver_number"], "+255700000003")
        self.assertEqual(row["trailer_number"], "SCOPED-TRAILER")
        self.assertIn(">Driver Name<", html)
        self.assertIn("Scoped Driver", html)
        self.assertIn("SCOPED-TRAILER", html)

    def test_other_client_trip_discloses_no_resources(self):
        other_client = self.env["res.partner"].create({"name": "Other Client"})
        other_driver = self.env["res.partner"].create(
            {"name": "Other Client Driver", "phone": "+255700000004"}
        )
        other_trailer = self._create_trailer("OTHER-TRAILER")
        truck = self._create_truck("NO-MATCH-TRUCK")
        self._started_trip(other_client, truck, other_driver, trailer=other_trailer)
        subscription = self._create_subscription(vehicle_ids=[Command.set([truck.id])])

        row = self._row_for(subscription, truck)
        html = subscription._render_report_html()

        for key in (
            "trip",
            "customer",
            "sale_order",
            "driver_name",
            "driver_number",
            "trailer",
            "trailer_number",
        ):
            self.assertEqual(row.get(key, ""), "")
        self.assertNotIn("Other Client Driver", html)
        self.assertNotIn("Other Client", html)
        self.assertNotIn("OTHER-TRAILER", html)

    def test_disallowed_company_trip_discloses_no_resources(self):
        other_company = self.env["res.company"].create(
            {"name": "Disallowed Tracking Company"}
        )
        other_client = self.env["res.partner"].create(
            {"name": "Disallowed Company Client", "company_id": other_company.id}
        )
        other_driver = self.env["res.partner"].create(
            {
                "name": "Disallowed Company Driver",
                "phone": "+255700000005",
                "company_id": other_company.id,
            }
        )
        truck = self._create_truck("DISALLOWED-TRUCK", company_id=other_company.id)
        trailer = self._create_trailer(
            "DISALLOWED-TRAILER", company_id=other_company.id
        )
        self._started_trip(
            other_client,
            truck,
            other_driver,
            trailer=trailer,
            company=other_company,
        )
        subscription = self._create_subscription(
            vehicle_ids=[Command.set([truck.id])],
            trip_details_scope="fleet",
        )

        hidden = subscription.with_context(allowed_company_ids=[self.env.company.id])
        row = self._row_for(hidden, truck)
        html = hidden._render_report_html()

        self.assertEqual(row.get("driver_name", ""), "")
        self.assertEqual(row.get("driver_number", ""), "")
        self.assertEqual(row.get("trailer_number", ""), "")
        self.assertNotIn("Disallowed Company Driver", html)
        self.assertNotIn("DISALLOWED-TRAILER", html)

        # Positive control: the same fleet-scoped subscription sees the
        # assignment once its company is explicitly allowed.
        allowed = subscription.with_context(allowed_company_ids=[other_company.id])
        allowed_row = self._row_for(allowed, truck)

        self.assertEqual(allowed_row.get("driver_number"), "+255700000005")
        self.assertEqual(allowed_row.get("trailer_number"), "DISALLOWED-TRAILER")

    def test_multiple_scoped_trips_blank_all_trip_resource_columns(self):
        other_client = self.env["res.partner"].create({"name": "Hidden Client"})
        driver_one = self.env["res.partner"].create(
            {"name": "First Driver", "phone": "+255700000006"}
        )
        driver_two = self.env["res.partner"].create(
            {"name": "Hidden Driver", "phone": "+255700000007"}
        )
        trailer_one = self._create_trailer("FIRST-TRAILER")
        trailer_two = self._create_trailer("HIDDEN-TRAILER")
        truck = self._create_truck("AMBIGUOUS-TRUCK")
        other_truck = self._create_truck("AMBIGUOUS-OTHER-TRUCK")
        self._started_trip(self.client, truck, driver_one, trailer=trailer_one)
        second_trip = self._started_trip(
            other_client, other_truck, driver_two, trailer=trailer_two
        )
        # Normal lifecycle rejects reusing an active truck. Simulate legacy
        # corrupt data in this test only so the report's ambiguity guard is
        # exercised without weakening the transport resource constraints.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE taj_transport_assignment SET vehicle_id = %s WHERE trip_id = %s",
            (truck.id, second_trip.id),
        )
        self.env["taj.transport.assignment"].invalidate_model(["vehicle_id"])
        self.env["taj.transport.trip"].invalidate_model(["current_assignment_id"])
        subscription = self._create_subscription(vehicle_ids=[Command.set([truck.id])])

        row = self._row_for(subscription, truck)
        html = subscription._render_report_html()

        for key in (
            "trip",
            "customer",
            "sale_order",
            "driver_name",
            "driver_number",
            "trailer",
            "trailer_number",
        ):
            self.assertEqual(row[key], "Multiple active trips")
        self.assertNotIn("Hidden Driver", html)
        self.assertNotIn("HIDDEN-TRAILER", html)
        self.assertNotIn("Hidden Client", html)

    def test_html_escapes_and_xlsx_exports_resource_columns(self):
        driver = self.env["res.partner"].create(
            {
                "name": "Driver <Unsafe> & Co",
                "phone": "+255700000008",
            }
        )
        trailer = self._create_trailer("EXCEL-TRAILER")
        truck = self._create_truck("EXCEL-TRUCK")
        self._started_trip(self.client, truck, driver, trailer=trailer)
        subscription = self._create_subscription(vehicle_ids=[Command.set([truck.id])])

        html = subscription._render_report_html()
        _sheet_xml, shared_xml = self._xlsx_parts(subscription)
        shared_root = ElementTree.fromstring(shared_xml)
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared_values = [
            "".join(node.text or "" for node in item.findall(".//s:t", ns))
            for item in shared_root.findall("s:si", ns)
        ]

        self.assertIn("Driver &lt;Unsafe&gt; &amp; Co", html)
        self.assertNotIn("Driver <Unsafe> & Co", html)
        for value in (
            "Driver Name",
            "Driver Number",
            "Trailer",
            "Trailer Number",
            "Driver <Unsafe> & Co",
            "+255700000008",
            "EXCEL-TRAILER",
        ):
            self.assertIn(value, shared_values)
