"""Explicit selection prevents other sales services from becoming trips."""
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class TajTransportTripCreateWizard(models.TransientModel):
    _name = "taj.transport.trip.create.wizard"
    _description = "Select Sales Order Lines for Transport Trips"

    sale_order_id = fields.Many2one(
        "sale.order", required=True, readonly=True,
    )
    line_ids = fields.Many2many(
        "sale.order.line", string="Transport Lines",
        domain="[('order_id', '=', sale_order_id), ('display_type', '=', 'product')]",
        help="Select only transport lines. Lines already assigned to a truck "
             "are suggested; select lines without a truck manually.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        order_id = values.get("sale_order_id") or self.env.context.get(
            "default_sale_order_id"
        )
        if order_id and "line_ids" in fields_list:
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
                values["line_ids"] = [Command.set(suggested.ids)]
        return values

    def action_create_trips(self):
        self.ensure_one()
        order = self.sale_order_id
        if order.state not in ("sale", "done"):
            raise UserError(_(
                "Confirm the sales order before creating transport trips."
            ))
        if not self.line_ids:
            raise UserError(_("Select at least one transport sales line."))
        trips = self.env["taj.transport.trip"]
        for line in self.line_ids:
            if line.order_id != order:
                raise UserError(_(
                    "All selected lines must belong to the chosen sales order."
                ))
            trips._validate_sale_line(line)
            existing = line.transport_trip_ids[:1]
            trips |= existing or trips.create({"sale_line_id": line.id})
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
