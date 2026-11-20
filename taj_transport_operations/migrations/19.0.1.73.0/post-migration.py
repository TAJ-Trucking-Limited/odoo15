"""Update the existing scheduled action to check the customer report schedule."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.ref("taj_transport_operations.ir_cron_trip_email_updates").write({
        "name": "Transport: Customer Trip Status Reports",
        "interval_number": 1,
    })
