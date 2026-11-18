"""Drop the planned departure/arrival fields from shipment requirements.

They are no longer configurable, so a stored requirement would demand a field
that the settings screen cannot show or uncheck.
"""

from odoo.tools.sql import column_exists

REMOVED = ("transport_planned_departure_at", "transport_planned_arrival_at")


def migrate(cr, version):
    if not column_exists(cr, "res_company", "transport_required_shipment_fields"):
        return

    cr.execute(
        """
        UPDATE res_company
           SET transport_required_shipment_fields = (
               SELECT COALESCE(json_agg(name), '[]'::json)
                 FROM json_array_elements_text(
                     transport_required_shipment_fields
                 ) AS name
                WHERE name <> ALL(%s)
           )
         WHERE transport_required_shipment_fields ?| %s
        """,
        (list(REMOVED), list(REMOVED)),
    )
