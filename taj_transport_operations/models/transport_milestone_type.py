"""Configurable checkpoint types used by route templates and milestones."""

from odoo import fields, models


class TajTransportMilestoneType(models.Model):
    """A classification for a checkpoint, e.g. Loading or Border Crossing.

    This catalog is deliberately configuration, not a fixed approved list:
    a route template line works with or without a type, and the Transport
    Manager maintains the options. Trip milestones store their own snapshot
    of the type name so renaming a type never rewrites trip history.
    """

    _name = "taj.transport.milestone.type"
    _description = "Transport Checkpoint Type"
    _order = "sequence, name, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _unique_name = models.Constraint(
        "UNIQUE(name)",
        "A checkpoint type with this name already exists.",
    )
