"""Recover known first-truck allocation times without inventing missing history."""


def migrate(cr, version):
    cr.execute("""
        UPDATE sale_order_line AS line
           SET allocation_date = history.allocated_at
          FROM (
                SELECT trip.sale_line_id, MIN(assignment.effective_from) AS allocated_at
                  FROM taj_transport_assignment AS assignment
                  JOIN taj_transport_trip AS trip ON trip.id = assignment.trip_id
                 WHERE assignment.vehicle_id IS NOT NULL
              GROUP BY trip.sale_line_id
          ) AS history
         WHERE line.id = history.sale_line_id
           AND line.vehicle_id IS NOT NULL
           AND line.allocation_date IS NULL
    """)
