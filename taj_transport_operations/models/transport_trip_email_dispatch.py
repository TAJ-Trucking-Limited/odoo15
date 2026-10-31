"""Audit trail for transport trip email updates.

Dispatch rows are immutable audit records created exclusively by the trip
email dispatch engine. Ordinary users cannot create, modify or delete them.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class TajTransportTripEmailDispatch(models.Model):
    _name = "taj.transport.trip.email.dispatch"
    _description = "Trip Email Dispatch Audit"
    _order = "queued_at desc, id desc"
    _check_company_auto = True
    _rec_name = "queued_at"

    trip_id = fields.Many2one(
        "taj.transport.trip",
        string="Trip",
        required=True,
        ondelete="restrict",
        index=True,
        check_company=True,
        copy=False,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related="trip_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    dispatch_type = fields.Selection(
        [("manual", "Manual"), ("automatic", "Automatic")],
        string="Dispatch Type",
        required=True,
        readonly=True,
    )
    queued_at = fields.Datetime(
        string="Queued At",
        required=True,
        readonly=True,
    )
    requested_by_id = fields.Many2one(
        "res.users",
        string="Requested By",
        readonly=True,
        ondelete="restrict",
    )
    recipient_emails = fields.Text(
        string="Recipients",
        readonly=True,
        help="Snapshot of normalized recipient email addresses at queue time.",
    )
    mail_ids = fields.Many2many(
        "mail.mail",
        "taj_transport_trip_email_dispatch_mail_rel",
        "dispatch_id",
        "mail_id",
        string="Mail Records",
        readonly=True,
    )
    mail_count = fields.Integer(
        string="Mail Count",
        compute="_compute_mail_stats",
    )
    status = fields.Selection(
        [
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("partial_failure", "Partial Failure"),
            ("failed", "Failed"),
        ],
        string="Status",
        compute="_compute_mail_stats",
        store=False,
        help="Aggregated outgoing mail transport state.",
    )

    @api.depends("mail_ids", "mail_ids.state")
    def _compute_mail_stats(self):
        for dispatch in self:
            mails = dispatch.mail_ids
            dispatch.mail_count = len(mails)
            if not mails:
                dispatch.status = "queued"
                continue
            states = set(mails.mapped("state"))
            if states == {"sent"}:
                dispatch.status = "sent"
            elif states.issubset({"exception", "cancel"}):
                dispatch.status = "failed"
            elif any(s in ("exception", "cancel") for s in states):
                dispatch.status = "partial_failure"
            else:
                dispatch.status = "queued"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Trip email dispatch records are system-managed."))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Trip email dispatch audit records cannot be edited."))

    def unlink(self):
        raise AccessError(_("Trip email dispatch audit records cannot be deleted."))
