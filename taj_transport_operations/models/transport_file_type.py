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
    clearing_service = fields.Selection(
        [("included", "Included"), ("not_included", "Not Included")],
        string="Default Clearing Service",
        help="Auto-filled when this File Type is selected on a sales line. "
        "Users can override it. Leave empty for no default.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
