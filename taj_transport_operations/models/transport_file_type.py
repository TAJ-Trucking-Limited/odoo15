"""Manager-maintained file type classifications for route templates."""

from odoo import fields, models


class TajTransportFileType(models.Model):
    _name = "taj.transport.file.type"
    _description = "Transport File Type"
    _order = "sequence, name, id"

    _unique_name = models.Constraint(
        "UNIQUE(name)",
        "A file type with this name already exists.",
    )

    name = fields.Char(string="File Type", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
