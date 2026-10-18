"""Add one checkpoint to a trip that the route template does not cover."""

from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportMilestoneAddWizard(models.TransientModel):
    _name = "taj.transport.milestone.add.wizard"
    _description = "Add Trip Checkpoint"

    trip_id = fields.Many2one(
        "taj.transport.trip",
        string="Trip",
        required=True,
        readonly=True,
    )
    name = fields.Char(
        string="Checkpoint",
        required=True,
        help="Operational name, e.g. a border or stop the route template "
        "does not contain.",
    )
    milestone_type_id = fields.Many2one(
        "taj.transport.milestone.type",
        string="Checkpoint Type",
        ondelete="restrict",
    )
    requires_confirmation = fields.Boolean(
        string="Requires Business Confirmation",
    )
    planned_offset_hours = fields.Float(
        string="Planned Offset (Hours)",
        digits=(16, 2),
    )
    notes = fields.Text(string="Checkpoint Notes")

    def action_add_checkpoint(self):
        self.ensure_one()
        trip = self.trip_id.exists()
        if not trip:
            raise UserError(_("The trip no longer exists."))
        milestone = trip._add_manual_milestone(
            {
                "name": self.name,
                "milestone_type_id": self.milestone_type_id.id or False,
                "requires_confirmation": self.requires_confirmation,
                "planned_offset_hours": self.planned_offset_hours or 0.0,
                "notes": self.notes,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
