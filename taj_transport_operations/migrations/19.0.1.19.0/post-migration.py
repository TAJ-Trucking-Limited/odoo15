"""Preserve existing order schedules on their sales lines."""


def migrate(cr, version):
    cr.execute("""
        SELECT count(*) FROM information_schema.columns
         WHERE table_name = 'sale_order'
           AND column_name IN ('transport_planned_departure_at', 'transport_planned_arrival_at')
    """)
    if cr.fetchone()[0] != 2:
        return
    cr.execute("""
        UPDATE sale_order_line AS line
           SET transport_planned_departure_at = COALESCE(line.transport_planned_departure_at, sale.transport_planned_departure_at),
               transport_planned_arrival_at = COALESCE(line.transport_planned_arrival_at, sale.transport_planned_arrival_at)
          FROM sale_order AS sale
         WHERE line.order_id = sale.id
           AND (sale.transport_planned_departure_at IS NOT NULL
                OR sale.transport_planned_arrival_at IS NOT NULL)
    """)
