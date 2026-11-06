"""Dispatch regressions and the Draft -> Start workflow, using real ORM records."""
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestTripDispatchSummary(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Dispatch summary customer"})
        cls.driver = cls.env["res.partner"].create({"name": "Dispatch summary driver"})
        brand = cls.env["fleet.vehicle.model.brand"].create({"name": "Dispatch summary brand"})
        model = cls.env["fleet.vehicle.model"].create({
            "name": "Dispatch summary model", "brand_id": brand.id,
            "category_id": cls.env.ref("taj_transport_operations.fleet_category_truck").id,
        })
        cls.trucks = cls.env["fleet.vehicle"].create([
            {"model_id": model.id, "license_plate": "SUMMARY-A", "driver_id": cls.driver.id},
            {"model_id": model.id, "license_plate": "SUMMARY-B"},
        ])
        cls.product = cls.env["product.product"].create({"name": "Summary freight", "type": "service"})
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.customer.id,
            "order_line": [Command.create({"product_id": cls.product.id, "price_unit": 100})],
        })
        cls.order.action_confirm()
        cls.line = cls.order.order_line
        cls.dispatcher = new_test_user(
            cls.env, login="summary_dispatcher", company_id=cls.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )

    def _assign(self, truck=None):
        wizard = self.env["taj.transport.assign.truck.wizard"].with_user(self.dispatcher).create({
            "sale_line_id": self.line.id, "vehicle_id": (truck or self.trucks[0]).id,
        })
        wizard.action_assign_truck()

    def _trip(self, assigned=True):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id, "origin_location": "Port", "destination_location": "Depot",
        })
        if assigned:
            self.env["taj.transport.assignment"].create({
                "trip_id": trip.id, "vehicle_id": self.trucks[0].id,
                "driver_id": self.driver.id,
                "effective_from": fields.Datetime.now() - timedelta(hours=4),
            })
        return trip

    def _queue_has_line(self, action_name, line=None):
        action = self.env.ref("taj_transport_operations." + action_name)
        return bool(self.env["sale.order.line"].search(
            [("id", "=", (line or self.line).id)] + safe_eval(action.domain)
        ))

    def test_stale_allocation_dialog_cannot_replace_the_first_vehicle(self):
        stale = self.env["taj.transport.assign.truck.wizard"].with_user(self.dispatcher).create({
            "sale_line_id": self.line.id, "vehicle_id": self.trucks[1].id,
        })
        self.assertFalse(stale.sale_line_id.vehicle_id)
        self._assign()
        with self.assertRaises(UserError):
            stale.action_assign_truck()
        self.assertEqual(self.line.vehicle_id, self.trucks[0])
        self.assertTrue(self.order.locked)

    def test_allocation_date_is_automatic_and_preserved(self):
        self.assertFalse(self.line.allocation_date)
        self._assign()
        allocated = self.line.allocation_date
        self.assertTrue(allocated)
        self.line.write({"vehicle_id": self.trucks[1].id})
        self.assertEqual(self.line.allocation_date, allocated)
        self.assertFalse(self.line.copy({"order_id": self.order.id}).allocation_date)

    def test_allocation_date_cannot_be_forged(self):
        with self.assertRaises(UserError):
            self.line.write({"allocation_date": fields.Datetime.now()})

    def test_default_vehicle_receives_a_real_allocation_time(self):
        line = self.env["sale.order.line"].with_context(
            default_vehicle_id=self.trucks[0].id, default_allocation_date="2000-01-01 00:00:00",
        ).create({"order_id": self.order.id, "product_id": self.product.id})
        self.assertEqual(line.vehicle_id, self.trucks[0])
        self.assertGreater(line.allocation_date.year, 2000)

    def test_assign_existing_draft_trip_creates_its_resource_assignment(self):
        trip = self._trip(assigned=False)
        self._assign()
        self.assertEqual(trip.current_vehicle_id, self.trucks[0])
        self.assertEqual(trip.current_driver_id, self.driver)
        self.assertEqual(self.line.vehicle_id, self.trucks[0])
        self.assertTrue(self.line.allocation_date)
        self.assertFalse(trip.initial_vehicle_id, "Keep the immutable creation snapshot")
        self.assertEqual(len(self.line.transport_trip_ids), 1)

    def test_downpayments_are_excluded_from_dispatch_queues(self):
        downpayment = self.env["sale.order.line"].create({
            "order_id": self.order.id, "product_id": self.product.id,
            "is_downpayment": True, "price_unit": 20,
        })
        for action in ("action_sale_order_line_without_truck", "action_sale_order_line_without_trip"):
            self.assertFalse(self._queue_has_line(action, downpayment))

    def test_not_started_queue_requires_truck_until_actual_start(self):
        self.assertFalse(self._queue_has_line("action_sale_order_line_without_trip"))
        trip = self._trip()
        self.assertFalse(self._queue_has_line("action_sale_order_line_without_trip"))
        self.line.sudo().write({"vehicle_id": self.trucks[0].id})
        self.assertTrue(self._queue_has_line("action_sale_order_line_without_trip"))
        self.assertEqual(self.line.action_open_or_create_transport_trip()["res_id"], trip.id)
        trip.action_start_trip()
        self.assertFalse(self._queue_has_line("action_sale_order_line_without_trip"))

    def test_start_from_draft_does_not_require_hidden_planned_dates(self):
        trip = self._trip()
        self.assertFalse(trip.planned_departure_at)
        self.assertFalse(trip.planned_arrival_at)
        trip.action_start_trip()
        self.assertEqual(trip.state, "in_progress")
        self.assertTrue(trip.actual_departure_at)
        self.assertTrue(trip.started_at)

    def test_start_dialog_accepts_historical_business_time(self):
        trip = self._trip()
        historical = fields.Datetime.now() - timedelta(hours=2)
        action = trip.action_open_start_trip_wizard()
        dialog = Form(self.env[action["res_model"]].with_context(**action["context"]))
        self.assertTrue(dialog.actual_start_at)
        dialog.actual_start_at = historical
        dialog.save().action_start()
        self.assertEqual(trip.actual_departure_at, historical)
        self.assertEqual(trip.state, "in_progress")
        recorded = trip.started_at
        trip.write({"actual_departure_at": historical + timedelta(minutes=5)})
        self.assertEqual(trip.started_at, recorded, "Keep the original action audit time")

    def test_actual_start_cannot_precede_assignment_or_be_in_the_future(self):
        trip = self._trip()
        for invalid in (fields.Datetime.now() + timedelta(days=1), fields.Datetime.now() - timedelta(days=1)):
            with self.assertRaises(ValidationError):
                trip.action_start_trip(actual_start_at=invalid)
        self.assertEqual(trip.state, "draft")

    def test_summary_uses_source_shipment_fields(self):
        self.line.write({"eta": "2026-12-01", "etol": "2026-12-02", "consignee": "Receiver"})
        trip = self._trip()
        self.assertEqual(trip.transport_file_id, self.line.transport_file_id)
        self.assertEqual(trip.etol, self.line.etol)
        self.assertEqual(trip.eta, self.line.eta)
        self.assertEqual(trip.allocation_date, self.line.allocation_date)
        self.assertEqual(trip.consignee, "Receiver")

    def test_eta_keeps_time_across_sales_file_and_trip(self):
        self.line.write({"eta": "2026-12-01 14:37:00"})
        trip = self._trip()
        row = self.line.transport_file_line_ids
        self.assertEqual(fields.Datetime.to_string(row.eta), "2026-12-01 14:37:00")
        self.assertEqual(trip.eta, row.eta)
        row.write({"eta": "2026-12-02 17:45:00"})
        self.assertEqual(fields.Datetime.to_string(self.line.eta), "2026-12-02 17:45:00")
        self.assertEqual(trip.eta, self.line.eta)
        self.assertEqual(self.line.price_unit, 100)
        self.assertIn("2026-12-02 17:45:00 UTC", self.order.trip_file_ids.etas)

    def test_eta_date_migration_uses_noon_damascus_and_preserves_null(self):
        spec = spec_from_file_location("eta_migration", Path(__file__).parents[1] / "migrations/19.0.1.27.0/pre-migration.py")
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        cr = self.env.cr
        cr.execute("SHOW search_path")
        original_path = cr.fetchone()[0]
        try:
            cr.execute("CREATE TEMP TABLE sale_order_line (id integer, eta date) ON COMMIT DROP")
            cr.execute("INSERT INTO sale_order_line (id, eta) VALUES (1, '2026-12-01'), (2, NULL)")
            cr.execute("SET LOCAL search_path TO pg_temp, public")
            migration.migrate(cr, "19.0.1.26.0")
            migration.migrate(cr, "19.0.1.26.0")
            cr.execute("SELECT eta FROM sale_order_line ORDER BY id")
            rows = cr.fetchall()
            self.assertEqual(fields.Datetime.to_string(rows[0][0]), "2026-12-01 09:00:00")
            self.assertFalse(rows[1][0])
        finally:
            cr.execute("DROP TABLE IF EXISTS pg_temp.sale_order_line")
            cr.execute("SELECT set_config('search_path', %s, true)", (original_path,))

    def test_activity_color_uses_company_inactivity_threshold(self):
        trip = self._trip()
        trip.action_start_trip(actual_start_at=fields.Datetime.now() - timedelta(hours=2))
        self.env.company.transport_inactivity_hours = 1
        self.assertEqual(trip.activity_status, "overdue")
        trip._record_trip_activity("Truck is moving")
        self.assertEqual(trip.activity_status, "recent")

    def test_create_trip_opens_saved_draft_with_route_and_summary(self):
        self.env["taj.transport.route"].create({
            "name": "Summary route", "product_tmpl_id": self.product.product_tmpl_id.id,
            "origin_location": "Port", "destination_location": "Depot",
        })
        action = self.line.with_user(self.dispatcher).action_open_or_create_transport_trip()
        trip = self.env["taj.transport.trip"].browse(action["res_id"])
        self.assertEqual(trip.state, "draft")
        self.assertEqual(trip.sale_line_id, self.line)
        self.assertFalse(trip.started_at)
        self.assertEqual(self.line.action_open_or_create_transport_trip()["res_id"], trip.id)

    def test_summary_latest_checkpoint_and_actual_start_correction(self):
        trip = self._trip()
        trip.action_start_trip(actual_start_at=fields.Datetime.now() - timedelta(hours=2))
        checkpoint = trip._add_manual_milestone({"name": "Dar es Salaam Port", "milestone_type_id": self.env.ref("taj_transport_operations.milestone_type_loading").id, "latitude": -6.8, "longitude": 39.3})
        arrived = fields.Datetime.now() - timedelta(hours=1)
        checkpoint._record_arrival(arrived)
        self.assertEqual(trip.last_milestone_id, checkpoint)
        self.assertIn("Dar es Salaam Port", trip.last_milestone_activity)
        self.assertEqual(trip.last_milestone_at, arrived)
        self.assertIn("-6.8,39.3", trip.email_position_map_url)
        with self.assertRaises(ValidationError):
            trip.write({"actual_departure_at": arrived + timedelta(minutes=5)})
        partial = trip._add_manual_milestone({"name": "Unknown longitude", "milestone_type_id": self.env.ref("taj_transport_operations.milestone_type_loading").id, "latitude": -6.8})
        partial._record_arrival(arrived + timedelta(minutes=10))
        self.assertFalse(trip.email_position_map_url)

    def test_dispatch_views_and_trip_filters(self):
        trip = self._trip()
        checkpoint = trip._add_manual_milestone({"name": "Loading", "milestone_type_id": self.env.ref("taj_transport_operations.milestone_type_loading").id})
        def in_menu(action_name):
            action = self.env.ref("taj_transport_operations." + action_name)
            return bool(self.env["taj.transport.milestone"].search(
                [("id", "=", checkpoint.id)] + safe_eval(action.domain)
            ))
        self.assertTrue(in_menu("action_transport_milestone_draft"))
        self.assertFalse(in_menu("action_transport_milestone_remaining"))
        trip.action_start_trip(actual_start_at=fields.Datetime.now() - timedelta(minutes=5))
        self.assertFalse(in_menu("action_transport_milestone_draft"))
        self.assertTrue(in_menu("action_transport_milestone_remaining"))
        self.assertFalse(in_menu("action_transport_milestone_finished"))
        trip.action_complete_trip()
        self.assertTrue(in_menu("action_transport_milestone_finished"))
        # A pending checkpoint on a completed parent belongs to Completed too.
        self.assertEqual(checkpoint.state, "pending")

    def test_start_correction_cannot_move_after_an_in_progress_replacement(self):
        trip = self._trip()
        now = fields.Datetime.now()
        trip.action_start_trip(actual_start_at=now - timedelta(hours=2))
        action = trip.action_open_replace_assignment_wizard()
        self.env["taj.transport.replace.assignment.wizard"].with_context(**action["context"]).create({
            "trip_id": trip.id, "expected_assignment_id": trip.current_assignment_id.id,
            "vehicle_id": self.trucks[1].id, "driver_id": self.driver.id,
            "effective_from": now - timedelta(hours=1), "reason": "Truck replacement",
        }).action_replace_resources()
        # The boundary itself must also be rejected: otherwise a second edit
        # could use the replacement row to move the start even further forward.
        for departure in (now - timedelta(hours=1), now - timedelta(minutes=30)):
            with self.assertRaises(ValidationError):
                trip.write({"actual_departure_at": departure})

    def test_upgrade_recovers_allocation_date_only_from_real_history(self):
        trip = self._trip()
        assignment = trip.current_assignment_id
        self.env.flush_all()
        self.env.cr.execute("UPDATE sale_order_line SET allocation_date = NULL WHERE id = %s", (self.line.id,))
        spec = spec_from_file_location("summary_migration", Path(__file__).parents[1] / "migrations/19.0.1.26.0/post-migration.py")
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.migrate(self.env.cr, "19.0.1.25.0")
        self.line.invalidate_recordset(["allocation_date"])
        self.assertEqual(self.line.allocation_date, assignment.effective_from)

    def test_upgrade_relocks_orders_and_refreshes_legacy_eta_summaries(self):
        self.line.write({"eta": "2026-12-01 09:00:00"})
        self.order.action_unlock()
        file = self.order.trip_file_ids
        self.env.flush_all()
        self.env.cr.execute("UPDATE taj_transport_trip_file SET etas = %s WHERE id = %s", ("2026-12-01", file.id))
        spec = spec_from_file_location("eta_post_migration", Path(__file__).parents[1] / "migrations/19.0.1.27.0/post-migration.py")
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.migrate(self.env.cr, "19.0.1.26.0")
        self.order.invalidate_recordset(["locked"])
        file.invalidate_recordset(["etas"])
        self.assertTrue(self.order.locked)
        self.assertEqual(file.etas, "2026-12-01 09:00:00 UTC")
