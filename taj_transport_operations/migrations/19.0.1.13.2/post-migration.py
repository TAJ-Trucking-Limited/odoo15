"""Preserve former order cargo defaults on product lines during upgrade."""

from psycopg2 import sql


def migrate(cr, version):
    # Post migration runs after line columns are created and before Odoo
    # removes the obsolete order fields. Earlier releases may lack description.
    cr.execute("""
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'sale_order'
           AND column_name IN (
               'cargo_type_id', 'cargo_description',
               'special_handling_instructions'
           )
    """)
    legacy_columns = {row[0] for row in cr.fetchall()}
    for field in (
        "cargo_type_id", "cargo_description", "special_handling_instructions"
    ):
        if field not in legacy_columns:
            continue
        # Preserve an existing line-specific value, including on cancelled
        # orders. Sections/notes have no cargo. Existing trips stay untouched.
        empty = (
            sql.SQL("line.{field} IS NULL") if field == "cargo_type_id"
            else sql.SQL("(line.{field} IS NULL OR line.{field} = '')")
        ).format(field=sql.Identifier(field))
        cr.execute(sql.SQL("""
            UPDATE sale_order_line AS line
               SET {field} = sale.{field}
              FROM sale_order AS sale
             WHERE line.order_id = sale.id
               AND line.product_id IS NOT NULL
               AND (line.display_type IS NULL OR line.display_type = 'product')
               AND {empty}
               AND sale.{field} IS NOT NULL
        """).format(field=sql.Identifier(field), empty=empty))
