"""Retain legacy trip settings for reference without reusing them as customer-wide settings."""

from odoo.tools.sql import column_exists, rename_column


def migrate(cr, version):
    table = "taj_transport_trip"
    for column in (
        "email_notification_enabled",
        "email_interval_hours",
        "email_extra_recipients",
    ):
        if column_exists(cr, table, column):
            rename_column(cr, table, column, f"legacy_{column}")
