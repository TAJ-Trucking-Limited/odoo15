"""Record the business start time separately from the lifecycle audit clock."""
from odoo import fields, models


class StartTripWizard(models.TransientModel):
    _name = "taj.transport.start.trip.wizard"
    _description = "Start Trip"

    trip_id = fields.Many2one("taj.transport.trip", required=True, readonly=True)
    actual_start_at = fields.Datetime(string="Actual Start", required=True, default=fields.Datetime.now)

    def action_start(self):
        self.ensure_one()
        self.trip_id.action_start_trip(actual_start_at=self.actual_start_at)
        return {"type": "ir.actions.act_window_close"}
