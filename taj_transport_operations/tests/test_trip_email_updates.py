"""Customer trip report delivery and scheduling."""

from base64 import b64decode
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from freezegun import freeze_time
from lxml import html

from odoo.addons.http_routing.tests.common import MockRequest
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from ..models.transport_trip_email import REPORT_XLSX_MIMETYPE
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

    def test_milestone_comparison_groups_arrivals_and_signed_differences_by_trip(self):
        specs = self._default_checkpoints()
        first = self._started_trip(
            route=self._route("Comparison route", specs), line=self._line_for(self.customer),
        )
        other_route = self._route("Other comparison route", [
            dict(specs[1], name="kasumbalesa border"), dict(specs[2], name="Delivery"),
        ])
        second = self._started_trip(route=other_route, line=self._line_for(self.customer))
        loading, border = first.milestone_ids.sorted("sequence")[:2]
        late = loading.planned_arrival_at + timedelta(hours=1.5)
        early = border.planned_arrival_at - timedelta(hours=2)
        with freeze_time(early):
            loading._record_arrival(late)
            border._record_arrival(early)
        second_border = second.milestone_ids.sorted("sequence")[0]
        with freeze_time(second_border.planned_arrival_at):
            second_border._record_arrival(second_border.planned_arrival_at)

        content = (first | second)._render_customer_trip_report("Africa/Dar_es_Salaam")
        with ZipFile(BytesIO(content)) as workbook:
            self.assertIn(b'name="Milestone Comparison"', workbook.read("xl/workbook.xml"))
            sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet3.xml"))
            strings = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            styles = workbook.read("xl/styles.xml")
        cells = {}
        for cell in sheet.findall(".//{*}c"):
            value = cell.findtext("{*}v")
            if value is not None:
                cells[cell.attrib["r"]] = (
                    "".join(strings[int(value)].itertext()) if cell.get("t") == "s" else float(value)
                )

        self.assertIn(b"+0.00;-0.00;0.00", styles)
        self.assertEqual([cells[key] for key in ("C1", "F1", "I1", "L1")], [
            "Loading", "Kasumbalesa border", "Offloading", "Delivery",
        ])
        self.assertEqual([cells[key] for key in ("C2", "D2", "E2")], [
            "Planned Arrival", "Actual Arrival", "Difference (hours)",
        ])
        self.assertEqual(
            (cells["A3"], cells["B3"], cells["A4"], cells["B4"]),
            (self.truck.license_plate, first.name, self.truck.license_plate, second.name),
        )
        epoch = datetime(1899, 12, 30)
        day = timedelta(days=1)
        offset = timedelta(hours=3)
        self.assertAlmostEqual(cells["C3"], (loading.planned_arrival_at + offset - epoch) / day)
        self.assertAlmostEqual(cells["D3"], (late + offset - epoch) / day)
        self.assertEqual((cells["E3"], cells["H3"], cells["H4"]), (1.5, -2.0, 0.0))
        self.assertIn("I3", cells)
        self.assertFalse({"J3", "K3", "L3", "C4", "M4", "N4"}.intersection(cells))

    def test_preview_downloads_the_customer_report_without_queueing_mail(self):
        self.customer.write({"transport_report_additional_emails": "tracking@example.com"})
        trip = self._started_trip(
            route=self._route("Report preview route"), line=self._line_for(self.customer),
        )
        other = self._started_trip()
        loading = trip.milestone_ids.sorted("sequence")[0]
        arrival = loading.planned_arrival_at + timedelta(hours=1.5)
        with freeze_time(arrival):
            loading._record_arrival(arrival)

        map_url = "https://www.google.com/maps?q=-6.7924,39.2083"
        position = {"label": "Route Mulumbu, Fungurume, Democratic Republic of the Congo", "map_url": map_url}
        with patch.object(type(trip), "_get_email_update_position_data", return_value=position):
            action = trip.with_user(self.dispatcher).action_preview_customer_trip_report()
        self.assertEqual(action["target"], "new")
        preview = self.env[action["res_model"]].with_user(self.dispatcher).browse(action["res_id"])
        self.assertEqual(preview.customer_id, self.customer)
        self.assertFalse(preview.email_to)
        self.assertEqual(preview.email_cc, "tracking@example.com")
        self.assertIn(trip.name, preview.trips_html)
        self.assertNotIn(other.name, preview.trips_html)
        self.assertIn("Kasumbalesa border", preview.checkpoints_html)
        self.assertIn("+1.50", preview.comparison_html)
        for report_html in (preview.trips_html, preview.checkpoints_html, preview.comparison_html):
            table = html.fromstring(report_html).xpath("//div[contains(@class, 'o_taj_report_preview')]/table")
            self.assertEqual(len(table), 1)
            self.assertNotIn("text-nowrap", table[0].classes)
        map_cell = html.fromstring(preview.trips_html).xpath("//tbody/tr/td[last()]")[0]
        self.assertEqual(map_cell.text_content().strip(), "View on Map")
        self.assertEqual(map_cell.xpath("./a/@href"), [map_url])
        data = b64decode(preview.report_file)
        with ZipFile(BytesIO(data)) as workbook:
            self.assertIn(b'name="Milestone Comparison"', workbook.read("xl/workbook.xml"))
            self.assertIn(b">1.5<", workbook.read("xl/worksheets/sheet3.xml"))
        self.assertEqual(preview.action_download_xlsx()["target"], "download")
        binary = self.env["ir.binary"].with_user(self.dispatcher)
        record = binary._find_record(res_model=preview._name, res_id=preview.id, field="report_file")
        with MockRequest(binary.env):
            stream = binary._get_stream_from(
                record, "report_file", filename_field="filename", mimetype=REPORT_XLSX_MIMETYPE,
            )
        self.assertEqual(stream.data, data)
        self.assertEqual(stream.download_name, preview.filename)
        with self.assertRaises(AccessError):
            binary.with_user(self.manager)._find_record(
                res_model=preview._name, res_id=preview.id, field="report_file",
            )
        self.assertFalse(trip.email_dispatch_ids)
        self.assertFalse(other.email_dispatch_ids)

    def test_milestone_preview_download_contains_only_the_selected_trip(self):
        checkpoints = [
            "Loading at Dar es Salaam", "Tunduma", "Nakonde", "Kasumbalesa Zambia",
            "Kasumbalesa DRC", "Whiskey", "Kanyaka Parking", "Lubumbashi", "Delivery at Lubumbashi",
        ]
        route = self._route("Dar es Salaam to Lubumbashi", [
            {"name": name, "milestone_type_id": self.type_border.id, "planned_offset_hours": index * 6}
            for index, name in enumerate(checkpoints)
        ])
        trip = self._completed_trip(route=route, line=self._line_for(self.customer))
        other = self._started_trip(line=self._line_for(self.customer))

        action = trip.with_user(self.dispatcher).action_preview_milestone_report()
        self.assertEqual(action["target"], "new")
        preview = self.env[action["res_model"]].with_user(self.dispatcher).browse(action["res_id"])
        self.assertTrue(preview.comparison_only)
        self.assertIn(trip.name, preview.comparison_html)
        self.assertIn(self.truck.license_plate, preview.comparison_html)
        self.assertNotIn(other.name, preview.comparison_html)
        self.assertFalse(preview.trips_html)
        self.assertFalse(preview.checkpoints_html)
        table = html.fromstring(preview.comparison_html)
        groups = table.xpath("//thead/tr[1]/th[@colspan='3']")
        self.assertEqual([group.text_content() for group in groups], checkpoints)
        self.assertEqual(len(table.xpath("//thead/tr[2]/th")), 2 + 3 * len(checkpoints))
        self.assertEqual(len(table.xpath("//tbody/tr/td")), 2 + 3 * len(checkpoints))
        with ZipFile(BytesIO(b64decode(preview.report_file))) as workbook:
            self.assertIn(b'name="Milestone Comparison"', workbook.read("xl/workbook.xml"))
            self.assertNotIn("xl/worksheets/sheet2.xml", workbook.namelist())
        self.assertFalse(trip.email_dispatch_ids)

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
