"""Warn before cancelling a Sales Order that still has transport trips."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TajTransportSaleCancelWizard(models.TransientModel):
    _name = "taj.transport.sale.cancel.wizard"
    _description = "Cancel Sales Order With Trips"

    sale_order_ids = fields.Many2many(
        "sale.order", string="Sales Orders", required=True, readonly=True,
    )
    warning_message = fields.Text(compute="_compute_warning_message")

    @api.depends(
        "sale_order_ids",
        "sale_order_ids.transport_trip_ids",
        "sale_order_ids.transport_trip_ids.state",
        "sale_order_ids.transport_trip_ids.name",
    )
    def _compute_warning_message(self):
        for wizard in self:
            trips = wizard.sale_order_ids.transport_trip_ids
            if not trips:
                wizard.warning_message = False
                continue
            active = trips.filtered(lambda t: t.state not in ("cancelled", "completed"))
            lines = [
                _("• %(trip)s (%(state)s)", trip=trip.display_name, state=trip.state)
                for trip in trips[:10]
            ]
            if len(trips) > 10:
                lines.append(_("• …and %(count)s more", count=len(trips) - 10))
            extra = ""
            if active:
                extra = _(
                    " %(count)s of them are still active (not cancelled/completed).",
                    count=len(active),
                )
            wizard.warning_message = _(
                "This Sales Order has %(total)s related transport trip(s).%(extra)s "
                "Cancelling the Sales Order does not cancel those trips.\n\n%(trips)s",
                total=len(trips),
                extra=extra,
                trips="\n".join(lines),
            )

    def action_confirm_cancellation(self):
        self.ensure_one()
        orders = self.sale_order_ids.exists()
        if not orders:
            raise UserError(_("The Sales Order no longer exists."))
        return orders.with_context(confirm_sale_cancel_with_trips=True).action_cancel()
