"""Retain every historical File Number and link its original Sales line."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    rows = env["taj.transport.trip.file.line"]
    for file in env["taj.transport.trip.file"].search([("legacy_sale_line_id", "!=", False)]):
        line = file.legacy_sale_line_id.exists()
        if line and not rows.search_count([("sale_line_id", "=", line.id)]):
            if file.legacy_bill_of_lading:
                line.write({"bill_of_lading": file.legacy_bill_of_lading})
            rows.create({"file_id": file.id, "sale_line_id": line.id})
            file.message_post(body="File Number retained during the grouped-shipment upgrade. Original snapshots are preserved.")
