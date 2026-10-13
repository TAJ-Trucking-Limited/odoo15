"""Mandatory cancellation reason for the audited trip lifecycle."""
from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportCancelTripWizard(models.TransientModel):
    _name = "taj.transport.cancel.trip.wizard"
    _description = "Cancel Transport Trip"

    trip_id = fields.Many2one(
        "taj.transport.trip", string="Trip", required=True, readonly=True,
    )
    reason = fields.Text(string="Cancellation Reason", required=True)

    def action_confirm_cancellation(self):
        self.ensure_one()
        trip = self.trip_id.exists()
        if not trip:
            raise UserError(_("The trip no longer exists."))
        trip._cancel_with_reason(self.reason)
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
