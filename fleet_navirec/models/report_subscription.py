import logging
from datetime import datetime, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class FleetNavirecReportSubscription(models.Model):
    _name = "fleet.navirec.report.subscription"
    _description = "Navirec Client Tracking Subscription"
    _order = "partner_id, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=False)
    partner_id = fields.Many2one(
        "res.partner",
        string="Client",
        required=True,
        ondelete="restrict",
        index=True,
    )
    vehicle_ids = fields.Many2many(
        "fleet.vehicle",
        "fleet_navirec_subscription_vehicle_rel",
        "subscription_id",
        "vehicle_id",
        string="Tracked Vehicles",
    )
    email_to = fields.Char(required=True)
    email_cc = fields.Char()
    timezone = fields.Char(
        required=True,
        default=lambda self: self.env.user.tz or "UTC",
        help="IANA timezone used for the three daily send times.",
    )
    send_time_1 = fields.Char(
        string="Send Time 1",
        required=True,
        help="Local client time in HH:MM format.",
    )
    send_time_2 = fields.Char(
        string="Send Time 2",
        required=True,
        help="Local client time in HH:MM format.",
    )
    send_time_3 = fields.Char(
        string="Send Time 3",
        required=True,
        help="Local client time in HH:MM format.",
    )
    next_send_at = fields.Datetime(readonly=True, index=True)
    last_attempt_at = fields.Datetime(readonly=True)
    last_sent_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    last_error_at = fields.Datetime(readonly=True)

    field_vehicle = fields.Boolean(string="Vehicle", default=True)
    field_license_plate = fields.Boolean(string="License Plate", default=True)
    field_status = fields.Boolean(string="Navirec Status", default=True)
    field_location = fields.Boolean(string="Current Position", default=True)
    field_coordinates = fields.Boolean(string="Coordinates", default=False)
    field_last_gps = fields.Boolean(string="Last GPS", default=True)
    field_speed = fields.Boolean(string="Speed", default=True)
    field_ignition = fields.Boolean(string="Ignition / Movement", default=True)
    field_odometer = fields.Boolean(string="Odometer", default=True)

    _schedule_fields = {
        "active",
        "timezone",
        "send_time_1",
        "send_time_2",
        "send_time_3",
    }

    @staticmethod
    def _parse_send_time(value):
        try:
            hour_text, minute_text = (value or "").split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except (AttributeError, TypeError, ValueError):
            raise ValidationError(
                _("Send times must use the HH:MM format.")
            )
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValidationError(
                _("Send times must use a valid 24-hour HH:MM value.")
            )
        return hour, minute

    def _timezone_info(self):
        self.ensure_one()
        try:
            return ZoneInfo(self.timezone or "UTC")
        except ZoneInfoNotFoundError as exc:
            raise ValidationError(
                _("Unknown IANA timezone: %(timezone)s")
                % {"timezone": self.timezone}
            ) from exc

    @api.constrains(
        "timezone",
        "send_time_1",
        "send_time_2",
        "send_time_3",
        "active",
        "vehicle_ids",
        "field_vehicle",
        "field_license_plate",
        "field_status",
        "field_location",
        "field_coordinates",
        "field_last_gps",
    )
    def _check_tracking_configuration(self):
        for subscription in self:
            subscription._timezone_info()
            times = [
                subscription.send_time_1,
                subscription.send_time_2,
                subscription.send_time_3,
            ]
            parsed = [
                subscription._parse_send_time(value)
                for value in times
            ]
            if len(set(parsed)) != 3:
                raise ValidationError(
                    _("The three daily send times must be different.")
                )
            if subscription.active and not subscription.vehicle_ids:
                raise ValidationError(
                    _(
                        "Add at least one tracked vehicle before activating "
                        "the subscription."
                    )
                )
            if not subscription.field_status:
                raise ValidationError(
                    _(
                        "Navirec Status is a mandatory safety column so stale "
                        "GPS data cannot be presented as live."
                    )
                )
            if not subscription.field_last_gps:
                raise ValidationError(
                    _(
                        "Last GPS is a mandatory safety column for the "
                        "tracking report."
                    )
                )
            if not (
                subscription.field_vehicle
                or subscription.field_license_plate
            ):
                raise ValidationError(
                    _(
                        "Enable Vehicle or License Plate so every report row "
                        "can be identified."
                    )
                )
            if not (
                subscription.field_location
                or subscription.field_coordinates
            ):
                raise ValidationError(
                    _(
                        "Enable Current Position or Coordinates for the fleet "
                        "position report."
                    )
                )

    def _get_next_send_at(self, reference_utc=None):
        self.ensure_one()
        if not self.active:
            return False

        reference_utc = (
            fields.Datetime.to_datetime(reference_utc)
            if reference_utc
            else fields.Datetime.now()
        )
        reference_aware = (
            reference_utc.astimezone(timezone.utc)
            if reference_utc.tzinfo
            else reference_utc.replace(tzinfo=timezone.utc)
        )
        tzinfo = self._timezone_info()
        local_reference = reference_aware.astimezone(tzinfo)
        schedule = sorted(
            self._parse_send_time(value)
            for value in (
                self.send_time_1,
                self.send_time_2,
                self.send_time_3,
            )
        )

        for day_offset in (0, 1):
            local_date = local_reference.date() + timedelta(days=day_offset)
            for hour, minute in schedule:
                candidate_local = datetime.combine(
                    local_date,
                    time(hour=hour, minute=minute),
                    tzinfo=tzinfo,
                )
                if candidate_local <= local_reference:
                    continue
                return (
                    candidate_local.astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )
        return False

    def _refresh_next_send_at(self, reference_utc=None):
        for subscription in self:
            next_send = subscription._get_next_send_at(reference_utc)
            subscription.with_context(
                fleet_navirec_skip_schedule_refresh=True
            ).write({"next_send_at": next_send})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._refresh_next_send_at()
        return records

    def write(self, vals):
        result = super().write(vals)
        if (
            not self.env.context.get(
                "fleet_navirec_skip_schedule_refresh"
            )
            and self._schedule_fields.intersection(vals)
        ):
            self._refresh_next_send_at()
        return result

    def _display_gps_datetime(self, value):
        self.ensure_one()
        if not value:
            return _("Not available")
        utc_value = fields.Datetime.to_datetime(value)
        utc_value = (
            utc_value.astimezone(timezone.utc)
            if utc_value.tzinfo
            else utc_value.replace(tzinfo=timezone.utc)
        )
        return utc_value.astimezone(
            self._timezone_info()
        ).strftime("%Y-%m-%d %H:%M")

    def _prepare_report_rows(self):
        self.ensure_one()
        rows = []
        for vehicle in self.vehicle_ids.sorted(
            key=lambda record: (
                record.license_plate or "",
                record.display_name or "",
            )
        ):
            rows.append({
                "vehicle": vehicle.display_name or "",
                "license_plate": vehicle.license_plate or "",
                "status": dict(
                    vehicle._fields[
                        "navirec_integration_status"
                    ].selection
                ).get(
                    vehicle.navirec_integration_status,
                    vehicle.navirec_integration_status or "",
                ),
                "location": vehicle.navirec_position_display,
                "coordinates": (
                    (
                        f"{vehicle.navirec_last_lat:.7f}, "
                        f"{vehicle.navirec_last_lon:.7f}"
                    )
                    if vehicle.navirec_has_position
                    else _("Not available")
                ),
                "last_gps": self._display_gps_datetime(
                    vehicle.navirec_last_state_time
                ),
                "speed": vehicle.navirec_speed_display,
                "ignition": (
                    _("%(ignition)s / %(movement)s")
                    % {
                        "ignition": vehicle.navirec_ignition_display,
                        "movement": dict(
                            vehicle._fields[
                                "navirec_movement_state"
                            ].selection
                        ).get(
                            vehicle.navirec_movement_state,
                            vehicle.navirec_movement_state or "",
                        ),
                    }
                ),
                "odometer": vehicle.navirec_odometer_display,
            })
        return rows

    def _report_columns(self):
        self.ensure_one()
        candidates = [
            ("field_vehicle", "vehicle", _("Vehicle")),
            (
                "field_license_plate",
                "license_plate",
                _("License Plate"),
            ),
            ("field_status", "status", _("Navirec Status")),
            ("field_location", "location", _("Current Position")),
            ("field_coordinates", "coordinates", _("Coordinates")),
            ("field_last_gps", "last_gps", _("Last GPS")),
            ("field_speed", "speed", _("Speed")),
            (
                "field_ignition",
                "ignition",
                _("Ignition / Movement"),
            ),
            ("field_odometer", "odometer", _("Odometer")),
        ]
        return [
            (key, label)
            for field_name, key, label in candidates
            if self[field_name]
        ]

    def _render_report_html(self):
        self.ensure_one()
        columns = self._report_columns()
        if not columns:
            raise UserError(
                _("Select at least one report field.")
            )

        generated_at = self._display_gps_datetime(
            fields.Datetime.now()
        )
        header = "".join(
            f"<th>{escape(str(label))}</th>"
            for _key, label in columns
        )
        body_rows = []
        for row in self._prepare_report_rows():
            cells = "".join(
                f"<td>{escape(str(row.get(key) or ''))}</td>"
                for key, _label in columns
            )
            body_rows.append(f"<tr>{cells}</tr>")

        if body_rows:
            body = "".join(body_rows)
        else:
            body = (
                f'<tr><td colspan="{len(columns)}">'
                f'{escape(str(_("No tracked vehicles configured.")))}'
                "</td></tr>"
            )

        return (
            f"<p><strong>{escape(self.partner_id.display_name)}</strong></p>"
            f"<p>{escape(str(_('Fleet Position Report')))}"
            f" — {escape(generated_at)}</p>"
            '<table style="border-collapse:collapse;width:100%" '
            'border="1" cellpadding="6">'
            f"<thead><tr>{header}</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table>"
        )

    def action_preview_report(self):
        self.ensure_one()
        preview = self.env["fleet.navirec.report.preview"].create({
            "subscription_id": self.id,
            "body_html": self._render_report_html(),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Fleet Position Report Preview"),
            "res_model": "fleet.navirec.report.preview",
            "res_id": preview.id,
            "view_mode": "form",
            "target": "new",
        }

    def _mail_sender(self):
        company_sender = self.env.company.partner_id.email_formatted
        user_sender = self.env.user.partner_id.email_formatted
        sender = company_sender or user_sender
        if not sender:
            raise UserError(
                _("Configure an email address for the company or current user.")
            )
        return sender

    def _send_tracking_email(self):
        self.ensure_one()
        if not self.vehicle_ids:
            raise UserError(
                _("Add at least one tracked vehicle before sending.")
            )
        if not self.email_to:
            raise UserError(_("Configure at least one recipient."))

        mail = self.env["mail.mail"].sudo().create({
            "email_from": self._mail_sender(),
            "email_to": self.email_to,
            "email_cc": self.email_cc or False,
            "subject": _(
                "Fleet Position Report - %(client)s"
            ) % {"client": self.partner_id.display_name},
            "body_html": self._render_report_html(),
        })
        mail.send(raise_exception=True)
        mail.invalidate_recordset(["state"])
        if mail.state != "sent":
            raise UserError(
                _(
                    "Odoo did not confirm email delivery. "
                    "Mail state: %(state)s"
                ) % {"state": mail.state or _("unknown")}
            )
        return mail

    def action_send_now(self):
        self.ensure_one()
        attempt_at = fields.Datetime.now()
        self.with_context(
            fleet_navirec_skip_schedule_refresh=True
        ).write({
            "last_attempt_at": attempt_at,
            "last_error": False,
            "last_error_at": False,
        })
        try:
            self._send_tracking_email()
        except Exception as exc:
            _logger.exception(
                "Manual Navirec report delivery failed for subscription %s",
                self.id,
            )
            self.with_context(
                fleet_navirec_skip_schedule_refresh=True
            ).write({
                "last_error": str(exc),
                "last_error_at": fields.Datetime.now(),
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Navirec"),
                    "message": _(
                        "Tracking report was not sent: %(error)s"
                    ) % {"error": str(exc)},
                    "type": "danger",
                    "sticky": True,
                },
            }

        sent_at = fields.Datetime.now()
        self.with_context(
            fleet_navirec_skip_schedule_refresh=True
        ).write({
            "last_sent_at": sent_at,
            "last_error": False,
            "last_error_at": False,
        })
        self._refresh_next_send_at(
            sent_at + timedelta(seconds=1)
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Navirec"),
                "message": _("Tracking report sent."),
                "type": "success",
            },
        }

    @api.model
    def _cron_send_due_reports(self):
        now = fields.Datetime.now()
        due = self.search([
            ("active", "=", True),
            ("next_send_at", "!=", False),
            ("next_send_at", "<=", now),
        ])
        if not due:
            return

        self.env["fleet.vehicle"]._cron_navirec_sync_states()

        for subscription in due:
            attempt_at = fields.Datetime.now()
            subscription.with_context(
                fleet_navirec_skip_schedule_refresh=True
            ).write({"last_attempt_at": attempt_at})
            try:
                subscription._send_tracking_email()
            except Exception as exc:
                _logger.exception(
                    "Navirec report delivery failed for subscription %s",
                    subscription.id,
                )
                retry_at = fields.Datetime.now() + timedelta(minutes=30)
                subscription.with_context(
                    fleet_navirec_skip_schedule_refresh=True
                ).write({
                    "last_error": str(exc),
                    "last_error_at": fields.Datetime.now(),
                    "next_send_at": retry_at,
                })
                continue

            sent_at = fields.Datetime.now()
            subscription.with_context(
                fleet_navirec_skip_schedule_refresh=True
            ).write({
                "last_sent_at": sent_at,
                "last_error": False,
                "last_error_at": False,
            })
            subscription._refresh_next_send_at(
                sent_at + timedelta(seconds=1)
            )


class FleetNavirecReportPreview(models.TransientModel):
    _name = "fleet.navirec.report.preview"
    _description = "Navirec Tracking Report Preview"

    subscription_id = fields.Many2one(
        "fleet.navirec.report.subscription",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    body_html = fields.Html(readonly=True)

    def action_send_now(self):
        self.ensure_one()
        return self.subscription_id.action_send_now()
