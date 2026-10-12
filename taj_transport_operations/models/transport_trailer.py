"""Simple company-scoped trailer master for transport operations."""
from odoo import fields, models


class TajTransportTrailer(models.Model):
    _name = "taj.transport.trailer"
    _description = "Transport Trailer"
    _order = "name, id"
    _check_company_auto = True

    _unique_company_trailer = models.Constraint(
        "UNIQUE(company_id, name)",
        "A trailer identifier must be unique within its company.",
    )

    name = fields.Char(
        string="Trailer Identifier", required=True, index=True,
        help="Company's internal trailer code or plate identifier.",
    )
    registration_number = fields.Char(
        string="Registration / Plate Number", copy=False,
    )
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company, index=True,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string="Notes")
