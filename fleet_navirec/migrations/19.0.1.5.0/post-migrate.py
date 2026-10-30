"""Move the fixed send_time_1/2/3 values into dedicated schedule rows.

Removed fields are not dropped by Odoo, so their columns are still readable
here. Each subscription that has no schedule rows yet gets one row per
non-empty legacy time.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'fleet_navirec_report_subscription'
        """
    )
    columns = {row[0] for row in cr.fetchall()}
    legacy = {"send_time_1", "send_time_2", "send_time_3"} & columns
    if not legacy:
        return

    values = " UNION ".join(
        f"SELECT id, {column} AS send_time FROM fleet_navirec_report_subscription"
        for column in sorted(legacy)
    )
    cr.execute(
        f"""
        INSERT INTO fleet_navirec_report_slot
            (subscription_id, "time", create_uid, create_date,
             write_uid, write_date)
        SELECT subscription.id, legacy.send_time,
               subscription.write_uid, timezone('utc', now()),
               subscription.write_uid, timezone('utc', now())
        FROM fleet_navirec_report_subscription subscription
        JOIN ({values}) legacy ON legacy.id = subscription.id
        WHERE legacy.send_time IS NOT NULL
          AND btrim(legacy.send_time) <> ''
          AND NOT EXISTS (
              SELECT 1 FROM fleet_navirec_report_slot slot
              WHERE slot.subscription_id = subscription.id
          )
        """
    )
    _logger.info(
        "Navirec: migrated %s legacy send times into schedule rows",
        cr.rowcount,
    )
