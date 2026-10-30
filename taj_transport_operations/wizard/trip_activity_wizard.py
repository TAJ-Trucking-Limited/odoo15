"""A submitted text update becomes one immutable trip activity."""
from odoo import fields, models


class TripActivityWizard(models.TransientModel):
    _name = "taj.transport.trip.activity.wizard"
    _description = "Record Trip Activity"

    trip_id = fields.Many2one("taj.transport.trip", required=True, readonly=True)
    description = fields.Text(string="Activity", required=True)

    def action_submit(self):
        self.ensure_one()
        self.trip_id._record_trip_activity(self.description)
        return {"type": "ir.actions.act_window_close"}
