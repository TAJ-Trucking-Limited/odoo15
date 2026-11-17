"""Manager-maintained classifications selected on sales order lines."""

from odoo import fields, models


class TajTransportTripType(models.Model):
    _name = "taj.transport.trip.type"
    _description = "Transport Trip Type"
    _order = "sequence, name, id"

    _unique_name = models.Constraint(
        "UNIQUE(name)", "A trip type with this name already exists.",
    )

    name = fields.Char(string="Trip Type", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
