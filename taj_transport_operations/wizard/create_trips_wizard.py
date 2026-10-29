"""Explicit selection prevents other sales services from becoming trips."""
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class TajTransportTripCreateWizard(models.TransientModel):
    _name = "taj.transport.trip.create.wizard"
    _description = "Select Sales Order Lines for Transport Trips"

    sale_order_id = fields.Many2one(
        "sale.order", required=True, readonly=True,
    )
    no_vehicle_on_order = fields.Boolean(
        compute="_compute_no_vehicle_on_order",
    )
    selection_ids = fields.One2many(
        "taj.transport.trip.create.wizard.line",
        "wizard_id",
        string="Transport Lines",
        help="Select only transport lines. Lines already assigned to a truck "
             "are suggested. Every new trip needs a truck and a route template "
             "linked to that line's product.",
    )
    company_id = fields.Many2one(
        "res.company", related="sale_order_id.company_id", readonly=True,
    )

    @api.depends(
        "sale_order_id.order_line.vehicle_id",
        "sale_order_id.order_line.product_id",
        "sale_order_id.order_line.display_type",
    )
    def _compute_no_vehicle_on_order(self):
        for wizard in self:
            wizard.no_vehicle_on_order = bool(wizard.sale_order_id) and not any(
                line.vehicle_id
                for line in wizard.sale_order_id.order_line
                if line.display_type in (False, "product") and line.product_id
            )

    @api.model
    def _wizard_line_vals_for_sale_line(self, sale_line):
        vals = {"sale_line_id": sale_line.id}
        if sale_line.transport_trip_ids:
            return vals
        routes = self.env["taj.transport.route"]._matching_for_sale_line(
            sale_line
        )
        if len(routes) == 1:
            vals["route_id"] = routes.id
        return vals

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        order_id = values.get("sale_order_id") or self.env.context.get(
            "default_sale_order_id"
        )
        if order_id and "selection_ids" in fields_list:
            order = self.env["sale.order"].browse(order_id).exists()
            if order:
                suggested = order.order_line.filtered(
                    lambda line: (
                        line.display_type in (False, "product")
                        and line.product_id
                        and line.vehicle_id
                        and not line.transport_trip_ids
                    )
                )
                values["selection_ids"] = [
                    Command.create(self._wizard_line_vals_for_sale_line(line))
                    for line in suggested
                ]
        return values

    def action_create_trips(self):
        self.ensure_one()
        order = self.sale_order_id
        if order.state not in ("sale", "done"):
            raise UserError(_(
                "Confirm the sales order before creating transport trips."
            ))
        if not self.selection_ids:
            raise UserError(_("Select at least one transport sales line."))

        Trip = self.env["taj.transport.trip"]
        Route = self.env["taj.transport.route"]
        # Prevalidate every row before any create: duplicates, foreign lines,
        # missing/forged templates and cargo defaults all fail the batch.
        create_vals = []
        open_trips = Trip
        seen_line_ids = set()
        for wizard_line in self.selection_ids:
            line = wizard_line.sale_line_id
            if line.id in seen_line_ids:
                raise UserError(_(
                    "Sales line %(line)s is selected more than once. "
                    "Remove the duplicate row before creating trips."
                ) % {"line": line.display_name})
            seen_line_ids.add(line.id)
            if line.order_id != order:
                raise UserError(_(
                    "All selected lines must belong to the chosen sales order."
                ))
            Trip._validate_sale_line(line)
            existing = line.transport_trip_ids[:1]
            if existing:
                open_trips |= existing
                continue
            matches = Route._matching_for_sale_line(line)
            if not matches:
                raise UserError(_(
                    "No active route template is linked to product "
                    "%(product)s (sales line %(line)s). Ask a Transport "
                    "Manager to link a route template to this product, or "
                    "remove the line from this dialog."
                ) % {
                    "product": line.product_id.display_name,
                    "line": line.display_name,
                })
            selected = wizard_line.route_id or (
                matches if len(matches) == 1 else Route
            )
            if not selected and len(matches) > 1:
                raise UserError(_(
                    "Several route templates match product %(product)s "
                    "(sales line %(line)s). Choose one template for that line."
                ) % {
                    "product": line.product_id.display_name,
                    "line": line.display_name,
                })
            route = Trip._require_matching_route_for_sale_line(line, selected)
            if route not in matches:
                raise UserError(_(
                    "Route template %(route)s is not a matching active "
                    "template for product %(product)s."
                ) % {
                    "route": route.display_name,
                    "product": line.product_id.display_name,
                })
            create_vals.append({
                "sale_line_id": line.id,
                "route_id": route.id,
            })

        trips = open_trips
        if create_vals:
            trips |= Trip.create(create_vals)
        if len(trips) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Transport Trip"),
                "res_model": "taj.transport.trip",
                "res_id": trips.id,
                "view_mode": "form",
                "target": "current",
            }
        action = self.env.ref(
            "taj_transport_operations.action_transport_trip"
        ).read()[0]
        action["domain"] = [("id", "in", trips.ids)]
        return action


