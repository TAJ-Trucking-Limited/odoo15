"""Tests for Navirec live GPS position enrichment on trip email updates."""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestTripEmailPosition(TransactionCase):
    """Trip email updates render fresh, stale or missing Navirec GPS correctly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Navirec email customer",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Navirec email transport",
            "type": "service",
            "list_price": 100,
        })
        cls.brand = cls.env["fleet.vehicle.model.brand"].create({
            "name": "Navirec email brand",
        })
        cls.model = cls.env["fleet.vehicle.model"].create({
            "name": "Navirec email model",
            "brand_id": cls.brand.id,
            "category_id": cls.env.ref("taj_transport_operations.fleet_category_truck").id,
        })
        cls.driver = cls.env["res.partner"].create({
            "name": "Navirec Driver",
        })
        cls.vehicle = cls.env["fleet.vehicle"].create({
            "model_id": cls.model.id,
            "license_plate": "NAV-EMAIL-01",
            "driver_id": cls.driver.id,
        })
        cls.vehicle.write({
            "navirec_uuid": "33333333-3333-4333-8333-333333333333",
            "navirec_has_position": True,
            "navirec_last_lat": -6.7924,
            "navirec_last_lon": 39.2083,
            "navirec_location_name": "Dar es Salaam Port",
            "navirec_last_state_time": fields.Datetime.now(),
        })

    def _create_trip(self, vehicle=None):
        order = self.env["sale.order"].create({"partner_id": self.partner.id})
        line = self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": self.product.id,
            "name": "Navirec email freight",
            "product_uom_qty": 1,
            "price_unit": 100,
            "vehicle_id": vehicle.id if vehicle else False,
        })
        order.action_confirm()
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": line.id,
            "origin_location": "Dar es Salaam",
            "destination_location": "Morogoro",
        })
        return trip

    def test_fresh_gps_position_rendered(self):
        """Fresh Navirec GPS provides live location, coordinates, and Google Maps link."""
        trip = self._create_trip(self.vehicle)
        pos = trip._get_email_update_position_data()

        self.assertEqual(pos["source"], "navirec")
        self.assertEqual(pos["label"], "Dar es Salaam Port")
        self.assertAlmostEqual(pos["latitude"], -6.7924)
        self.assertAlmostEqual(pos["longitude"], 39.2083)
        self.assertEqual(pos["map_url"], "https://www.google.com/maps?q=-6.7924,39.2083")
        self.assertFalse(pos["is_stale"])
        self.assertFalse(pos["notice"])

        # Preview fields on the trip form reflect Navirec
        self.assertEqual(trip.email_position_preview, "Dar es Salaam Port")
        self.assertEqual(trip.email_position_source, "Navirec")
        self.assertEqual(trip.email_position_map_url, "https://www.google.com/maps?q=-6.7924,39.2083")

    def test_stale_gps_falls_back_to_planned_origin_when_no_milestones(self):
        """When GPS is stale and no actual milestones exist, fallback to planned origin with notice."""
        trip = self._create_trip(self.vehicle)
        stale_time = fields.Datetime.now() - timedelta(days=2)
        self.vehicle.write({"navirec_last_state_time": stale_time})
        self.assertTrue(self.vehicle.navirec_is_stale)

        pos = trip._get_email_update_position_data()
        self.assertEqual(pos["source"], "planned_with_stale_gps")
        self.assertTrue(pos["is_stale"])
        self.assertEqual(pos["label"], "Dar es Salaam")
        # Maps link must NOT show stale GPS coordinates when the displayed label is the planned origin
        self.assertFalse(pos["map_url"])
        self.assertTrue(pos["notice"])
        self.assertIn("stale", pos["notice"].lower())
        self.assertIn("planned location, not a confirmed checkpoint or live position", pos["notice"])

    def test_stale_gps_falls_back_to_milestone_when_milestones_exist(self):
        """When GPS is stale and an actual milestone event exists, fallback to milestone."""
        trip = self._create_trip(self.vehicle)
        stale_time = fields.Datetime.now() - timedelta(days=2)
        self.vehicle.write({"navirec_last_state_time": stale_time})

        # Add milestone to trip and record arrival
        m_type = self.env["taj.transport.milestone.type"].search([], limit=1)
        if not m_type:
            m_type = self.env["taj.transport.milestone.type"].create({"name": "Checkpoint"})
        milestone = self.env["taj.transport.milestone"].create({
            "trip_id": trip.id,
            "name": "Kibaha Weighbridge",
            "milestone_type_id": m_type.id,
            "sequence": 10,
        })
        # Mark trip ready so milestone events are allowed
        trip.write({
            "planned_departure_at": fields.Datetime.now() - timedelta(hours=5),
            "planned_arrival_at": fields.Datetime.now() + timedelta(hours=5),
        })
        trip.action_mark_ready()
        arrival_time = fields.Datetime.now() - timedelta(hours=3)
        milestone._record_arrival(arrival_time, self.env.user)

        pos = trip._get_email_update_position_data()
        self.assertEqual(pos["source"], "milestone_with_stale_gps")
        self.assertTrue(pos["is_stale"])
        self.assertIn("Arrived at Kibaha Weighbridge", pos["label"])
        self.assertEqual(pos["updated_at"], arrival_time)
        # Maps link corresponds to presented location (milestone has no lat/lon -> False)
        self.assertFalse(pos["map_url"])
        self.assertIn("Showing last confirmed checkpoint", pos["notice"])

    def test_gps_missing_timestamp_treated_as_stale(self):
        """When GPS coordinates exist but navirec_last_state_time is missing, position is not fresh."""
        trip = self._create_trip(self.vehicle)
        self.vehicle.write({"navirec_last_state_time": False})

        pos = trip._get_email_update_position_data()
        self.assertTrue(pos["is_stale"])
        self.assertNotEqual(pos["source"], "navirec")
        self.assertIn("stale", pos["notice"].lower())
        self.assertIn("timestamp missing", pos["notice"])

    def test_coordinate_zero_edge_cases(self):
        """Coordinates (0.0, 0.0) are legitimate and must be treated as valid and fresh."""
        now = fields.Datetime.now()
        trip = self._create_trip(self.vehicle)
        self.vehicle.write({
            "navirec_last_lat": 0.0,
            "navirec_last_lon": 0.0,
            "navirec_last_state_time": now,
            "navirec_location_name": "Gulf of Guinea / Prime Meridian",
        })

        pos = trip._get_email_update_position_data()
        self.assertFalse(pos["is_stale"])
        self.assertEqual(pos["source"], "navirec")
        self.assertEqual(pos["latitude"], 0.0)
        self.assertEqual(pos["longitude"], 0.0)
        self.assertEqual(pos["map_url"], "https://www.google.com/maps?q=0.0,0.0")

        # Invalid coordinate (out of range > 90) must not be treated as fresh
        self.vehicle.write({"navirec_last_lat": 999.0})
        pos_invalid = trip._get_email_update_position_data()
        self.assertTrue(pos_invalid["is_stale"])

    def test_missing_navirec_gps_falls_back(self):
        """Vehicle without GPS falls back to base milestone/planned provider."""
        truck_no_gps = self.env["fleet.vehicle"].create({
            "model_id": self.model.id,
            "license_plate": "NO-GPS-01",
        })
        trip = self._create_trip(truck_no_gps)
        pos = trip._get_email_update_position_data()

        self.assertEqual(pos["source"], "planned")
        self.assertEqual(pos["label"], "Dar es Salaam")
        self.assertFalse(pos["map_url"])
        self.assertFalse(pos["latitude"])
