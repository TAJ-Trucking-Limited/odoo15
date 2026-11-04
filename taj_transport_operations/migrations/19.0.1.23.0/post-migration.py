"""Apply the confirmed-order lock to sales already confirmed before upgrade."""


def migrate(cr, version):
    cr.execute("UPDATE sale_order SET locked = TRUE WHERE state = 'sale' AND NOT COALESCE(locked, FALSE)")
