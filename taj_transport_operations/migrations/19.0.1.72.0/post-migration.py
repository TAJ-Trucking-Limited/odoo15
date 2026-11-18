"""Drop the planned departure/arrival fields from shipment requirements.

They are no longer configurable, so a stored requirement would demand a field
that the settings screen cannot show or uncheck.
"""

from odoo.tools.sql import column_exists

REMOVED = ("transport_planned_departure_at", "transport_planned_arrival_at")


def migrate(cr, version):
    if not column_exists(cr, "res_company", "transport_required_shipment_fields"):
        return

    # res_company.transport_required_shipment_fields is jsonb, so the jsonb
    # array operators must be used here, not the json_* functions.
    cr.execute(
        """
        UPDATE res_company
           SET transport_required_shipment_fields =
               transport_required_shipment_fields - %s::text[]
         WHERE transport_required_shipment_fields ?| %s::text[]
        """,
        (list(REMOVED), list(REMOVED)),
    )
