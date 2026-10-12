from odoo import SUPERUSER_ID, api
from odoo.addons.taj_transport_operations.hooks import migrate_legacy_trailers


def migrate(cr, version):
    migrate_legacy_trailers(api.Environment(cr, SUPERUSER_ID, {}))
