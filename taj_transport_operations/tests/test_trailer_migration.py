"""Exercise the migration against isolated legacy SQL tables in Odoo tests."""
from odoo.tests import TransactionCase, tagged

from ..hooks import ensure_transport_categories, migrate_legacy_trailers


@tagged("post_install", "-at_install")
class TestTrailerMigration(TransactionCase):
    def test_existing_category_is_adopted_without_duplication(self):
        before = self.env["fleet.vehicle.model.category"].search_count([])
        categories = ensure_transport_categories(self.env)
        again = ensure_transport_categories(self.env)
        self.assertEqual(categories, again)
        self.assertEqual(self.env["fleet.vehicle.model.category"].search_count([]), before)

    def test_legacy_ids_are_remapped_and_history_is_preserved_idempotently(self):
        # Temporary tables shadow only the legacy sources, never Fleet or Sales.
        self.env.flush_all()
        cr = self.env.cr
        cr.execute("CREATE TEMP TABLE taj_transport_trailer ("
                   "id integer PRIMARY KEY, name varchar, registration_number varchar, "
                   "company_id integer, active boolean, notes text, fleet_vehicle_id integer)")
        cr.execute("CREATE TEMP TABLE taj_transport_assignment ("
                   "id integer PRIMARY KEY, trailer_id integer REFERENCES taj_transport_trailer(id), "
                   "vehicle_id integer, trip_id integer, effective_from timestamp, "
                   "effective_to timestamp, reason varchar, assigned_by_id integer)")
        cr.execute("CREATE TEMP TABLE taj_transport_initial_assignment_wizard ("
                   "id integer PRIMARY KEY, trailer_id integer REFERENCES taj_transport_trailer(id))")
        categories = ensure_transport_categories(self.env)
        brand = self.env["fleet.vehicle.model.brand"].create({"name": "Migration Test Brand"})
        model = self.env["fleet.vehicle.model"].create({
            "name": "Migration Test Trailer", "brand_id": brand.id,
            "category_id": categories["trailer"].id,
        })
        existing = self.env["fleet.vehicle"].create({
            "model_id": model.id, "license_plate": "MIG-EXISTING",
            "description": "Existing Fleet note",
        })
        # A legacy ID deliberately collides with an unrelated Fleet record ID.
        cr.execute("INSERT INTO taj_transport_trailer VALUES "
                   "(%s, 'LEGACY-NEW', 'MIG-NEW', %s, false, 'Keep legacy notes', NULL), "
                   "(%s, 'LEGACY-REUSE', 'MIG EXISTING', %s, true, 'Reused notes', NULL)",
                   (existing.id, self.env.company.id, existing.id + 100000, self.env.company.id))
        cr.execute("INSERT INTO taj_transport_assignment VALUES "
                   "(1, %s, NULL, 91, '2026-10-01 12:00:00', NULL, 'Initial', %s), "
                   "(2, %s, NULL, 92, '2026-09-01 12:00:00', '2026-09-02 12:00:00', 'History', %s)",
                   (existing.id, self.env.user.id, existing.id + 100000, self.env.user.id))
        cr.execute("INSERT INTO taj_transport_initial_assignment_wizard VALUES (1, %s)", (existing.id,))
        self.env["ir.config_parameter"].sudo().set_param(
            "taj_transport_operations.trailers_migrated_to_fleet", False)
        migrate_legacy_trailers(self.env)
        cr.execute("SELECT fleet_vehicle_id FROM taj_transport_trailer WHERE id = %s", (existing.id,))
        new_vehicle_id = cr.fetchone()[0]
        self.assertNotEqual(new_vehicle_id, existing.id)
        new_vehicle = self.env["fleet.vehicle"].browse(new_vehicle_id)
        self.assertEqual(new_vehicle.license_plate, "MIG-NEW")
        self.assertEqual(new_vehicle.model_id.name, "Legacy Trailer - Unknown Model")
        self.assertEqual(new_vehicle.model_id.brand_id.name, "Legacy Trailer - Unknown Manufacturer")
        self.assertNotEqual(new_vehicle.model_id, model)
        self.assertEqual(existing.model_id, model)
        self.assertFalse(new_vehicle.active)
        self.assertIn("LEGACY-NEW", new_vehicle.description)
        self.assertIn("Keep legacy notes", new_vehicle.description)
        self.assertIn("Existing Fleet note", existing.description)
        cr.execute("SELECT trailer_id, trip_id, effective_from::text, effective_to::text, "
                   "reason, assigned_by_id FROM taj_transport_assignment ORDER BY id")
        rows = cr.fetchall()
        self.assertEqual(rows, [
            (new_vehicle_id, 91, "2026-10-01 12:00:00", None, "Initial", self.env.user.id),
            (existing.id, 92, "2026-09-01 12:00:00", "2026-09-02 12:00:00", "History", self.env.user.id),
        ])
        cr.execute("SELECT trailer_id FROM taj_transport_initial_assignment_wizard")
        self.assertEqual(cr.fetchone()[0], new_vehicle_id)
        count = self.env["fleet.vehicle"].with_context(active_test=False).search_count([])
        description = existing.description
        migrate_legacy_trailers(self.env)
        self.assertEqual(self.env["fleet.vehicle"].with_context(active_test=False).search_count([]), count)
        self.assertEqual(existing.description, description)
