"""Locked legacy archive, retained until staging migration has been reviewed.

New trailers are exclusively Fleet vehicles. No UI or ACL exposes this archive.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TajTransportTrailer(models.Model):
    _name = "taj.transport.trailer"
    _description = "Legacy Transport Trailer Archive"
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
    fleet_vehicle_id = fields.Many2one("fleet.vehicle", readonly=True, ondelete="restrict")

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError(_("Create trailers in Odoo Fleet."))

    def write(self, vals):
        raise UserError(_("Legacy trailer records are a locked migration archive."))

    def unlink(self):
        raise UserError(_("Legacy trailer records are a locked migration archive."))
