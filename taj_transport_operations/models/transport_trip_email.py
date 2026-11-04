"""Trip email updates: recipient configuration, periodic dispatch, audit trail."""

from datetime import timedelta
import logging
import math
import pytz

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)

_SCHEDULE_WRITE_TOKEN = object()


class TajTransportTrip(models.Model):
    _inherit = "taj.transport.trip"

    commercial_partner_id = fields.Many2one(
        "res.partner",
        string="Customer Commercial Entity",
        related="partner_id.commercial_partner_id",
        store=True,
        readonly=True,
        index=True,
    )

    email_notification_enabled = fields.Boolean(
        string="Enable Automatic Updates",
        default=False,
        copy=False,
        help="Enable automated periodic status email updates while this trip is In Progress.",
    )
    email_partner_ids = fields.Many2many(
        "res.partner",
        "taj_transport_trip_email_partner_rel",
        "trip_id",
        "partner_id",
        string="Customer Contacts",
        copy=False,
        help="Explicit client contacts to receive trip status updates.",
    )
    email_extra_recipients = fields.Char(
        string="Additional Emails",
        copy=False,
        help="Comma-separated additional email addresses (e.g. ops@client.com, tracking@client.com).",
    )
    email_interval_hours = fields.Float(
        string="Update Interval (Hours)",
        default=4.0,
        copy=False,
        help="Hours between automated status emails while In Progress. Minimum 0.25 hours.",
    )
    email_next_send_at = fields.Datetime(
        string="Next Scheduled Update",
        readonly=True,
        copy=False,
        index=True,
        help="System-managed timestamp when the next automated email will be queued.",
    )
    email_last_queued_at = fields.Datetime(
        string="Last Queued",
        readonly=True,
        copy=False,
        help="Timestamp when the last status email was queued into Odoo's mail system.",
    )
    email_failure_count = fields.Integer(
        string="Email Failure Count",
        default=0,
        readonly=True,
        copy=False,
        help="Consecutive automated dispatch failures. Resets upon successful dispatch or configuration change.",
    )
    email_last_error = fields.Text(
        string="Last Email Error",
        readonly=True,
        copy=False,
        help="Diagnostic message from the most recent automated email dispatch failure.",
    )
    email_last_failed_at = fields.Datetime(
        string="Last Failed At",
        readonly=True,
        copy=False,
    )
    email_dispatch_ids = fields.One2many(
        "taj.transport.trip.email.dispatch",
        "trip_id",
        string="Email Dispatches",
        readonly=True,
        copy=False,
    )

    # Live position preview on the trip form
    email_position_preview = fields.Char(
        string="Position Preview",
        compute="_compute_email_position_preview",
    )
    email_position_source = fields.Char(
        string="Position Source",
        compute="_compute_email_position_preview",
    )
    email_position_updated_at = fields.Datetime(
        string="Position Recorded At",
        compute="_compute_email_position_preview",
    )
    email_position_map_url = fields.Char(
        string="Map Link",
        compute="_compute_email_position_preview",
    )
    email_position_notice = fields.Char(
        string="Position Notice",
        compute="_compute_email_position_preview",
    )

    _email_system_fields = frozenset({
        "email_next_send_at",
        "email_last_queued_at",
        "email_failure_count",
        "email_last_error",
        "email_last_failed_at",
    })

    @api.constrains("email_interval_hours")
    def _check_email_interval(self):
        for trip in self:
            hours = trip.email_interval_hours
            if not math.isfinite(hours) or hours < 0.25:
                raise ValidationError(_("Update interval must be a valid number of at least 0.25 hours (15 minutes)."))

    @api.constrains("email_notification_enabled", "email_partner_ids", "email_extra_recipients", "partner_id")
    def _check_email_recipients_constraint(self):
        for trip in self:
            trip._validate_recipient_settings()
            if trip.email_notification_enabled:
                recipients = trip._get_email_recipients(raise_if_empty=False)
                if not recipients:
                    raise ValidationError(_("Configure at least one valid recipient before enabling automated email updates."))

    def _validate_recipient_settings(self):
        """Validate recipient rules server-side to prevent invalid entries or RPC bypass.

        Ensures:
        - All selected contacts belong to the trip customer's commercial entity.
        - All selected contacts have a valid non-empty normalized email.
        - Additional emails contain no control characters and are each valid normalized addresses.
        """
        self.ensure_one()
        commercial_partner = self.partner_id.commercial_partner_id if self.partner_id else False
        for partner in self.email_partner_ids:
            if commercial_partner and partner.commercial_partner_id != commercial_partner:
                raise ValidationError(_(
                    "Contact '%s' does not belong to the trip customer '%s'."
                ) % (partner.display_name, self.partner_id.display_name))
            if not partner.email or not email_normalize(partner.email):
                raise ValidationError(_(
                    "Selected contact '%s' does not have a valid email address."
                ) % partner.display_name)

        extras_raw = self.email_extra_recipients or ""
        if "\r" in extras_raw or "\n" in extras_raw:
            raise ValidationError(_("Additional email addresses must not contain line breaks or control characters."))
        if extras_raw.strip():
            for raw in extras_raw.split(","):
                clean = raw.strip()
                if not clean:
                    continue
                if not email_normalize(clean):
                    raise ValidationError(_("Invalid additional email address: '%s'.") % clean)

    def _get_email_recipients(self, raise_if_empty=True):
        """Return a deduplicated list of normalized recipient email addresses.

        Strictly revalidates all configured contacts and extras before dispatch.
        Rejects immediately if any configured contact or extra address is invalid.
        """
        self.ensure_one()
        self._validate_recipient_settings()

        recipients = []
        seen = set()

        for partner in self.email_partner_ids:
            normalized = email_normalize(partner.email)
            if not normalized:
                raise ValidationError(_(
                    "Selected contact '%s' does not have a valid email address."
                ) % partner.display_name)
            if normalized.lower() not in seen:
                seen.add(normalized.lower())
                recipients.append(normalized)

        if self.email_extra_recipients:
            for raw in self.email_extra_recipients.split(","):
                clean = raw.strip()
                if not clean:
                    continue
                normalized = email_normalize(clean)
                if not normalized:
                    raise ValidationError(_("Invalid additional email address: '%s'.") % clean)
                if normalized.lower() not in seen:
                    seen.add(normalized.lower())
                    recipients.append(normalized)

        if not recipients and raise_if_empty:
            raise ValidationError(_("No valid email recipients configured for transport trip %s.") % self.name)
        return recipients

    @api.model_create_multi
    def create(self, vals_list):
        self._check_email_system_field_protection(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._check_email_system_field_protection([vals])
        res = super().write(vals)
        if self.env.context.get("_taj_internal_schedule_token") is not _SCHEDULE_WRITE_TOKEN:
            self._handle_email_schedule_lifecycle_transitions(vals)
        return res

    @api.model
    def _check_email_system_field_protection(self, vals_list):
        if self.env.context.get("_taj_internal_schedule_token") is _SCHEDULE_WRITE_TOKEN:
            return
        for vals in vals_list:
            if self._email_system_fields.intersection(vals):
                raise AccessError(_("Email schedule and failure fields are system-managed and cannot be modified directly."))

    def _write_email_schedule(self, vals):
        """Safe internal updater for system-managed email scheduling fields."""
        return super(TajTransportTrip, self.sudo().with_context(
            _taj_internal_schedule_token=_SCHEDULE_WRITE_TOKEN
        )).write(vals)

    def _handle_email_schedule_lifecycle_transitions(self, vals):
        """Update email schedule clocks upon state, enablement, or interval changes."""
        now = fields.Datetime.now()
        for trip in self:
            updates = {}
            new_state = vals.get("state", trip.state)
            new_enabled = vals.get("email_notification_enabled", trip.email_notification_enabled)
            new_interval = vals.get("email_interval_hours", trip.email_interval_hours)

            state_changed = "state" in vals
            enabled_changed = "email_notification_enabled" in vals
            interval_changed = "email_interval_hours" in vals
            recipients_changed = "email_partner_ids" in vals or "email_extra_recipients" in vals

            # Reset error tracking if recipients or enablement are updated
            if recipients_changed or (enabled_changed and new_enabled):
                if trip.email_failure_count > 0:
                    updates["email_failure_count"] = 0
                    updates["email_last_error"] = False
                    updates["email_last_failed_at"] = False

            if new_state in ("completed", "cancelled"):
                if trip.email_next_send_at:
                    updates["email_next_send_at"] = False
            elif not new_enabled:
                if trip.email_next_send_at:
                    updates["email_next_send_at"] = False
            elif new_state == "in_progress":
                if (state_changed and new_state == "in_progress") or (enabled_changed and new_enabled) or interval_changed or (recipients_changed and not trip.email_next_send_at):
                    updates["email_next_send_at"] = now + timedelta(hours=new_interval)
            elif new_state in ("draft", "ready"):
                if trip.email_next_send_at:
                    updates["email_next_send_at"] = False

            if updates:
                trip._write_email_schedule(updates)

    def action_start_trip(self, actual_start_at=None):
        res = super().action_start_trip(actual_start_at=actual_start_at)
        now = fields.Datetime.now()
        for trip in self:
            if trip.email_notification_enabled:
                trip._write_email_schedule({
                    "email_next_send_at": now + timedelta(hours=trip.email_interval_hours)
                })
        return res

    def action_complete_trip(self):
        res = super().action_complete_trip()
        for trip in self:
            if trip.email_next_send_at:
                trip._write_email_schedule({"email_next_send_at": False})
        return res

    def _cancel_with_reason(self, reason):
        res = super()._cancel_with_reason(reason)
        for trip in self:
            if trip.email_next_send_at:
                trip._write_email_schedule({"email_next_send_at": False})
        return res

    def _get_email_update_position_data(self):
        """Return structured position dictionary.

        Base implementation falls back to the latest confirmed milestone arrival/departure,
        or the trip's planned origin. Overridden in fleet_navirec when live GPS is available.
        """
        self.ensure_one()
        # Find latest actual milestone event
        milestones = self.milestone_ids.filtered(
            lambda m: m.state not in ("skipped", "pending") and (m.actual_departure_at or m.actual_arrival_at)
        )
        if milestones:
            # Sort by actual departure or arrival time descending
            latest = milestones.sorted(
                key=lambda m: (m.actual_departure_at or m.actual_arrival_at, m.sequence, m.id),
                reverse=True,
            )[0]
            if latest.actual_departure_at:
                event_type = _("Departed")
                event_time = latest.actual_departure_at
            else:
                event_type = _("Arrived at")
                event_time = latest.actual_arrival_at
            return {
                "label": f"{event_type} {latest.name}",
                "source": "milestone",
                "updated_at": event_time,
                "latitude": latest.latitude,
                "longitude": latest.longitude,
                "map_url": (
                    "https://www.google.com/maps?q=%s,%s" % (latest.latitude, latest.longitude)
                    # Optional Float fields use zero for missing coordinates.
                    # Suppress incomplete/ambiguous historical coordinate pairs.
                    if (latest.latitude and latest.longitude
                        and -90 <= latest.latitude <= 90 and -180 <= latest.longitude <= 180) else False
                ),
                "is_stale": False,
                "notice": _("Last recorded checkpoint (not a live GPS fix)"),
            }

        # Planned origin fallback
        origin = (self.origin_location or "").strip()
        return {
            "label": origin if origin else _("Origin location not specified"),
            "source": "planned",
            "updated_at": False,
            "latitude": False,
            "longitude": False,
            "map_url": False,
            "is_stale": False,
            "notice": _("Planned location, not a confirmed checkpoint or live position"),
        }

    def _compute_email_position_preview(self):
        for trip in self:
            pos = trip._get_email_update_position_data()
            trip.email_position_preview = pos.get("label") or ""
            trip.email_position_source = (pos.get("source") or "").title()
            trip.email_position_updated_at = pos.get("updated_at") or False
            trip.email_position_map_url = pos.get("map_url") or ""
            trip.email_position_notice = pos.get("notice") or ""

    def _get_trip_email_subject(self):
        self.ensure_one()
        status_label = dict(self._fields["state"].selection).get(self.state, self.state.title())
        return _("Trip Status Update: %s - %s") % (self.name, status_label)

    def _get_trip_email_timezone(self):
        """Resolve a single consistent timezone string for email formatting and footer labelling."""
        self.ensure_one()
        return self.company_id.partner_id.tz or self.env.user.tz or "UTC"

    def _format_datetime_for_email(self, dt, tz_name=None):
        """Format datetime consistently using the specified or resolved email timezone."""
        if not dt:
            return ""
        tz_str = tz_name or self._get_trip_email_timezone()
        try:
            target_tz = pytz.timezone(tz_str)
        except Exception:
            target_tz = pytz.UTC
            tz_str = "UTC"

        if dt.tzinfo is None:
            utc_dt = pytz.UTC.localize(dt)
        else:
            utc_dt = dt.astimezone(pytz.UTC)

        local_dt = utc_dt.astimezone(target_tz)
        return f"{local_dt.strftime('%Y-%m-%d %H:%M')} ({tz_str})"

    def _render_trip_email_html(self):
        """Construct secure and styled HTML email body for trip status updates."""
        self.ensure_one()
        pos = self._get_email_update_position_data()
        status_label = dict(self._fields["state"].selection).get(self.state, self.state.title())
        truck_name = self.current_vehicle_id.license_plate or self.current_vehicle_id.name or _("Unassigned")
        tz_str = self._get_trip_email_timezone()

        # Cargo display: show both cargo type and description when available
        cargo_parts = []
        if self.cargo_type_id and self.cargo_type_id.name:
            cargo_parts.append(self.cargo_type_id.name)
        if self.cargo_description and self.cargo_description.strip():
            cargo_parts.append(self.cargo_description.strip())
        cargo_display = " - ".join(cargo_parts)

        # Position section
        pos_label = escape(pos.get("label") or _("Unavailable"))
        pos_time = self._format_datetime_for_email(pos.get("updated_at"), tz_name=tz_str)
        pos_notice = escape(pos.get("notice") or "")
        map_url = pos.get("map_url")

        map_link_html = Markup("")
        if map_url and isinstance(map_url, str) and (map_url.startswith("https://") or map_url.startswith("http://")):
            map_link_html = Markup(" | <a href='{url}' target='_blank' style='color:#017e84;text-decoration:underline;'>{link_text}</a>").format(
                url=escape(map_url),
                link_text=_("View on Google Maps"),
            )

        pos_time_html = Markup("")
        if pos_time:
            pos_time_html = Markup("<br/><small style='color:#6c757d;'>{label}: {time}</small>").format(
                label=_("Recorded At"),
                time=pos_time,
            )

        pos_notice_html = Markup("")
        if pos_notice:
            pos_notice_html = Markup("<br/><small style='color:#856404;font-style:italic;'>{notice}</small>").format(
                notice=pos_notice,
            )

        # Milestone rows
        milestone_rows = []
        for ms in self.milestone_ids.sorted(key=lambda m: (m.sequence, m.id)):
            ms_state_label = dict(ms._fields["state"].selection).get(ms.state, ms.state.title())
            milestone_rows.append(Markup(
                "<tr>"
                "<td style='padding:8px;border-bottom:1px solid #dee2e6;'>{name}</td>"
                "<td style='padding:8px;border-bottom:1px solid #dee2e6;'>{planned}</td>"
                "<td style='padding:8px;border-bottom:1px solid #dee2e6;'>{arr}</td>"
                "<td style='padding:8px;border-bottom:1px solid #dee2e6;'>{dep}</td>"
                "<td style='padding:8px;border-bottom:1px solid #dee2e6;'><span style='display:inline-block;padding:2px 6px;border-radius:4px;font-size:12px;background:#e9ecef;'>{state}</span></td>"
                "</tr>"
            ).format(
                name=escape(ms.name or ""),
                planned=self._format_datetime_for_email(ms.planned_arrival_at, tz_name=tz_str),
                arr=self._format_datetime_for_email(ms.actual_arrival_at, tz_name=tz_str),
                dep=self._format_datetime_for_email(ms.actual_departure_at, tz_name=tz_str),
                state=escape(ms_state_label),
            ))

        milestones_table_html = Markup("")
        if milestone_rows:
            milestones_table_html = Markup(
                "<h3 style='color:#333;margin-top:25px;margin-bottom:10px;font-size:16px;'>{title}</h3>"
                "<table style='width:100%;border-collapse:collapse;font-size:13px;text-align:left;'>"
                "<thead>"
                "<tr style='background:#f8f9fa;color:#495057;'>"
                "<th style='padding:8px;border-bottom:2px solid #dee2e6;'>{th_name}</th>"
                "<th style='padding:8px;border-bottom:2px solid #dee2e6;'>{th_planned}</th>"
                "<th style='padding:8px;border-bottom:2px solid #dee2e6;'>{th_arr}</th>"
                "<th style='padding:8px;border-bottom:2px solid #dee2e6;'>{th_dep}</th>"
                "<th style='padding:8px;border-bottom:2px solid #dee2e6;'>{th_state}</th>"
                "</tr>"
                "</thead>"
                "<tbody>{rows}</tbody>"
                "</table>"
            ).format(
                title=_("Trip Checkpoints & Milestones"),
                th_name=_("Checkpoint"),
                th_planned=_("Planned Arrival"),
                th_arr=_("Actual Arrival"),
                th_dep=_("Actual Departure"),
                th_state=_("Status"),
                rows=Markup("").join(milestone_rows),
            )

        html = Markup(
            "<div style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,Helvetica,Arial,sans-serif;max-width:680px;margin:0 auto;color:#212529;padding:20px;border:1px solid #e9ecef;border-radius:8px;'>"
            "<div style='border-bottom:2px solid #017e84;padding-bottom:12px;margin-bottom:20px;'>"
            "<h2 style='color:#017e84;margin:0;font-size:20px;'>{company_name}</h2>"
            "<p style='margin:4px 0 0;color:#6c757d;font-size:14px;'>{header_subtitle}</p>"
            "</div>"

            "<div style='background:#f8f9fa;border-left:4px solid #017e84;padding:12px 16px;margin-bottom:20px;'>"
            "<table style='width:100%;font-size:14px;border-collapse:collapse;'>"
            "<tr><td style='padding:4px 0;width:35%;color:#6c757d;'><strong>{lbl_trip}:</strong></td><td style='padding:4px 0;'><strong>{trip_name}</strong></td></tr>"
            "<tr><td style='padding:4px 0;color:#6c757d;'><strong>{lbl_status}:</strong></td><td style='padding:4px 0;'><span style='background:#017e84;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;'>{status_val}</span></td></tr>"
            "{container_row}"
            "{cargo_row}"
            "<tr><td style='padding:4px 0;color:#6c757d;'><strong>{lbl_route}:</strong></td><td style='padding:4px 0;'>{origin_val} &rarr; {dest_val}</td></tr>"
            "<tr><td style='padding:4px 0;color:#6c757d;'><strong>{lbl_truck}:</strong></td><td style='padding:4px 0;'>{truck_val}</td></tr>"
            "</table>"
            "</div>"

            "<div style='margin-bottom:20px;padding:12px 16px;background:#ffffff;border:1px solid #dee2e6;border-radius:6px;'>"
            "<h4 style='margin:0 0 8px;color:#495057;font-size:15px;'>{lbl_curr_pos}</h4>"
            "<p style='margin:0;font-size:14px;'><strong>{pos_val}</strong>{map_link}{pos_time}{pos_notice}</p>"
            "</div>"

            "{milestones_table}"

            "<div style='margin-top:30px;padding-top:15px;border-top:1px solid #dee2e6;font-size:12px;color:#6c757d;text-align:center;'>"
            "<p style='margin:0 0 4px;'>{footer_note}</p>"
            "<p style='margin:0;'>{footer_tz}</p>"
            "</div>"
            "</div>"
        ).format(
            company_name=escape(self.company_id.name or ""),
            header_subtitle=_("Transport Trip Status Notification"),
            lbl_trip=_("Trip Reference"),
            trip_name=escape(self.name or ""),
            lbl_status=_("Status"),
            status_val=escape(status_label),
            container_row=Markup("<tr><td style='padding:4px 0;color:#6c757d;'><strong>{lbl}:</strong></td><td style='padding:4px 0;'>{val}</td></tr>").format(
                lbl=_("Container Number"), val=escape(self.container_num or "")
            ) if self.container_num else Markup(""),
            cargo_row=Markup("<tr><td style='padding:4px 0;color:#6c757d;'><strong>{lbl}:</strong></td><td style='padding:4px 0;'>{val}</td></tr>").format(
                lbl=_("Cargo"), val=escape(cargo_display)
            ) if cargo_display else Markup(""),
            lbl_route=_("Route"),
            origin_val=escape(self.origin_location or _("N/A")),
            dest_val=escape(self.destination_location or _("N/A")),
            lbl_truck=_("Assigned Truck"),
            truck_val=escape(truck_name),
            lbl_curr_pos=_("Current / Last-Known Position"),
            pos_val=pos_label,
            map_link=map_link_html,
            pos_time=pos_time_html,
            pos_notice=pos_notice_html,
            milestones_table=milestones_table_html,
            footer_note=_("This is an automated operational status update regarding your transport order."),
            footer_tz=_("All times displayed in %s.") % tz_str,
        )
        return html

    def _lock_for_email_dispatch(self):
        """Serialize email dispatches and timer calculations per trip row."""
        if self.ids:
            self.env.cr.execute(
                "SELECT id FROM taj_transport_trip WHERE id IN %s ORDER BY id FOR UPDATE",
                (tuple(self.ids),),
            )

    def _queue_trip_email_update(self, dispatch_type="manual"):
        """Single source of truth for email generation, mail queueing, and dispatch audit."""
        self.ensure_one()
        if dispatch_type == "manual":
            self.check_access("write")
            if self.state == "draft":
                raise UserError(_("Trip must reach Ready status before an update email can be sent."))

        self._lock_for_email_dispatch()
        self.invalidate_recordset([
            "state", "email_notification_enabled", "email_next_send_at",
            "email_interval_hours", "email_partner_ids", "email_extra_recipients",
        ])

        now = fields.Datetime.now()
        if dispatch_type == "automatic":
            if self.state != "in_progress" or not self.email_notification_enabled:
                return False
            if not self.email_next_send_at or self.email_next_send_at > now:
                return False

        # Strictly validate and resolve recipients before creating mail.mail or audit records
        recipients = self._get_email_recipients(raise_if_empty=True)
        subject = self._get_trip_email_subject()
        body_html = self._render_trip_email_html()

        # Queue one separate mail.mail record per recipient
        mail_model = self.env["mail.mail"].sudo().with_company(self.company_id)
        mail_records = mail_model.browse()
        sender = (
            self.company_id.partner_id.email_formatted
            or self.env.user.partner_id.email_formatted
        )
        for addr in recipients:
            mail = mail_model.create({
                "subject": subject,
                "body_html": body_html,
                "email_from": sender,
                "email_to": addr,
                "auto_delete": False,
                "model": "taj.transport.trip",
                "res_id": self.id,
            })
            mail_records |= mail

        # Create immutable dispatch audit record
        dispatch = self.env["taj.transport.trip.email.dispatch"].sudo().create({
            "trip_id": self.id,
            "dispatch_type": dispatch_type,
            "queued_at": now,
            "requested_by_id": self.env.user.id if dispatch_type == "manual" else False,
            "recipient_emails": "\n".join(recipients),
            "mail_ids": [(6, 0, mail_records.ids)],
        })

        # Post internal Chatter note
        note_body = Markup(
            "<p><strong>{type_label} {title}</strong></p>"
            "<ul style='margin-bottom:0;'>"
            "<li><strong>{lbl_recipients}:</strong> {recipients}</li>"
            "<li><strong>{lbl_queued}:</strong> {queued_time} UTC</li>"
            "<li><strong>{lbl_audit}:</strong> #{dispatch_id}</li>"
            "</ul>"
        ).format(
            type_label=_("Manual") if dispatch_type == "manual" else _("Automatic"),
            title=_("Email update queued"),
            lbl_recipients=_("Recipients"),
            recipients=", ".join(recipients),
            lbl_queued=_("Queued At"),
            queued_time=fields.Datetime.to_string(now),
            lbl_audit=_("Audit Record"),
            dispatch_id=dispatch.id,
        )
        self.message_post(body=note_body, subtype_xmlid="mail.mt_note")

        # Update timestamps and clear error tracking on success
        schedule_vals = {
            "email_last_queued_at": now,
            "email_failure_count": 0,
            "email_last_error": False,
            "email_last_failed_at": False,
        }
        if self.state == "in_progress" and self.email_notification_enabled:
            schedule_vals["email_next_send_at"] = now + timedelta(hours=self.email_interval_hours)
        else:
            schedule_vals["email_next_send_at"] = False

        self._write_email_schedule(schedule_vals)
        return dispatch

    def action_send_email_update_manual(self):
        """Dispatcher action to trigger an immediate status email."""
        self.ensure_one()
        dispatch = self._queue_trip_email_update(dispatch_type="manual")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Email Update Queued"),
                "message": _("Status email queued for %d recipient(s).") % dispatch.mail_count,
                "type": "success",
                "sticky": False,
            },
        }

    def _handle_cron_email_failure(self, exc):
        """Apply bounded backoff and record failure diagnostics without transaction pollution."""
        self.ensure_one()
        now = fields.Datetime.now()
        new_count = self.email_failure_count + 1
        error_msg = str(exc) or exc.__class__.__name__

        MAX_RETRIES = 3
        if new_count < MAX_RETRIES:
            backoff_multiplier = 2 ** (new_count - 1)
            backoff_hours = min(24.0, max(1.0, self.email_interval_hours) * backoff_multiplier)
            next_send = now + timedelta(hours=backoff_hours)
        else:
            # Suspend automated scheduling to prevent starving other trips in the batch
            next_send = False

        self._write_email_schedule({
            "email_failure_count": new_count,
            "email_last_error": error_msg,
            "email_last_failed_at": now,
            "email_next_send_at": next_send,
        })

        if new_count == 1:
            note_body = Markup(
                "<p style='color:#dc3545;'><strong>{title}</strong></p>"
                "<p>{msg}</p>"
                "<small style='color:#6c757d;'>{retry_msg}</small>"
            ).format(
                title=_("Automated Email Update Failed (Attempt 1 of %d)") % MAX_RETRIES,
                msg=escape(error_msg),
                retry_msg=_("Automated updates will retry in %.1f hour(s).") % backoff_hours,
            )
            self.message_post(body=note_body, subtype_xmlid="mail.mt_note")
        elif new_count >= MAX_RETRIES:
            note_body = Markup(
                "<p style='color:#dc3545;'><strong>{title}</strong></p>"
                "<p>{msg}</p>"
                "<p style='color:#856404;'><strong>{warn}</strong></p>"
            ).format(
                title=_("Automated Email Updates Suspended"),
                msg=escape(error_msg),
                warn=_(
                    "Periodic status updates for this trip have been suspended after %d consecutive failures. Please verify recipient configuration."
                ) % MAX_RETRIES,
            )
            self.message_post(body=note_body, subtype_xmlid="mail.mt_note")

    @api.model
    def _cron_send_due_trip_email_updates(self, batch_size=50):
        """Periodic cron job: find and process due trips with enabled notifications."""
        now = fields.Datetime.now()
        due_trips = self.search([
            ("state", "=", "in_progress"),
            ("email_notification_enabled", "=", True),
            ("email_next_send_at", "<=", now),
        ], order="email_next_send_at asc, id asc", limit=batch_size)

        for trip in due_trips:
            try:
                # Acquire the lock *before* the inner savepoint. A rollback to
                # the inner savepoint then cannot release this trip lock while
                # we are recording its failure/backoff state.
                with self.env.cr.savepoint():
                    trip._lock_for_email_dispatch()
                    try:
                        with self.env.cr.savepoint():
                            trip._queue_trip_email_update(dispatch_type="automatic")
                    except Exception as exc:
                        _logger.exception(
                            "Failed to dispatch automated email update for trip %s (id=%s): %s",
                            trip.name, trip.id, exc,
                        )
                        trip._handle_cron_email_failure(exc)
            except Exception as exc:
                _logger.exception(
                    "Could not process trip %s email update or record its failure: %s",
                    trip.id, exc,
                )
