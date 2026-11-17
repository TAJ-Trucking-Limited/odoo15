"""Carry forward File Type classifications used for route matching."""

from odoo.tools.sql import column_exists


def migrate(cr, version):
    if not column_exists(cr, "taj_transport_route", "file_type_id"):
        return

    cr.execute("""
        INSERT INTO taj_transport_trip_type (name, sequence, active)
        SELECT file_type.name, file_type.sequence, file_type.active
          FROM taj_transport_file_type AS file_type
         WHERE EXISTS (
             SELECT 1 FROM taj_transport_route AS route
              WHERE route.file_type_id = file_type.id
                AND route.trip_type_id IS NULL
         )
        ON CONFLICT (name) DO NOTHING
    """)
    cr.execute("""
        UPDATE taj_transport_route AS route
           SET trip_type_id = trip_type.id
          FROM taj_transport_file_type AS file_type
          JOIN taj_transport_trip_type AS trip_type ON trip_type.name = file_type.name
         WHERE route.trip_type_id IS NULL AND route.file_type_id = file_type.id
    """)
    # Prefer the actual route on an existing trip over a candidate route.
    cr.execute("""
        UPDATE sale_order_line AS line
           SET trip_type_id = route.trip_type_id
          FROM taj_transport_trip AS trip
          JOIN taj_transport_route AS route ON route.id = trip.route_id
         WHERE line.id = trip.sale_line_id
           AND line.trip_type_id IS NULL
           AND route.file_type_id IS NOT NULL
           AND route.trip_type_id IS NOT NULL
    """)
    cr.execute("""
        UPDATE sale_order_line AS line
           SET trip_type_id = trip_type.id
          FROM taj_transport_file_type AS file_type
          JOIN taj_transport_trip_type AS trip_type ON trip_type.name = file_type.name
         WHERE line.trip_type_id IS NULL
           AND line.file_type_id = file_type.id
           AND line.display_type IS NULL
           AND line.is_downpayment IS NOT TRUE
           AND EXISTS (
             SELECT 1
               FROM product_product AS product
               JOIN taj_transport_route AS route ON route.product_tmpl_id = product.product_tmpl_id
              WHERE product.id = line.product_id AND route.company_id = line.company_id
                AND route.active AND route.file_type_id = file_type.id
                AND route.trip_type_id = trip_type.id
           )
           AND NOT EXISTS (
             SELECT 1 FROM taj_transport_trip AS trip WHERE trip.sale_line_id = line.id
           )
    """)
