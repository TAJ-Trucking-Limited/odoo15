"""Reusable route templates and their ordered checkpoint definitions.

A template only *describes* the expected checkpoints. Trips copy the
definition into their own milestone records, so editing a template never
changes a trip that was already generated (see ``taj.transport.milestone``).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class TajTransportRoute(models.Model):
    _name = "taj.transport.route"
    _description = "Transport Route Template"
    _order = "name, id"
    _check_company_auto = True

    _unique_name_company = models.Constraint(
        "UNIQUE(company_id, name)",
        "A route template with this name already exists for this company.",
    )

    name = fields.Char(
        string="Route Template",
        required=True,
        index=True,
        help="Short operational name, e.g. 'Kasumbalesa - Dar es Salaam'.",
    )
    active = fields.Boolean(
        default=True,
        help="Archive a route that is no longer used. Trips keep their own "
        "checkpoint records and are never changed by archiving.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        help="Route templates are reusable inside their own company. They "
        "are hidden from users who cannot see this company.",
    )
    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Sales Product",
        index=True,
        check_company=True,
        ondelete="restrict",
        help="Optional link used by Sales Create Trips / Trip buttons to "
        "match this template to a sales line's product. Several active "
        "templates may share the same product. Leave empty for standalone "
        "or legacy templates that are chosen only on the trip form.",
    )
    origin_location = fields.Char(
        string="Planned Origin",
        help="Optional planning default. On trip create with this template, "
        "a non-empty value takes precedence over provided or product-name "
        "locations. On a Draft trip route change, a non-empty value "
        "overwrites the trip location; a blank template field keeps the "
        "existing trip location.",
    )
    destination_location = fields.Char(string="Planned Destination")
    notes = fields.Text(string="Route Notes")
    line_ids = fields.One2many(
        "taj.transport.route.line",
        "route_id",
        string="Checkpoints",
        copy=True,
    )
    line_count = fields.Integer(
        string="Checkpoints",
        compute="_compute_line_count",
    )

    @api.depends("line_ids")
    def _compute_line_count(self):
        for route in self:
            route.line_count = len(route.line_ids)

    @api.model
    def _matching_for_sale_line(self, sale_line):
        """Active, same-company templates linked to the line's product.

        No uniqueness: zero, one or many matches are all valid outcomes.
        Record rules and the caller's read rights apply; unreadable rows
        are dropped so Sales buttons never auto-pick a hidden template.
        """
        if not sale_line or not sale_line.product_id:
            return self.browse()
        company = sale_line.company_id or sale_line.order_id.company_id
        routes = self.search(
            [
                ("product_tmpl_id", "=", sale_line.product_id.product_tmpl_id.id),
                ("company_id", "=", company.id),
                ("active", "=", True),
            ]
        )
        return routes._filtered_access("read")


class TajTransportRouteLine(models.Model):
    """One ordered checkpoint definition inside a route template."""

    _name = "taj.transport.route.line"
    _description = "Route Template Checkpoint"
    _order = "sequence, id"
    _check_company_auto = True

    route_id = fields.Many2one(
        "taj.transport.route",
        string="Route Template",
        required=True,
        ondelete="cascade",
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="route_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    sequence = fields.Integer(
        default=10,
        help="Order of the checkpoint on the route.",
    )
    name = fields.Char(
        string="Checkpoint",
        required=True,
        help="Human readable name shown on the trip, e.g. "
        "'Kasumbalesa border' or 'Offloading at customer site'.",
    )
    milestone_type_id = fields.Many2one(
        "taj.transport.milestone.type",
        string="Checkpoint Type",
        ondelete="restrict",
        help="Optional classification maintained by the Transport Manager.",
    )
    requires_confirmation = fields.Boolean(
        string="Legacy Confirmation Requirement", readonly=True,
        help="Historical planning data only. Departure completes the checkpoint.",
    )
    planned_offset_hours = fields.Float(
        string="Planned Offset (Hours)",
        digits=(16, 2),
        help="Optional planning hint: hours after the trip's planned "
        "departure when this checkpoint is expected.",
    )
    planned_offset_days = fields.Float(
        string="Planned Offset (Days)",
        compute="_compute_planned_offset_days",
        inverse="_inverse_planned_offset_days",
        readonly=False,
        digits=(16, 6),
        help="Days after the trip's planned departure. One day equals 24 hours; "
             "editing either offset updates the other.",
    )
    latitude = fields.Float(
        digits=(10, 7),
        help="Optional coordinates kept for the later GPS/geofence phase. "
        "Nothing is detected automatically in this phase.",
    )
    longitude = fields.Float(digits=(10, 7))
    notes = fields.Text(string="Checkpoint Notes")

    @api.depends("planned_offset_hours")
    def _compute_planned_offset_days(self):
        for line in self:
            line.planned_offset_days = line.planned_offset_hours / 24.0

    @api.onchange("planned_offset_days")
    def _inverse_planned_offset_days(self):
        # Update Hours both while editing the form and when saving Days.
        for line in self:
            line.planned_offset_hours = line.planned_offset_days * 24.0

    @api.constrains("route_id", "name")
    def _check_unique_name_per_route(self):
        for line in self:
            duplicate = self.search_count(
                [
                    ("route_id", "=", line.route_id.id),
                    ("id", "!=", line.id),
                    ("name", "=ilike", (line.name or "").strip()),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _("This route template already has a checkpoint with this name.")
                )
