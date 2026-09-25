import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from odoo import fields
from odoo.modules.module import get_module_path
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.navirec_api import NavirecAPIError


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

    def test_mapping_audit_reports_matches_duplicates_and_unmatched(self):
        duplicate_local = self.env["fleet.vehicle"].create({
            "model_id": self.model.id,
            "license_plate": "T000 AAA",
        })
        self.env["fleet.vehicle"].create({
            "model_id": self.model.id,
            "license_plate": "T999ZZZ",
        })
        client = Mock()
        client.get_vehicles.return_value = [
            {"id": "one", "registration": "T000-AAA"},
            {"id": "two", "registration": "T111BBB"},
            {"id": "three", "registration": "T222CCC"},
            {"id": "four", "registration": "T222-CCC"},
        ]

        audit = self.vehicle._navirec_build_mapping_audit(client)

        self.assertIn("T000AAA", audit["duplicate_local"])
        self.assertIn("T222CCC", audit["duplicate_remote"])
        self.assertIn("T111BBB", audit["unmatched_remote"])
        self.assertIn("T999ZZZ", audit["odoo_only"])
        self.assertEqual(audit["matched_count"], 0)
        duplicate_local.unlink()

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

    def test_area_name_matching_prefers_poi_and_supports_circle_polygon(self):
        areas = [
            {
                "name": "Large Geofence",
                "category": "geofence",
                "type": "polygon",
                "active": True,
                "shape": {
                    "type": "Polygon",
                    "coordinates": [[
                        [39.0, -7.0], [39.5, -7.0], [39.5, -6.5],
                        [39.0, -6.5], [39.0, -7.0],
                    ]],
                },
            },
            {
                "name": "Client Depot",
                "category": "point_of_interest",
                "type": "circle",
                "active": True,
                "center": {"type": "Point", "coordinates": [39.2, -6.8]},
                "radius": 500,
            },
        ]
        self.assertEqual(
            self.vehicle._navirec_area_name_for_coordinates(areas, 39.2, -6.8),
            "Client Depot",
        )
        self.assertEqual(
            self.vehicle._navirec_area_name_for_coordinates(areas, 39.45, -6.8),
            "Large Geofence",
        )
        self.assertFalse(
            self.vehicle._navirec_area_name_for_coordinates(areas, 40.0, -6.8)
        )

    def test_area_name_matching_supports_multipolygon_and_polygon_holes(self):
        areas = [{
            "name": "Port Zones",
            "category": "geofence",
            "type": "multipolygon",
            "active": True,
            "shape": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[
                        [38.0, -7.0], [38.2, -7.0], [38.2, -6.8],
                        [38.0, -6.8], [38.0, -7.0],
                    ]],
                    [
                        [
                            [39.0, -7.0], [39.5, -7.0], [39.5, -6.5],
                            [39.0, -6.5], [39.0, -7.0],
                        ],
                        [
                            [39.1, -6.9], [39.3, -6.9], [39.3, -6.7],
                            [39.1, -6.7], [39.1, -6.9],
                        ],
                    ],
                ],
            },
        }]
        self.assertEqual(
            self.vehicle._navirec_area_name_for_coordinates(areas, 38.1, -6.9),
            "Port Zones",
        )
        self.assertFalse(
            self.vehicle._navirec_area_name_for_coordinates(areas, 39.2, -6.8)
        )

    def test_state_sync_can_enrich_position_with_navirec_area_name(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        params.set_param("fleet_navirec.account_id", "acct")
        self.vehicle.navirec_uuid = "11111111-1111-4111-8111-111111111111"
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": "https://api.navirec.com/vehicles/11111111-1111-4111-8111-111111111111/",
            "time": "2026-09-25T10:00:00Z",
            "location": {"coordinates": [39.2, -6.8]},
            "speed": 0,
            "ignition": False,
        }]
        client.get_areas.return_value = [{
            "name": "TAJ Yard",
            "category": "point_of_interest",
            "type": "circle",
            "active": True,
            "center": {"type": "Point", "coordinates": [39.2, -6.8]},
            "radius": 250,
        }]

        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 1)
        client.get_areas.assert_called_once_with(account_id="acct")
        client.get_vehicle_events.assert_not_called()
        self.assertEqual(self.vehicle.navirec_location_name, "TAJ Yard")
        self.vehicle._compute_navirec_display_values()
        self.assertEqual(self.vehicle.navirec_position_display, "TAJ Yard")

    def test_state_sync_falls_back_to_nearby_navirec_event_address(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        params.set_param("fleet_navirec.account_id", "acct")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.navirec_uuid = uuid
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
            "speed": 0,
            "ignition": False,
        }]
        client.get_areas.return_value = []
        client.get_vehicle_events.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:15:00Z",
            "location": {"coordinates": [33.5221, -8.9065]},
            "address": "Itezi, Mbeya, Tanzania",
        }]

        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 1)
        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )
        client.get_vehicle_events.assert_called_once()

    def test_event_address_is_rejected_when_not_near_current_gps(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.navirec_uuid = uuid
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
        }]
        client.get_areas.return_value = []
        client.get_vehicle_events.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:15:00Z",
            "location": {"coordinates": [39.2, -6.8]},
            "address": "Dar es Salaam, Tanzania",
        }]
        client.get_trips.return_value = []

        self.vehicle._navirec_sync_states(client)

        self.assertFalse(self.vehicle.navirec_location_name)
        self.vehicle._compute_navirec_display_values()
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "-8.9064783, 33.5219450",
        )

    def test_nearby_cached_location_name_avoids_repeat_event_lookup(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.write({
            "navirec_uuid": uuid,
            "navirec_location_name": "Itezi, Mbeya, Tanzania",
            "navirec_has_position": True,
            "navirec_last_lon": 33.5219450,
            "navirec_last_lat": -8.9064783,
        })
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:20:00Z",
            "location": {"coordinates": [33.5220, -8.9065]},
        }]
        client.get_areas.return_value = []

        self.vehicle._navirec_sync_states(client)

        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )
        client.get_vehicle_events.assert_not_called()

    def test_manual_sync_uses_same_human_readable_location_enrichment(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        params.set_param("fleet_navirec.account_id", "acct")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.navirec_uuid = uuid
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
        }]
        client.get_areas.return_value = []
        client.get_vehicle_events.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:10:00Z",
            "location": {"coordinates": [33.5219, -8.9065]},
            "address": "Itezi, Mbeya, Tanzania",
        }]
        vehicle_type = type(self.vehicle)

        with patch.object(vehicle_type, "_navirec_client", return_value=client):
            action = self.vehicle.action_navirec_sync_now()

        self.assertEqual(action["params"]["type"], "success")
        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )
        client.get_vehicle_events.assert_called_once()

    def test_event_api_failure_still_falls_back_to_trip_address(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.navirec_uuid = uuid
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
        }]
        client.get_areas.return_value = []
        client.get_vehicle_events.side_effect = NavirecAPIError("events unavailable")
        client.get_trips.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "start_time": "2026-09-25T18:00:00Z",
            "end_time": "2026-09-25T19:30:00Z",
            "end_location": {"coordinates": [33.5220, -8.9065]},
            "end_address": "Itezi, Mbeya, Tanzania",
        }]

        self.vehicle._navirec_sync_states(client)

        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )
        client.get_trips.assert_called_once()

    def test_state_sync_falls_back_to_nearby_trip_end_address(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        params.set_param("fleet_navirec.account_id", "acct")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.navirec_uuid = uuid
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
        }]
        client.get_areas.return_value = []
        client.get_vehicle_events.return_value = []
        client.get_trips.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "start_time": "2026-09-25T18:00:00Z",
            "end_time": "2026-09-25T19:30:00Z",
            "start_location": {"coordinates": [33.40, -8.80]},
            "start_address": "Elsewhere, Tanzania",
            "end_location": {"coordinates": [33.5220, -8.9065]},
            "end_address": "Itezi, Mbeya, Tanzania",
        }]

        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 1)
        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )
        client.get_trips.assert_called_once()

    def test_trip_address_is_rejected_when_trip_endpoint_is_far_from_current_gps(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.navirec_uuid = uuid
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
        }]
        client.get_areas.return_value = []
        client.get_vehicle_events.return_value = []
        client.get_trips.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "start_time": "2026-09-25T18:00:00Z",
            "end_time": "2026-09-25T19:30:00Z",
            "end_location": {"coordinates": [39.2, -6.8]},
            "end_address": "Dar es Salaam, Tanzania",
        }]

        self.vehicle._navirec_sync_states(client)

        self.assertFalse(self.vehicle.navirec_location_name)
        self.vehicle._compute_navirec_display_values()
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "-8.9064783, 33.5219450",
        )

    def test_manual_sync_can_fall_back_to_nearby_trip_address(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        params.set_param("fleet_navirec.account_id", "acct")
        uuid = "11111111-1111-4111-8111-111111111111"
        self.vehicle.navirec_uuid = uuid
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
        }]
        client.get_areas.return_value = []
        client.get_vehicle_events.return_value = []
        client.get_trips.return_value = [{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "start_time": "2026-09-20T18:00:00Z",
            "end_time": "2026-09-20T19:30:00Z",
            "end_location": {"coordinates": [33.5220, -8.9065]},
            "end_address": "Itezi, Mbeya, Tanzania",
        }]
        vehicle_type = type(self.vehicle)

        with patch.object(vehicle_type, "_navirec_client", return_value=client):
            action = self.vehicle.action_navirec_sync_now()

        self.assertEqual(action["params"]["type"], "success")
        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )
        kwargs = client.get_trips.call_args.kwargs
        self.assertIn("start_time_gte", kwargs)
        self.assertIn("end_time_lte", kwargs)

    def test_area_enrichment_failure_does_not_block_position_sync(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        self.vehicle.navirec_uuid = "11111111-1111-4111-8111-111111111111"
        client = Mock()
        client.get_last_vehicle_states.return_value = [{
            "vehicle": "https://api.navirec.com/vehicles/11111111-1111-4111-8111-111111111111/",
            "time": "2026-09-25T10:00:00Z",
            "location": {"coordinates": [39.2, -6.8]},
        }]
        client.get_areas.side_effect = NavirecAPIError("missing area permission")
        client.get_vehicle_events.side_effect = NavirecAPIError(
            "missing event permission"
        )
        client.get_trips.side_effect = NavirecAPIError(
            "missing trip permission"
        )

        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 1)
        self.assertTrue(self.vehicle.navirec_has_position)
        self.assertFalse(self.vehicle.navirec_location_name)
        self.vehicle._compute_navirec_display_values()
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "-6.8000000, 39.2000000",
        )

    def _geocode_client(self, states, address="Itezi, Mbeya, Tanzania"):
        client = Mock()
        client.get_last_vehicle_states.return_value = states
        client.get_areas.return_value = []
        client.get_vehicle_events.return_value = []
        client.get_trips.return_value = []
        client.get_geocoding_context.return_value = (
            "https://realtime.navirec.com/geocoding/",
            {"key": "account-key"},
        )
        client.reverse_geocode.return_value = address
        return client

    def test_state_sync_uses_navirec_reverse_geocode_after_area_event_trip_miss(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        params.set_param("fleet_navirec.account_id", "acct")
        uuid = "54c0b29b-2535-4a47-9165-f7d83fb582b8"
        self.vehicle.navirec_uuid = uuid
        client = self._geocode_client([{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
            "speed": 0,
            "ignition": False,
        }])

        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 1)
        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )
        self.vehicle._compute_navirec_display_values()
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "Itezi, Mbeya, Tanzania",
        )
        self.assertAlmostEqual(self.vehicle.navirec_last_lat, -8.9064783)
        self.assertAlmostEqual(self.vehicle.navirec_last_lon, 33.5219450)
        client.get_geocoding_context.assert_called_once_with(account_id="acct")
        client.reverse_geocode.assert_called_once()

    def test_reverse_geocode_is_reused_for_a_nearby_vehicle_in_the_same_sync(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        first = "54c0b29b-2535-4a47-9165-f7d83fb582b8"
        second = "22222222-2222-4222-8222-222222222222"
        self.vehicle.navirec_uuid = first
        other = self.env["fleet.vehicle"].create({
            "model_id": self.model.id,
            "license_plate": "T000-BBB",
            "navirec_uuid": second,
        })
        client = self._geocode_client([
            {
                "vehicle": f"https://api.navirec.com/vehicles/{first}/",
                "time": "2026-09-25T20:18:00Z",
                "location": {"coordinates": [33.5219450, -8.9064783]},
            },
            {
                "vehicle": f"https://api.navirec.com/vehicles/{second}/",
                "time": "2026-09-25T20:18:00Z",
                "location": {"coordinates": [33.52210, -8.90660]},
            },
        ])

        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 2)
        self.assertEqual(self.vehicle.navirec_location_name, "Itezi, Mbeya, Tanzania")
        self.assertEqual(other.navirec_location_name, "Itezi, Mbeya, Tanzania")
        self.assertEqual(client.reverse_geocode.call_count, 1)
        client.get_geocoding_context.assert_called_once()

    def test_reverse_geocode_failure_does_not_block_position_sync(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        uuid = "54c0b29b-2535-4a47-9165-f7d83fb582b8"
        self.vehicle.navirec_uuid = uuid
        client = self._geocode_client([{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
            "speed": 0,
            "ignition": False,
        }])
        client.reverse_geocode.side_effect = NavirecAPIError(
            "Navirec geocoding error 503"
        )

        updated = self.vehicle._navirec_sync_states(client)

        self.assertEqual(updated, 1)
        self.assertTrue(self.vehicle.navirec_has_position)
        self.assertFalse(self.vehicle.navirec_location_name)
        self.vehicle._compute_navirec_display_values()
        self.assertEqual(
            self.vehicle.navirec_position_display,
            "-8.9064783, 33.5219450",
        )

    def test_manual_sync_uses_navirec_reverse_geocode(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("fleet_navirec.use_area_names", "True")
        uuid = "54c0b29b-2535-4a47-9165-f7d83fb582b8"
        self.vehicle.navirec_uuid = uuid
        client = self._geocode_client([{
            "vehicle": f"https://api.navirec.com/vehicles/{uuid}/",
            "time": "2026-09-25T20:18:00Z",
            "location": {"coordinates": [33.5219450, -8.9064783]},
        }])
        vehicle_type = type(self.vehicle)

        with patch.object(vehicle_type, "_navirec_client", return_value=client):
            action = self.vehicle.action_navirec_sync_now()

        self.assertEqual(action["params"]["type"], "success")
        self.assertEqual(
            self.vehicle.navirec_location_name,
            "Itezi, Mbeya, Tanzania",
        )

    def test_open_navirec_uses_verified_configured_deep_link_template(self):
        self.vehicle.navirec_uuid = "11111111-1111-4111-8111-111111111111"
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(
            "fleet_navirec.vehicle_url_template",
            "https://app.navirec.com/vehicle/{uuid}",
        )
        action = self.vehicle.action_open_in_navirec()
        self.assertEqual(
            action["url"],
            "https://app.navirec.com/vehicle/11111111-1111-4111-8111-111111111111",
        )

    def test_open_navirec_falls_back_to_application_home(self):
        self.vehicle.navirec_uuid = "11111111-1111-4111-8111-111111111111"
        self.env["ir.config_parameter"].sudo().set_param(
            "fleet_navirec.vehicle_url_template", ""
        )
        action = self.vehicle.action_open_in_navirec()
        self.assertEqual(action["url"], "https://app.navirec.com/")

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
