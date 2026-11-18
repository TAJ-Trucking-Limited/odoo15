"""Warn about empty required shipment fields before confirming an order."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TajTransportSaleConfirmWizard(models.TransientModel):
    _name = "taj.transport.sale.confirm.wizard"
    _description = "Confirm Sales Order With Empty Shipment Fields"

    sale_order_ids = fields.Many2many(
        "sale.order",
        string="Sales Orders",
        required=True,
        readonly=True,
    )
    warning_message = fields.Text(compute="_compute_warning_message")

    @api.depends("sale_order_ids")
    def _compute_warning_message(self):
        for wizard in self:
            missing = (
                wizard.sale_order_ids.order_line._missing_required_shipment_fields()
            )
            wizard.warning_message = _(
                "These required shipment fields are still empty:\n\n%(missing)s\n\n"
                "Confirm the order anyway?",
                missing="\n".join("• %s" % message for message in missing),
            )

    def action_confirm_order(self):
        self.ensure_one()
        orders = self.sale_order_ids.exists()
        if not orders:
            raise UserError(_("The Sales Order no longer exists."))
        orders.action_confirm_with_missing_shipment_fields()
        return {"type": "ir.actions.act_window_close"}
