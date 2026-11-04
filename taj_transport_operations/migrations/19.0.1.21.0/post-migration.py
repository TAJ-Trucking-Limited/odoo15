"""Fill empty sales vehicles from the first recorded truck assignment."""


def migrate(cr, version):
    cr.execute("""
        UPDATE sale_order_line AS line
           SET vehicle_id = source.vehicle_id
          FROM (
              SELECT DISTINCT ON (trip.sale_line_id)
                     trip.sale_line_id, assignment.vehicle_id
                FROM taj_transport_assignment AS assignment
                JOIN taj_transport_trip AS trip ON trip.id = assignment.trip_id
               WHERE assignment.vehicle_id IS NOT NULL
               ORDER BY trip.sale_line_id, assignment.effective_from, assignment.id
          ) AS source
         WHERE line.id = source.sale_line_id AND line.vehicle_id IS NULL
    """)
