"""Transporters maintained independently from Contacts."""

from odoo import fields, models


class TajTransportTransporter(models.Model):
    _name = "taj.transport.transporter"
    _description = "Transporter"
    _order = "name, id"

    name = fields.Char(required=True)
    email = fields.Char()
