from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestTransportMilestonePosition(TransactionCase):
    """The milestone menu shows the truck's live Navirec position."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Navirec milestone customer",
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Navirec milestone freight",
                "type": "service",
                "list_price": 100,
            }
        )
        cls.brand = cls.env["fleet.vehicle.model.brand"].create(
            {
                "name": "Navirec milestone brand",
            }
        )
        cls.model = cls.env["fleet.vehicle.model"].create(
            {
                "name": "Navirec milestone model",
                "brand_id": cls.brand.id,
                "vehicle_type": "car",
            }
        )
        cls.vehicle = cls.env["fleet.vehicle"].create(
            {
                "model_id": cls.model.id,
                "license_plate": "NAV-MILE-01",
            }
        )
        cls.vehicle.write(
            {
                "navirec_uuid": "22222222-2222-4222-8222-222222222222",
                "navirec_has_position": True,
                "navirec_last_lat": 33.8938,
                "navirec_last_lon": 35.5018,
                "navirec_location_name": "Beirut Port",
                "navirec_last_state_time": fields.Datetime.now(),
            }
        )

    def _milestone(self, vehicle=None):
        order = self.env["sale.order"].create({"partner_id": self.partner.id})
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "name": "Navirec milestone freight",
                "product_uom_qty": 1,
                "price_unit": 100,
                "vehicle_id": vehicle.id if vehicle else False,
            }
        )
        order.action_confirm()
        trip = self.env["taj.transport.trip"].create({"sale_line_id": line.id})
        return trip._add_manual_milestone({"name": "Loading"})

    def test_milestone_shows_truck_current_position(self):
        milestone = self._milestone(self.vehicle)

        self.assertEqual(milestone.vehicle_id, self.vehicle)
        self.assertTrue(milestone.navirec_has_position)
        self.assertEqual(milestone.navirec_position_display, "Beirut Port")
        self.assertEqual(
            milestone.navirec_last_state_time,
            self.vehicle.navirec_last_state_time,
        )
        self.assertFalse(milestone.navirec_is_stale)

    def test_milestone_position_marks_stale_gps(self):
        milestone = self._milestone(self.vehicle)
        self.vehicle.write(
            {
                "navirec_last_state_time": fields.Datetime.now() - timedelta(days=2),
            }
        )

        self.assertTrue(milestone.navirec_is_stale)

    def test_milestone_map_action_opens_truck_coordinates(self):
        milestone = self._milestone(self.vehicle)

        action = milestone.action_view_current_truck_on_map()

        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn("33.8938", action["url"])
        self.assertIn("35.5018", action["url"])

    def test_milestone_without_truck_cannot_open_map(self):
        milestone = self._milestone()

        self.assertFalse(milestone.vehicle_id)
        with self.assertRaises(UserError):
            milestone.action_view_current_truck_on_map()
