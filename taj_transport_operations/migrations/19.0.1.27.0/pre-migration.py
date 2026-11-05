"""Promote the shipment ETA column from a date to a datetime.

Existing values carry no time component. Use noon in Damascus as a clearly
documented placeholder; it is not a recorded arrival time.
"""


def migrate(cr, version):
    cr.execute("""
        SELECT data_type
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'sale_order_line'
           AND column_name = 'eta'
    """)
    column = cr.fetchone()
    if not column or column[0] != "date":
        return
    cr.execute("""
        ALTER TABLE sale_order_line
        ALTER COLUMN eta TYPE timestamp
        USING (eta::timestamp + interval '12 hours')
              AT TIME ZONE 'Asia/Damascus' AT TIME ZONE 'UTC'
    """)
