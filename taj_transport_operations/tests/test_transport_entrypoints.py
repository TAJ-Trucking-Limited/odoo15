"""Trip form source selection and Vehicle Availability assignment regressions."""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .phase3_case import TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestTransportEntrypoints(TajTransportPhase3Case):
    user_tag = "transport_entrypoints"

    def test_dispatch_defaults_source_and_unique_route_without_saving(self):
        line = self._sale_line("ENTRY", vehicle_id=False, cargo_description="Glass")
        route = self._route("Entry route", product_tmpl_id=line.product_id.product_tmpl_id.id)
        action = line.action_prepare_transport_trip()
        defaults = self.env["taj.transport.trip"].with_context(action["context"]).default_get([
            "sale_order_id", "sale_line_id", "route_id", "cargo_description",
        ])
        self.assertEqual(defaults["sale_order_id"], line.order_id.id)
        self.assertEqual(defaults["sale_line_id"], line.id)
        self.assertEqual(defaults["route_id"], route.id)
        self.assertEqual(defaults["cargo_description"], "Glass")
        self.assertFalse(line.transport_trip_ids)

    def test_selected_order_must_match_line_and_cannot_change_after_save(self):
        line = self._sale_line("MATCH", vehicle_id=False)
        other = self._sale_line("OTHER", vehicle_id=False)
        route = self._route("Matching route", product_tmpl_id=line.product_id.product_tmpl_id.id)
        with self.assertRaises(ValidationError):
            self.env["taj.transport.trip"].create({
                "sale_line_id": line.id, "sale_order_id": other.order_id.id,
                "route_id": route.id,
            })
        trip = self._trip(route, line=line)
        with self.assertRaises(UserError):
            trip.write({"sale_order_id": other.order_id.id})
        with self.assertRaises(UserError):
            line.write({"order_id": other.order_id.id})

    def test_form_route_must_match_source_product(self):
        line = self._sale_line("ROUTE", vehicle_id=False)
        unrelated = self._route("Unlinked template")
        with self.assertRaises(UserError):
            self.env["taj.transport.trip"].with_context(transport_trip_entry=True).create({
                "sale_line_id": line.id, "route_id": unrelated.id,
            })
        self.assertFalse(line.transport_trip_ids)

    def test_source_change_clears_previous_route_and_cargo(self):
        first = self._sale_line("FIRST", cargo_description="Glass")
        second = self._sale_line("SECOND", vehicle_id=False)
        trip = self.env["taj.transport.trip"].new({"sale_line_id": first.id})
        trip._onchange_sale_line_suggest_route()
        self.assertEqual(trip.cargo_description, "Glass")
        trip.sale_order_id = second.order_id
        trip._onchange_sale_order_id()
        trip._onchange_sale_line_suggest_route()
        self.assertFalse(trip.sale_line_id)
        self.assertFalse(trip.route_id)
        self.assertFalse(trip.cargo_description)
        self.assertEqual(trip.sale_order_id, second.order_id)

    def test_transport_selector_labels_show_order_product_and_truck(self):
        line = self._sale_line("LABEL")
        self.assertEqual(
            line.order_id.with_context(transport_order_label=True).display_name,
            line.order_id.name,
        )
        label = line.with_context(transport_line_label=True).display_name
        self.assertIn(line.product_id.display_name, label)
        self.assertIn(line.vehicle_id.license_plate, label)

    def _vehicle_wizard(self, trip):
        action = self.truck.action_assign_transport_trip()
        return self.env["taj.transport.initial.assignment.wizard"].with_context(action["context"]).create({
            "trip_id": trip.id, "vehicle_id": self.truck.id,
            "driver_id": self.driver.id,
        })

    def test_availability_assigns_truck_without_changing_sales_source(self):
        line = self._sale_line("ASSIGN", vehicle_id=False)
        trip = self._trip(line=line)
        self._vehicle_wizard(trip).action_assign_initial_resources()
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertEqual(trip.current_driver_id, self.driver)
        self.assertEqual(self.truck.transport_availability_state, "assigned")
        self.assertFalse(trip.initial_vehicle_id)
        self.assertFalse(line.vehicle_id)
        self.assertEqual(len(trip.assignment_ids), 1)

    def test_reserved_truck_can_only_be_assigned_to_its_own_trip(self):
        reserved_trip = self._trip(tag="RESERVED")
        other = self._trip(line=self._sale_line("TARGET", vehicle_id=False))
        wizard = self._vehicle_wizard(other)
        self.assertIn(reserved_trip, wizard.available_trip_ids)
        self.assertNotIn(other, wizard.available_trip_ids)
        with self.assertRaises(UserError):
            wizard.action_assign_initial_resources()
        self.assertFalse(other.assignment_ids)
        self._vehicle_wizard(reserved_trip).action_assign_initial_resources()
        self.assertEqual(reserved_trip.current_vehicle_id, self.truck)

    def test_stale_vehicle_dialog_cannot_reuse_assigned_truck(self):
        first = self._trip(line=self._sale_line("FIRSTTRIP", vehicle_id=False))
        second = self._trip(line=self._sale_line("SECONDTRIP", vehicle_id=False))
        stale = self._vehicle_wizard(second)
        self._vehicle_wizard(first).action_assign_initial_resources()
        with self.assertRaises(UserError):
            stale.action_assign_initial_resources()
        self.assertFalse(second.assignment_ids)
