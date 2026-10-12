"""Fleet category setup and the explicit legacy trailer ID migration."""
from html import escape

from psycopg2 import sql


def ensure_transport_categories(env):
    """Adopt existing categories without renaming them or duplicating names."""
    categories = {}
    for kind, label in (("truck", "Truck"), ("trailer", "Trailer")):
        xml_name = "fleet_category_" + kind
        category = env.ref("taj_transport_operations." + xml_name,
                           raise_if_not_found=False)
        if not category:
            matches = env["fleet.vehicle.model.category"].search([
                ("name", "=ilike", label),
            ])
            if len(matches) > 1:
                raise ValueError("Ambiguous Fleet category: " + label)
            category = matches or env["fleet.vehicle.model.category"].create({
                "name": label,
            })
            env["ir.model.data"].create({
                "module": "taj_transport_operations", "name": xml_name,
                "model": "fleet.vehicle.model.category", "res_id": category.id,
                "noupdate": True,
            })
        categories[kind] = category
    if categories["truck"] == categories["trailer"]:
        raise ValueError("Truck and Trailer must be different Fleet categories")
    return categories


def migrate_legacy_trailers(env):
    """Run before schema setup; never interpret a legacy ID as a Fleet ID.

    Source rows and their durable Fleet mapping remain in a locked archive.
    The marker and mapping are committed only with the complete module upgrade.
    Ambiguous matches abort the upgrade transaction instead of guessing.
    """
    cr = env.cr
    params = env["ir.config_parameter"].sudo()
    marker = "taj_transport_operations.trailers_migrated_to_fleet"
    if params.get_param(marker):
        return
    categories = ensure_transport_categories(env)
    cr.execute("SELECT to_regclass('taj_transport_trailer')")
    if not cr.fetchone()[0]:
        params.set_param(marker, "1")
        return
    cr.execute("ALTER TABLE taj_transport_trailer "
               "ADD COLUMN IF NOT EXISTS fleet_vehicle_id integer")
    cr.execute("SELECT id, name, registration_number, company_id, active, notes, "
               "fleet_vehicle_id FROM taj_transport_trailer ORDER BY id")
    trailers = cr.dictfetchall()
    fleet = env["fleet.vehicle"].sudo().with_context(active_test=False)
    model = None
    claimed = set()
    for row in trailers:
        vehicle = fleet.browse(row["fleet_vehicle_id"]).exists() if row["fleet_vehicle_id"] else fleet.browse()
        if not vehicle and row["registration_number"]:
            plate = "".join(c for c in row["registration_number"].upper() if c.isalnum())
            candidates = fleet.search([("company_id", "=", row["company_id"])])
            vehicle = candidates.filtered(lambda v: plate and plate == "".join(
                c for c in (v.license_plate or "").upper() if c.isalnum()))
        if len(vehicle) > 1:
            raise ValueError("Ambiguous Fleet match for legacy trailer %s" % row["id"])
        if vehicle and (vehicle.company_id.id != row["company_id"] or
                        vehicle.category_id != categories["trailer"]):
            raise ValueError("Fleet company/category mismatch for legacy trailer %s" % row["id"])
        if vehicle and vehicle.id in claimed:
            raise ValueError("Multiple legacy trailers match one Fleet vehicle")
        description = "<p>Legacy trailer identifier: %s</p><p>%s</p>" % (
            escape(row["name"] or ""), escape(row["notes"] or "").replace("\n", "<br/>"),
        )
        if not vehicle:
            if not model:
                model = env["fleet.vehicle.model"].search([
                    ("name", "=", "Legacy Trailer - Unknown Model"),
                    ("category_id", "=", categories["trailer"].id),
                    ("brand_id.name", "=", "Legacy Trailer - Unknown Manufacturer"),
                ], limit=2)
                if len(model) > 1:
                    raise ValueError("Ambiguous legacy trailer placeholder model")
                if not model:
                    brand = env["fleet.vehicle.model.brand"].search([
                        ("name", "=", "Legacy Trailer - Unknown Manufacturer"),
                    ], limit=2)
                    if len(brand) > 1:
                        raise ValueError("Ambiguous legacy trailer placeholder manufacturer")
                    brand = brand or env["fleet.vehicle.model.brand"].create({
                        "name": "Legacy Trailer - Unknown Manufacturer"})
                    model = env["fleet.vehicle.model"].create({
                        "name": "Legacy Trailer - Unknown Model",
                        "brand_id": brand.id, "category_id": categories["trailer"].id,
                    })
            vehicle = fleet.with_company(row["company_id"]).create({
                "model_id": model.id, "category_id": categories["trailer"].id,
                "company_id": row["company_id"], "active": row["active"],
                "license_plate": row["registration_number"] or row["name"],
                "description": description,
            })
        elif not row["fleet_vehicle_id"]:
            vehicle.write({"description": (vehicle.description or "") + description})
        claimed.add(vehicle.id)
        cr.execute("UPDATE taj_transport_trailer SET fleet_vehicle_id = %s WHERE id = %s",
                   (vehicle.id, row["id"]))

    # Drop only foreign keys that still point to the legacy table. PostgreSQL
    # otherwise rejects remapped IDs before Odoo can install the new Fleet FK.
    for table in ("taj_transport_assignment", "taj_transport_initial_assignment_wizard"):
        cr.execute("SELECT to_regclass(%s)", (table,))
        if not cr.fetchone()[0]:
            continue
        cr.execute("SELECT count(*) FROM information_schema.columns "
                   "WHERE table_name = %s AND column_name = 'trailer_id'", (table,))
        if not cr.fetchone()[0]:
            continue
        cr.execute(sql.SQL("SELECT count(*) FROM {} a LEFT JOIN taj_transport_trailer t "
                           "ON t.id = a.trailer_id WHERE a.trailer_id IS NOT NULL "
                           "AND t.fleet_vehicle_id IS NULL").format(sql.Identifier(table)))
        if cr.fetchone()[0]:
            raise ValueError("Unmapped legacy trailer references in " + table)
        cr.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass "
                   "AND confrelid = 'taj_transport_trailer'::regclass AND contype = 'f'", (table,))
        for (name,) in cr.fetchall():
            cr.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                sql.Identifier(table), sql.Identifier(name)))
        cr.execute(sql.SQL("UPDATE {} a SET trailer_id = t.fleet_vehicle_id "
                           "FROM taj_transport_trailer t WHERE a.trailer_id = t.id").format(
                               sql.Identifier(table)))
        cr.execute(sql.SQL("SELECT count(*) FROM {} a LEFT JOIN fleet_vehicle v "
                           "ON v.id = a.trailer_id WHERE a.trailer_id IS NOT NULL "
                           "AND v.id IS NULL").format(sql.Identifier(table)))
        if cr.fetchone()[0]:
            raise ValueError("Fleet trailer integrity check failed in " + table)
    params.set_param(marker, "1")


def post_init_hook(env):
    # A fresh install has no legacy IDs to translate.
    env["ir.config_parameter"].sudo().set_param(
        "taj_transport_operations.trailers_migrated_to_fleet", "1")
