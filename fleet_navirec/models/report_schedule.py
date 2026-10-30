from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class FleetNavirecReportSlot(models.Model):
    _name = "fleet.navirec.report.slot"
    _description = "Navirec Client Report Send Time"
    _order = "time"

    subscription_id = fields.Many2one(
        "fleet.navirec.report.subscription",
        required=True,
        ondelete="cascade",
    )
    time = fields.Char(
        required=True,
        help="Local client time in HH:MM format.",
    )

    @api.constrains("time", "subscription_id")
    def _check_time(self):
        for subscription in self.subscription_id:
            parsed = [
                subscription._parse_send_time(slot.time)
                for slot in subscription.send_time_ids
            ]
            if len(parsed) != len(set(parsed)):
                raise ValidationError(_("The daily send times must be different."))

    @api.model_create_multi
    def create(self, vals_list):
        slots = super().create(vals_list)
        slots.subscription_id._refresh_next_send_at()
        return slots

    def write(self, vals):
        result = super().write(vals)
        self.subscription_id._refresh_next_send_at()
        return result

    def unlink(self):
        subscriptions = self.subscription_id
        result = super().unlink()
        # The parent may be gone already when a subscription cascade-deletes
        # its own slots; never re-create/refresh a deleted record.
        subscriptions.exists()._refresh_next_send_at()
        return result
