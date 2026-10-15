"""Configurable cargo types for the Transport Trip dropdown."""

from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportCargoType(models.Model):
    _name = "taj.transport.cargo.type"
    _description = "Transport Cargo Type"
    _order = "sequence, name, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _unique_name = models.Constraint(
        "UNIQUE(name)",
        "A cargo type with this name already exists.",
    )

    def write(self, vals):
        # Existing records are historical classifications. Renaming an
        # in-use category would make legacy snapshots disagree with the
        # catalog; archive and create a new type instead.
        if "name" in vals and self.env["taj.transport.trip"].sudo().search_count([
            ("cargo_type_id", "in", self.ids),
        ]):
            raise UserError(_(
                "A cargo type used by a trip cannot be renamed. "
                "Archive it and create a new cargo type instead."
            ))
        return super().write(vals)
