"""Ambiguous product-linked route templates for the sales line Trip button."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TajTransportTripRouteChooserWizard(models.TransientModel):
    _name = "taj.transport.trip.route.chooser.wizard"
    _description = "Choose Route Template for Sales Line Trip"

    sale_line_id = fields.Many2one(
        "sale.order.line", required=True, readonly=True,
    )
    company_id = fields.Many2one(
        related="sale_line_id.company_id", readonly=True,
    )
    product_tmpl_id = fields.Many2one(
        related="sale_line_id.product_id.product_tmpl_id", readonly=True,
    )
    route_id = fields.Many2one(
        "taj.transport.route",
        string="Route Template",
        required=True,
        domain="[('active', '=', True), ('company_id', '=', company_id), "
               "('product_tmpl_id', '=', product_tmpl_id)]",
        help="Several active templates match this sales line's product. "
             "Choose which one to copy onto the new trip.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        line_id = values.get("sale_line_id") or self.env.context.get(
            "default_sale_line_id"
        )
        if line_id and "route_id" in fields_list:
            line = self.env["sale.order.line"].browse(line_id).exists()
            matches = self.env["taj.transport.route"]._matching_for_sale_line(
                line
            )
            if len(matches) == 1:
                values["route_id"] = matches.id
        return values

    def action_create_trip(self):
        self.ensure_one()
        line = self.sale_line_id
        Trip = self.env["taj.transport.trip"]
        existing = line.transport_trip_ids[:1]
        if existing:
            trip = existing
        else:
            Trip._validate_sale_line(line)
            route = Trip._require_matching_route_for_sale_line(
                line, self.route_id
            )
            matches = self.env["taj.transport.route"]._matching_for_sale_line(
                line
            )
            if route not in matches:
                raise UserError(_(
                    "Route template %(route)s is not a matching active "
                    "template for product %(product)s."
                ) % {
                    "route": route.display_name,
                    "product": line.product_id.display_name,
                })
            trip = Trip.create({
                "sale_line_id": line.id,
                "route_id": route.id,
            })
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
