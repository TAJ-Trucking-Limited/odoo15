"""Preserve legacy UTC timestamps and convert ETA to Damascus calendar dates."""


def migrate(cr, version):
    cr.execute("""
        SELECT data_type FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'sale_order_line'
           AND column_name = 'eta'
    """)
    column = cr.fetchone()
    if not column or column[0] != 'timestamp without time zone':
        return
    cr.execute("ALTER TABLE sale_order_line ADD COLUMN IF NOT EXISTS eta_timestamp_backup timestamp without time zone")
    cr.execute("UPDATE sale_order_line SET eta_timestamp_backup = eta WHERE eta_timestamp_backup IS NULL AND eta IS NOT NULL")
    cr.execute("""
        ALTER TABLE sale_order_line ALTER COLUMN eta TYPE date
        USING ((eta AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Damascus')::date
    """)
