"""Explicit confirmation before discarding unrecorded, generated checkpoints."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TajTransportRegenerateMilestonesWizard(models.TransientModel):
    _name = "taj.transport.regenerate.milestones.wizard"
    _description = "Regenerate Trip Checkpoints"

    trip_id = fields.Many2one(
        "taj.transport.trip",
        string="Trip",
        required=True,
        readonly=True,
    )
    route_id = fields.Many2one(
        "taj.transport.route",
        related="trip_id.route_id",
        readonly=True,
    )
    generated_count = fields.Integer(
        string="Generated Checkpoints",
        compute="_compute_generated_count",
    )
    recorded_count = fields.Integer(
        string="Checkpoints With Recorded Progress",
        compute="_compute_generated_count",
    )
    note = fields.Text(
        string="Scope",
        compute="_compute_generated_count",
        readonly=True,
    )

    @api.depends("trip_id", "trip_id.milestone_ids")
    def _compute_generated_count(self):
        for wizard in self:
            milestones = wizard.trip_id.milestone_ids
            wizard.generated_count = len(milestones.filtered("template_line_id"))
            wizard.recorded_count = len(
                milestones.filtered(
                    lambda milestone: (
                        milestone.actual_arrival_at
                        or milestone.actual_departure_at
                        or milestone.confirmed_at
                    )
                )
            )
            wizard.note = _(
                "Checkpoints generated from this template without any "
                "recorded event are replaced by the current template. "
                "Manually added checkpoints, skipped checkpoints and every "
                "checkpoint with recorded progress are kept. Re-planning is "
                "only possible while the trip is in Draft."
            )

    def action_regenerate(self):
        self.ensure_one()
        trip = self.trip_id.exists()
        if not trip:
            raise UserError(_("The trip no longer exists."))
        if trip.state != "draft":
            raise UserError(
                _(
                    "Checkpoints can only be re-planned while the trip is in "
                    "Draft: %(trip)s is %(state)s, so its checkpoint plan is "
                    "already in operational use. Add a manual checkpoint for a "
                    "route variance instead."
                )
                % {
                    "trip": trip.name,
                    "state": trip._milestone_state_label(),
                }
            )
        if trip._has_recorded_milestone_progress():
            raise UserError(
                _(
                    "This trip already has recorded checkpoint progress. "
                    "Checkpoint history is never discarded: add the missing "
                    "checkpoint instead."
                )
            )
        trip._regenerate_milestones_from_route()
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
