"""Apply the price-only lock and refresh grouped ETA datetime summaries."""


def migrate(cr, version):
    cr.execute("""
        UPDATE sale_order SET locked = TRUE
         WHERE state IN ('sale', 'done') AND NOT COALESCE(locked, FALSE)
    """)
    cr.execute("""
        UPDATE taj_transport_trip_file AS file
           SET etas = (
               SELECT string_agg(DISTINCT to_char(line.eta, 'YYYY-MM-DD HH24:MI:SS') || ' UTC', ', '
                                 ORDER BY to_char(line.eta, 'YYYY-MM-DD HH24:MI:SS') || ' UTC')
                 FROM taj_transport_trip_file_line AS shipment
                 JOIN sale_order_line AS line ON line.id = shipment.sale_line_id
                WHERE shipment.file_id = file.id
           )
    """)
