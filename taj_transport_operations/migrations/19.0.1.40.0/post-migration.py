"""Fill historical missing milestone types from already-classified route lines.

This runs only when TAJ Operations is upgraded. Normal route-line writes
already fill missing types on open, untouched trip checkpoints, but do not
run for types assigned before the feature was introduced.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["taj.transport.milestone"]._backfill_existing_checkpoint_types()
