"""Refresh stored date-only ETA summaries after the type conversion."""


def migrate(cr, version):
    cr.execute("""
        UPDATE taj_transport_trip_file AS file
           SET etas = (SELECT string_agg(DISTINCT to_char(line.eta, 'YYYY-MM-DD'), ', '
                                         ORDER BY to_char(line.eta, 'YYYY-MM-DD'))
                         FROM taj_transport_trip_file_line AS shipment
                         JOIN sale_order_line AS line ON line.id = shipment.sale_line_id
                        WHERE shipment.file_id = file.id)
    """)
