"""Trip form source selection and Vehicle Availability assignment regressions."""
import runpy

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import file_path

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

    def _vehicle_wizard(self, line, truck=None):
        vehicle = truck or self.truck
        action = vehicle.action_assign_transport_trip()
        self.assertEqual(
            action["res_model"], "taj.transport.vehicle.sale.allocation.wizard"
        )
        self.assertEqual(action["context"]["default_vehicle_id"], vehicle.id)
        return self.env["taj.transport.vehicle.sale.allocation.wizard"].with_context(
            action["context"]
        ).create({
            "vehicle_id": vehicle.id,
            "sale_order_id": line.order_id.id,
            "sale_line_id": line.id,
            "driver_id": self.driver.id,
            "etol": "2026-11-10",
        })

    def test_vehicle_selection_filters_unallocated_sale_lines_not_trips(self):
        from lxml import etree
        first = self._sale_line("SELECT1", vehicle_id=False)
        second = self._sale_line("SELECT2", vehicle_id=False)
        action = self.truck.action_assign_transport_trip()
        view = self.env.ref(
            "taj_transport_operations.view_vehicle_sale_allocation_wizard_form"
        )
        root = etree.fromstring(view.get_combined_arch())
        self.assertFalse(root.xpath("//field[@name='trip_id']"))
        line_field = root.xpath("//field[@name='sale_line_id']")[0]
        self.assertIn("'vehicle_id', '=', False", line_field.get("domain") or
                      self.env["taj.transport.vehicle.sale.allocation.wizard"]._fields["sale_line_id"].domain)
        self.assertIn("transport_trip_ids", self.env[
            "taj.transport.vehicle.sale.allocation.wizard"
        ]._fields["sale_line_id"].domain)
        self.assertEqual(action["context"]["default_vehicle_id"], self.truck.id)

        wrong = self._vehicle_wizard(first)
        wrong.sale_order_id = second.order_id
        with self.assertRaises(UserError):
            wrong.action_allocate()
        self.assertFalse(first.vehicle_id)
        self.assertFalse(first.transport_trip_ids)

    def test_availability_allocates_sales_line_without_creating_trip(self):
        line = self._sale_line("ASSIGN", vehicle_id=False)
        self.assertTrue(self.truck.transport_can_assign)
        self._vehicle_wizard(line).action_allocate()
        self.assertEqual(line.vehicle_id, self.truck)
        self.assertEqual(line.transport_allocated_driver_id, self.driver)
        self.assertFalse(line.transport_trip_ids)
        self.assertEqual(self.truck.transport_planned_sale_line_count, 1)
        self.assertEqual(self.truck.transport_availability_state, "standby")
        # Creation is a separate step in Sale Orders - Trip Not Started.
        trip = self._trip(line=line)
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertEqual(trip.current_driver_id, self.driver)
        self.assertEqual(self.truck.transport_availability_state, "assigned")
        self.truck.invalidate_recordset(["transport_planned_sale_line_count"])
        self.assertEqual(self.truck.transport_planned_sale_line_count, 0)
        self.assertEqual(len(trip.assignment_ids), 1)

    def test_vehicle_dialog_suggests_driver_and_line_etol(self):
        self.truck.driver_id = self.driver
        line = self._sale_line("PREFILL", vehicle_id=False, etol="2026-11-18")
        Wizard = self.env["taj.transport.vehicle.sale.allocation.wizard"]
        wizard = Wizard.new({
            "vehicle_id": self.truck.id,
            "sale_order_id": line.order_id.id,
            "sale_line_id": line.id,
        })
        self.assertEqual(wizard.driver_id, self.driver)
        wizard._onchange_sale_line_id()
        self.assertEqual(str(wizard.etol), "2026-11-18")
        wizard.sale_order_id = False
        wizard._onchange_sale_order_id()
        self.assertFalse(wizard.sale_line_id)
        self.assertFalse(wizard.etol)

    def test_vehicle_dialog_rejects_existing_trip_even_without_a_truck(self):
        line = self._sale_line("EXISTING-TRIP", vehicle_id=False)
        self._trip(line=line)
        with self.assertRaises(UserError):
            self._vehicle_wizard(line).action_allocate()
        self.assertFalse(line.vehicle_id)

    def test_duplicating_sales_order_clears_backfilled_vehicle(self):
        line = self._sale_line("DUPLICATE")
        duplicate = line.order_id.copy()
        self.assertFalse(duplicate.order_line.vehicle_id)

    def test_dispatcher_can_backfill_vehicle_without_sales_write_access(self):
        line = self._sale_line("DISPATCH-BACKFILL", vehicle_id=False)
        trip = self._trip(line=line)
        self.env.invalidate_all()
        self.env["taj.transport.assignment"].with_user(self.dispatcher).create({
            "trip_id": trip.id, "vehicle_id": self.truck.id, "driver_id": self.driver.id,
        })
        self.assertEqual(line.vehicle_id, self.truck)

    def test_upgrade_backfills_empty_vehicle_without_overwriting_source(self):
        line = self._sale_line("BACKFILL", vehicle_id=False)
        trip = self._trip(line=line)
        self._assign(trip)
        line.vehicle_id = False
        self.env.flush_all()
        migrate = runpy.run_path(file_path(
            "taj_transport_operations/migrations/19.0.1.21.0/post-migration.py"
        ))["migrate"]
        migrate(self.env.cr, "19.0.1.20.0")
        line.invalidate_recordset(["vehicle_id"])
        self.assertEqual(line.vehicle_id, self.truck)
        other = self.env["fleet.vehicle"].create({
            "model_id": self.truck_model.id, "license_plate": "KEEP-SALES-TRUCK",
        })
        line.vehicle_id = other
        self.env.flush_all()
        migrate(self.env.cr, "19.0.1.20.0")
        line.invalidate_recordset(["vehicle_id"])
        self.assertEqual(line.vehicle_id, other)

    def test_uncategorized_vehicle_can_be_assigned_and_marked_ready(self):
        self.truck.category_id = False
        line = self._sale_line("NO-CATEGORY", vehicle_id=False)
        self.assertTrue(self.truck.transport_can_assign)
        self._vehicle_wizard(line).action_allocate()
        trip = self._trip(line=line)
        self.assertEqual(trip.current_vehicle_id, self.truck)
        self.assertFalse(self.truck.category_id)
        self._plan(trip)
        trip.action_mark_ready()
        self.assertEqual(trip.state, "ready")

    def test_reserved_truck_uses_trip_resources_not_vehicle_sales_allocation(self):
        line = self._sale_line("RESERVED")
        # The inactive Fleet truck cannot auto-assign, so the Draft Trip
        # retains its Sales truck reservation when the vehicle returns.
        self.truck.active = False
        reserved_trip = self._trip(line=line)
        self.truck.active = True
        self.assertFalse(reserved_trip.assignment_ids)
        self.assertFalse(self.truck.transport_can_assign)
        with self.assertRaises(ValidationError):
            self.truck.action_assign_transport_trip()
        # Existing Draft trips still manage their resources in the Trip form.
        self.env["taj.transport.initial.assignment.wizard"].create({
            "trip_id": reserved_trip.id,
            "vehicle_id": self.truck.id,
            "driver_id": self.driver.id,
        }).action_assign_initial_resources()
        self.assertEqual(reserved_trip.current_vehicle_id, self.truck)

    def test_stale_vehicle_dialog_cannot_replace_an_allocated_sale_line(self):
        line = self._sale_line("STALE", vehicle_id=False)
        stale = self._vehicle_wizard(line)
        self._vehicle_wizard(line).action_allocate()
        with self.assertRaises(UserError):
            stale.action_allocate()
        self.assertEqual(line.vehicle_id, self.truck)
        self.assertFalse(line.transport_trip_ids)

    def test_vehicle_dialog_rechecks_availability_when_trip_becomes_active(self):
        target = self._sale_line("STALE-TARGET", vehicle_id=False)
        stale = self._vehicle_wizard(target)
        occupied = self._sale_line("OCCUPIED", vehicle_id=False)
        self._vehicle_wizard(occupied).action_allocate()
        self._trip(line=occupied)
        with self.assertRaises(UserError):
            stale.action_allocate()
        self.assertFalse(target.vehicle_id)

    def test_planned_sales_allocation_count_distinguishes_plans_from_busy(self):
        first = self._sale_line("PLANNED1", vehicle_id=False)
        second = self._sale_line("PLANNED2", vehicle_id=False)
        self._vehicle_wizard(first).action_allocate()
        self._vehicle_wizard(second).action_allocate()
        self.assertEqual(self.truck.transport_planned_sale_line_count, 2)
        self.assertEqual(self.truck.transport_availability_state, "standby")
        self.assertFalse(first.transport_trip_ids | second.transport_trip_ids)
