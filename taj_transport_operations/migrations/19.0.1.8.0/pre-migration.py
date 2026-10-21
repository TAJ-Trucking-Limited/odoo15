"""Keep Contact IDs separate before the transporter foreign key changes."""

from psycopg2 import sql


def migrate(cr, version):
    cr.execute("""
        SELECT relation
          FROM ir_model_fields
         WHERE model = 'taj.transport.trip' AND name = 'transporter_id'
    """)
    field = cr.fetchone()
    if not field or field[0] != "res.partner":
        return
    cr.execute("""
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'taj_transport_trip'
           AND column_name IN ('transporter_id', 'legacy_transporter_partner_id')
    """)
    columns = {row[0] for row in cr.fetchall()}
    if "transporter_id" not in columns or "legacy_transporter_partner_id" in columns:
        return
    # The renamed column must not retain a constraint name that Odoo needs
    # for the replacement transporter_id column during schema setup.
    cr.execute("""
        SELECT constraint_record.conname
          FROM pg_constraint constraint_record
          JOIN pg_attribute attribute
            ON attribute.attrelid = constraint_record.conrelid
           AND attribute.attnum = ANY(constraint_record.conkey)
         WHERE constraint_record.conrelid = 'taj_transport_trip'::regclass
           AND constraint_record.contype = 'f'
           AND attribute.attname = 'transporter_id'
    """)
    for (constraint_name,) in cr.fetchall():
        cr.execute(sql.SQL("ALTER TABLE taj_transport_trip DROP CONSTRAINT {}").format(
            sql.Identifier(constraint_name),
        ))
    cr.execute("""
        ALTER TABLE taj_transport_trip
        RENAME COLUMN transporter_id TO legacy_transporter_partner_id
    """)
