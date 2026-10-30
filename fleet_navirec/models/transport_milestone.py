"""Expose the checkpoint truck's live Navirec state on milestone records.

Milestone rows are historical snapshots, so the truck position is a read-only,
non-stored view of the vehicle's latest Navirec state. It is labelled as the
*current* position in the UI and is never written back to the milestone.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError


class TajTransportMilestone(models.Model):
    _inherit = "taj.transport.milestone"

    navirec_has_position = fields.Boolean(
        related="vehicle_id.navirec_has_position",
        string="Truck Has Navirec Position",
        readonly=True,
    )
    navirec_position_display = fields.Char(
        related="vehicle_id.navirec_position_display",
        string="Current Truck Position",
        readonly=True,
        help="Live Navirec position of this row's truck, not the position "
        "where the checkpoint was recorded.",
    )
    navirec_last_state_time = fields.Datetime(
        related="vehicle_id.navirec_last_state_time",
        string="Last Truck GPS",
        readonly=True,
        help="Time of the latest Navirec GPS fix for this row's truck.",
    )
    navirec_is_stale = fields.Boolean(
        related="vehicle_id.navirec_is_stale",
        string="Truck GPS Stale",
        readonly=True,
    )

    def action_view_current_truck_on_map(self):
        """Open the row's truck at its latest Navirec coordinates."""
        self.ensure_one()
        if not self.vehicle_id:
            raise UserError(_("This milestone has no truck to locate."))
        return self.vehicle_id.action_view_on_map()
