"""Preserve and classify existing free-text cargo types when upgrading.

The existing cargo_type Char column stays untouched. Existing distinct
non-empty text values become available in the editable cargo-type catalog.
Only the new cargo_type_id foreign key is filled.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    CargoType = env["taj.transport.cargo.type"].sudo().with_context(
        active_test=False
    )

    cr.execute("""
        SELECT DISTINCT BTRIM(cargo_type)
          FROM taj_transport_trip
         WHERE cargo_type IS NOT NULL
           AND BTRIM(cargo_type) != ''
           AND cargo_type_id IS NULL
    """)
    names = [row[0] for row in cr.fetchall()]
    for name in names:
        category = CargoType.search([("name", "=ilike", name)], limit=1)
        if not category:
            category = CargoType.create({"name": name})
        cr.execute("""
            UPDATE taj_transport_trip
               SET cargo_type_id = %s
             WHERE cargo_type_id IS NULL
               AND BTRIM(cargo_type) = %s
        """, (category.id, name))
