import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from odoo import fields
from odoo.modules.module import get_module_path
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestVehicleSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env["fleet.vehicle.model.brand"].create({
            "name": "Navirec Test Brand",
        })
        cls.model = cls.env["fleet.vehicle.model"].create({
            "name": "Navirec Test Model",
            "brand_id": cls.brand.id,
            "vehicle_type": "car",
        })
        cls.vehicle = cls.env["fleet.vehicle"].create({
            "model_id": cls.model.id,
            "license_plate": "T000-AAA",
        })

    def _fixture(self, name):
        path = (
            Path(get_module_path("fleet_navirec"))
            / "tests"
            / "fixtures"
            / name
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def test_plate_normalization_matches_without_creating_vehicle(self):
        client = Mock()
        client.get_vehicles.return_value = [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "registration": "t000 aaa",
            }
        ]
        before = self.env["fleet.vehicle"].search_count([])
        matched = self.vehicle._navirec_match_vehicles(client)

        self.assertEqual(matched, 1)
        self.assertEqual(
            self.vehicle.navirec_uuid,
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(self.env["fleet.vehicle"].search_count([]), before)

    def test_duplicate_remote_plate_is_not_matched(self):
        client = Mock()
        client.get_vehicles.return_value = [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "registration": "T000AAA",
            },
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "registration": "T000-AAA",
            },
        ]
        matched = self.vehicle._navirec_match_vehicles(client)

        self.assertEqual(matched, 0)
        self.assertFalse(self.vehicle.navirec_uuid)

    def test_state_sync_writes_fields_and_allows_zero_coordinates(self):
        self.vehicle.navirec_uuid = (
            "11111111-1111-4111-8111-111111111111"
        )
        client = Mock()
        client.get_last_vehicle_states.return_value = self._fixture(
            "last_vehicle_states.json"
        )
        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 1)
        self.assertTrue(self.vehicle.navirec_has_position)
        self.assertEqual(self.vehicle.navirec_last_lat, 0.0)
        self.assertEqual(self.vehicle.navirec_last_lon, 0.0)
        self.assertEqual(self.vehicle.navirec_last_speed, 42.5)
        self.assertTrue(self.vehicle.navirec_ignition)
        self.assertAlmostEqual(self.vehicle.navirec_odometer, 123.456)
        action = self.vehicle.action_view_on_map()
        self.assertIn("query=0.0,0.0", action["url"])

    def test_bad_timestamp_does_not_abort_or_clear_previous_time(self):
        previous = fields.Datetime.from_string("2026-09-18 09:00:00")
        self.vehicle.write({
            "navirec_last_state_time": previous,
            "navirec_uuid": "11111111-1111-4111-8111-111111111111",
        })
        self.vehicle._write_navirec_state({
            "time": "not-a-timestamp",
            "speed": 12.0,
            "ignition": False,
            "location": {"coordinates": [10.0, 20.0]},
        })

        self.assertEqual(self.vehicle.navirec_last_state_time, previous)
        self.assertEqual(self.vehicle.navirec_last_speed, 12.0)
        self.assertTrue(self.vehicle.navirec_has_position)

    def test_unknown_state_uuid_is_skipped(self):
        self.vehicle.navirec_uuid = (
            "11111111-1111-4111-8111-111111111111"
        )
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": "https://api.navirec.com/vehicles/unknown/",
            "time": "2026-09-18T10:00:00Z",
        }]
        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 0)
        self.assertFalse(self.vehicle.navirec_last_state_time)

    def test_crons_noop_without_token(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.api_token", "")
        self.assertFalse(self.vehicle._cron_navirec_match_vehicles())
        self.assertFalse(self.vehicle._cron_navirec_sync_states())

    def test_integration_status_states(self):
        self.vehicle._compute_navirec_is_stale()
        self.vehicle._compute_navirec_integration_status()
        self.assertEqual(
            self.vehicle.navirec_integration_status,
            "not_mapped",
        )

        self.vehicle.navirec_uuid = (
            "11111111-1111-4111-8111-111111111111"
        )
        self.vehicle._compute_navirec_is_stale()
        self.vehicle._compute_navirec_integration_status()
        self.assertEqual(
            self.vehicle.navirec_integration_status,
            "awaiting_data",
        )

        self.vehicle.navirec_last_state_time = fields.Datetime.now()
        self.vehicle._compute_navirec_is_stale()
        self.vehicle._compute_navirec_integration_status()
        self.assertEqual(
            self.vehicle.navirec_integration_status,
            "connected",
        )

        self.env["ir.config_parameter"].sudo().set_param(
            "fleet_navirec.stale_hours", "24"
        )
        self.vehicle.navirec_last_state_time = (
            fields.Datetime.now() - timedelta(days=2)
        )
        self.vehicle._compute_navirec_is_stale()
        self.vehicle._compute_navirec_integration_status()
        self.assertEqual(
            self.vehicle.navirec_integration_status,
            "stale",
        )

    def test_zero_telemetry_is_available_not_missing(self):
        self.vehicle._write_navirec_state({
            "time": "2026-09-18T10:00:00Z",
            "location": {"coordinates": [0.0, 0.0]},
            "speed": 0.0,
            "ignition": False,
            "total_distance": 0.0,
        })
        self.vehicle._compute_navirec_display_values()

        self.assertTrue(self.vehicle.navirec_has_position)
        self.assertTrue(self.vehicle.navirec_has_speed)
        self.assertTrue(self.vehicle.navirec_has_ignition)
        self.assertTrue(self.vehicle.navirec_has_odometer)
        self.assertEqual(self.vehicle.navirec_speed_display, "0.0 km/h")
        self.assertEqual(self.vehicle.navirec_ignition_display, "Off")
        self.assertEqual(self.vehicle.navirec_odometer_display, "0.0 km")
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "0.0000000, 0.0000000",
        )

    def test_missing_telemetry_is_not_rendered_as_zero(self):
        self.vehicle._write_navirec_state({
            "time": "2026-09-18T10:00:00Z",
        })
        self.vehicle._compute_navirec_display_values()

        self.assertFalse(self.vehicle.navirec_has_position)
        self.assertFalse(self.vehicle.navirec_has_speed)
        self.assertFalse(self.vehicle.navirec_has_ignition)
        self.assertFalse(self.vehicle.navirec_has_odometer)
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "Not available",
        )
        self.assertEqual(self.vehicle.navirec_speed_display, "Not available")
        self.assertEqual(
            self.vehicle.navirec_ignition_display,
            "Not available",
        )
        self.assertEqual(
            self.vehicle.navirec_odometer_display,
            "Not available",
        )

    def test_vehicle_form_has_dedicated_navirec_telematics_tab(self):
        view = self.env.ref("fleet_navirec.fleet_vehicle_view_form_navirec")
        arch = view.arch_db
        self.assertIn('name="navirec_telematics"', arch)
        self.assertIn("Navirec / Telematics", arch)
        self.assertIn("navirec_integration_status", arch)
        self.assertIn("Stale GPS data", arch)


    def test_location_name_takes_priority_over_coordinates(self):
        self.vehicle.write({
            "navirec_location_name": "Nakonde Border",
            "navirec_last_lat": -9.342,
            "navirec_last_lon": 32.765,
            "navirec_has_position": True,
        })
        self.vehicle._compute_navirec_display_values()

        self.assertEqual(
            self.vehicle.navirec_position_display,
            "Nakonde Border",
        )

    def test_search_view_has_mapping_filters(self):
        view = self.env.ref(
            "fleet_navirec.fleet_vehicle_view_search_navirec"
        )
        arch = view.arch_db
        self.assertIn("navirec_mapped", arch)
        self.assertIn("navirec_not_mapped", arch)


    def test_new_state_clears_old_location_name_until_refreshed(self):
        self.vehicle.navirec_location_name = "Old Area"
        self.vehicle._write_navirec_state({
            "time": "2026-09-25T10:00:00Z",
            "location": {"coordinates": [39.2, -6.8]},
        })

        self.assertFalse(self.vehicle.navirec_location_name)
        self.vehicle._compute_navirec_display_values()
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "-6.8000000, 39.2000000",
        )
