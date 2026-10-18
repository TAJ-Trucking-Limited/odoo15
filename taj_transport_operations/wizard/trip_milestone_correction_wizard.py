"""Manager-only, reasoned correction of a recorded checkpoint timestamp."""

from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportMilestoneCorrectionWizard(models.TransientModel):
    _name = "taj.transport.milestone.correction.wizard"
    _description = "Correct Checkpoint Timestamp"

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
    field_name = fields.Selection(
        [
            ("actual_arrival_at", "Actual Arrival"),
            ("actual_departure_at", "Actual Departure"),
        ],
        string="Value To Correct",
        required=True,
        default="actual_arrival_at",
    )
    current_value = fields.Datetime(
        string="Recorded Value",
        compute="_compute_current_value",
        readonly=True,
    )
    new_value = fields.Datetime(
        string="Corrected Value",
        help="Leave empty to clear the recorded value. The reason below is "
        "still required, and the before/after values are kept in the "
        "checkpoint audit trail.",
    )
    reason = fields.Char(
        string="Correction Reason",
        required=True,
        help="Mandatory. Stored permanently with the before/after values, "
        "the author and the correction time.",
    )
    note = fields.Text(string="Correction Note")

    def _compute_current_value(self):
        for wizard in self:
            wizard.current_value = (
                wizard.milestone_id[wizard.field_name]
                if wizard.milestone_id and wizard.field_name
                else False
            )

    def action_apply_correction(self):
        self.ensure_one()
        milestone = self.milestone_id.exists()
        if not milestone:
            raise UserError(_("The checkpoint no longer exists."))
        milestone._correct_timestamp(
            self.field_name,
            self.new_value,
            self.reason,
            self.note,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": milestone.trip_id.id,
            "view_mode": "form",
            "target": "current",
        }
