from odoo import fields, models


class FleetNavirecSyncLog(models.Model):
    _name = "fleet.navirec.sync.log"
    _description = "Navirec Synchronization Log"
    _order = "started_at desc, id desc"

    operation = fields.Selection(
        [
            ("vehicle_match", "Vehicle Matching"),
            ("state_sync", "Vehicle State Sync"),
        ],
        required=True,
        index=True,
    )
    status = fields.Selection(
        [
            ("success", "Success"),
            ("error", "Error"),
        ],
        required=True,
        index=True,
    )
    started_at = fields.Datetime(required=True, index=True)
    finished_at = fields.Datetime(required=True)
    records_updated = fields.Integer()
    message = fields.Text()
