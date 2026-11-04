"""Preserve per-line source identifiers and snapshots before recomputation."""


def migrate(cr, version):
    for column, data_type in (
        ("legacy_sale_line_id", "integer"),
        ("legacy_container_number", "varchar"),
        ("legacy_bill_of_lading", "varchar"),
    ):
        cr.execute(f"ALTER TABLE taj_transport_trip_file ADD COLUMN IF NOT EXISTS {column} {data_type}")
    cr.execute("""
        UPDATE taj_transport_trip_file
           SET legacy_sale_line_id = COALESCE(legacy_sale_line_id, sale_line_id),
               legacy_container_number = COALESCE(legacy_container_number, container_number),
               legacy_bill_of_lading = COALESCE(legacy_bill_of_lading, bill_of_lading)
    """)
