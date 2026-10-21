"""Preserve saved trip transporters as name/email catalog entries."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    cr.execute("""
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'taj_transport_trip'
           AND column_name = 'legacy_transporter_partner_id'
    """)
    if not cr.fetchone():
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        SELECT DISTINCT partner.id, partner.name, partner.email
          FROM res_partner partner
          JOIN taj_transport_trip trip
            ON trip.legacy_transporter_partner_id = partner.id
         ORDER BY partner.id
    """)
    for partner_id, name, email in cr.fetchall():
        transporter = env["taj.transport.transporter"].create({
            "name": name or "Transporter %s" % partner_id,
            "email": email or False,
        })
        # Include closed trips without going through the operational edit
        # guard. Only their catalog reference changes; Contacts stay intact.
        cr.execute("""
            UPDATE taj_transport_trip
               SET transporter_id = %s
             WHERE legacy_transporter_partner_id = %s
        """, (transporter.id, partner_id))
    cr.execute("ALTER TABLE taj_transport_trip DROP COLUMN legacy_transporter_partner_id")
    env["taj.transport.trip"].invalidate_model(["transporter_id"])
