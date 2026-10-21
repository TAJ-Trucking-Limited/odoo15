"""Manual checkpoint events: arrival, departure and manager skip."""

from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportMilestoneActionWizard(models.TransientModel):
    _name = "taj.transport.milestone.action.wizard"
    _description = "Record Trip Checkpoint Event"

    milestone_id = fields.Many2one(
        "taj.transport.milestone",
        string="Checkpoint",
        required=True,
        readonly=True,
    )
    trip_id = fields.Many2one(
        "taj.transport.trip",
        related="milestone_id.trip_id",
        readonly=True,
    )
    action_type = fields.Selection(
        [
            ("arrival", "Record Arrival"),
            ("departure", "Record Departure"),
            ("skip", "Skip Checkpoint"),
        ],
        string="Action",
        required=True,
        readonly=True,
    )
    event_at = fields.Datetime(
        string="Event Time",
        default=fields.Datetime.now,
        help="The operational time of the event, not the time of data entry. "
        "It cannot be in the future.",
    )
    note = fields.Text(string="Note")
    reason = fields.Char(
        string="Reason",
        help="Required when skipping a checkpoint.",
    )

    def action_confirm(self):
        self.ensure_one()
        milestone = self.milestone_id.exists()
        if not milestone:
            raise UserError(_("The checkpoint no longer exists."))
        if self.action_type == "arrival":
            milestone._record_arrival(self.event_at, self.note)
        elif self.action_type == "departure":
            milestone._record_departure(self.event_at, self.note)
        elif self.action_type == "skip":
            milestone._skip_checkpoint(self.reason)
        else:
            raise UserError(_("Unsupported checkpoint action."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": milestone.trip_id.id,
            "view_mode": "form",
            "target": "current",
        }