class TajTransportTripCreateWizardLine(models.TransientModel):
    _name = "taj.transport.trip.create.wizard.line"
    _description = "Sales Line and Route Template for Trip Creation"

    wizard_id = fields.Many2one(
        "taj.transport.trip.create.wizard",
        required=True,
        ondelete="cascade",
    )
    sale_order_id = fields.Many2one(
        related="wizard_id.sale_order_id", readonly=True,
    )
    sale_line_id = fields.Many2one(
        "sale.order.line",
        string="Sales Line",
        required=True,
        domain="[('order_id', '=', sale_order_id), "
               "('display_type', 'in', [False, 'product']), "
               "('vehicle_id', '!=', False)]",
    )
    company_id = fields.Many2one(
        related="wizard_id.company_id", readonly=True,
    )
    # Computed (not related) so inline One2many new rows refresh product,
    # domain helpers and route defaulting as soon as sale_line_id is set.
    product_id = fields.Many2one(
        "product.product", string="Product",
        compute="_compute_route_helpers", readonly=True,
    )
    product_tmpl_id = fields.Many2one(
        "product.template", string="Product Template",
        compute="_compute_route_helpers", readonly=True,
    )
    existing_trip_id = fields.Many2one(
        "taj.transport.trip",
        string="Existing Trip",
        compute="_compute_route_helpers",
    )
    needs_route = fields.Boolean(compute="_compute_route_helpers")
    matching_route_count = fields.Integer(compute="_compute_route_helpers")
    route_id = fields.Many2one(
        "taj.transport.route",
        string="Route Template",
        domain="[('active', '=', True), ('company_id', '=', company_id), "
               "('product_tmpl_id', '=', product_tmpl_id)]",
        help="Required for each new trip. Auto-selected when exactly one "
             "active template matches the line's product. Existing trips "
             "are opened as-is and ignore this field.",
    )

    @api.depends(
        "sale_line_id",
        "sale_line_id.transport_trip_ids",
        "sale_line_id.product_id",
        "sale_line_id.product_id.product_tmpl_id",
        "company_id",
    )
    def _compute_route_helpers(self):
        Route = self.env["taj.transport.route"]
        for line in self:
            product = line.sale_line_id.product_id
            line.product_id = product
            line.product_tmpl_id = product.product_tmpl_id
            existing = line.sale_line_id.transport_trip_ids[:1]
            line.existing_trip_id = existing
            line.needs_route = bool(line.sale_line_id) and not existing
            matches = (
                Route._matching_for_sale_line(line.sale_line_id)
                if line.needs_route else Route
            )
            line.matching_route_count = len(matches)

    @api.onchange("sale_line_id")
    def _onchange_sale_line_id(self):
        for line in self:
            if not line.sale_line_id or line.sale_line_id.transport_trip_ids:
                line.route_id = False
                continue
            routes = self.env["taj.transport.route"]._matching_for_sale_line(
                line.sale_line_id
            )
            line.route_id = routes.id if len(routes) == 1 else False
