"""Trip milestone records: snapshotted checkpoints, manual workflow, audit.

Milestone rows are created by copying a route template's checkpoint
definition. The copy is a *snapshot*: later template edits never rewrite a
trip's checkpoints. Operational events (arrival and departure)
are recorded manually through the dedicated workflow methods in this file;
direct field writes are rejected server-side and the audit trail lives in
``taj.transport.milestone.event``.
"""

from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

MANAGER_GROUP = "taj_transport_operations.group_transport_manager"

# Only free text is directly editable by Dispatchers. Every operational
# value (state, timestamps, authors, snapshot fields) is owned by the
# workflow methods below, which validate access, chronology and audit.
DIRECT_WRITABLE_FIELDS = frozenset({"notes"})

SYSTEM_FIELDS = frozenset(
    {
        "trip_id",
        "route_id",
        "template_line_id",
        "sequence",
        "name",
        "milestone_type_id",
        "type_name",
        "requires_confirmation",
        "planned_offset_hours",
        "latitude",
        "longitude",
        "state",
        "actual_arrival_at",
        "actual_departure_at",
        "arrived_by_id",
        "departed_by_id",
        "confirmed_at",
        "confirmed_by_id",
        "confirmation_note",
        "company_id",
    }
)

STATE_SELECTION = [
    ("pending", "Pending"),
    ("arrived", "Arrived"),
    ("awaiting_confirmation", "Awaiting Confirmation"),
    ("departed", "Departed"),
    ("completed", "Completed"),
    ("skipped", "Skipped"),
]


class TajTransportMilestone(models.Model):
    _name = "taj.transport.milestone"
    _description = "Trip Milestone"
    _order = "sequence, id"
    _check_company_auto = True
    _rec_name = "name"

    trip_id = fields.Many2one(
        "taj.transport.trip",
        string="Trip",
        required=True,
        ondelete="restrict",
        index=True,
        check_company=True,
        copy=False,
    )
    sale_order_id = fields.Many2one(
        "sale.order", string="Sales Order",
        related="trip_id.sale_order_id", readonly=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Customer",
        related="trip_id.partner_id", readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="trip_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Truck",
        compute="_compute_vehicle_id", store=True, readonly=True,
        index=True, check_company=True,
        falsy_value_label="Unassigned",
        help="Finished checkpoints use the truck assigned at departure or "
        "skip time. Unfinished checkpoints follow the assigned truck, or the "
        "last truck after trip closure. Before any assignment exists, "
        "unfinished checkpoints use the original Sales truck. Missing "
        "historical assignments appear as Unassigned.",
    )
    route_id = fields.Many2one(
        "taj.transport.route",
        string="Generated From Route",
        readonly=True,
        ondelete="restrict",
        check_company=True,
        copy=False,
    )
    template_line_id = fields.Many2one(
        "taj.transport.route.line",
        string="Template Checkpoint",
        readonly=True,
        ondelete="set null",
        copy=False,
        help="Traceability only. The milestone keeps its own snapshot, so "
        "editing or removing the template line changes nothing here.",
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Milestone",
        required=True,
        help="Checkpoint name copied from the route template or entered by "
        "operations. Kept as a snapshot of the planning at generation "
        "time.",
    )
    milestone_type_id = fields.Many2one(
        "taj.transport.milestone.type",
        string="Checkpoint Type",
        readonly=True,
        ondelete="restrict",
    )
    type_name = fields.Char(
        string="Checkpoint Type at Planning",
        readonly=True,
        help="Snapshot of the checkpoint type name, so renaming the catalog "
        "never rewrites trip history.",
    )
    requires_confirmation = fields.Boolean(
        string="Legacy Confirmation Requirement",
        readonly=True,
        help="Historical planning data only. Departure completes the checkpoint.",
    )
    planned_offset_hours = fields.Float(
        string="Planned Offset (Hours)",
        digits=(16, 2),
        readonly=True,
    )
    planned_arrival_at = fields.Datetime(
        string="Planned Arrival at Checkpoint",
        readonly=True,
        compute="_compute_planned_arrival_at",
        store=True,
        help="Planning hint derived from the trip's planned departure plus "
        "the template's planned offset. It follows replanning and is "
        "never an actual event.",
    )
    latitude = fields.Float(digits=(10, 7), readonly=True)
    longitude = fields.Float(digits=(10, 7), readonly=True)
    actual_arrival_at = fields.Datetime(
        string="Actual Arrival",
        readonly=True,
        copy=False,
    )
    actual_departure_at = fields.Datetime(
        string="Actual Departure",
        readonly=True,
        copy=False,
    )
    arrived_by_id = fields.Many2one(
        "res.users",
        string="Arrival Recorded By",
        readonly=True,
        copy=False,
    )
    departed_by_id = fields.Many2one(
        "res.users",
        string="Departure Recorded By",
        readonly=True,
        copy=False,
    )
    confirmed_at = fields.Datetime(
        string="Confirmed At",
        readonly=True,
        copy=False,
    )
    confirmed_by_id = fields.Many2one(
        "res.users",
        string="Confirmed By",
        readonly=True,
        copy=False,
    )
    confirmation_note = fields.Text(
        string="Confirmation Note",
        readonly=True,
        copy=False,
    )
    state = fields.Selection(
        STATE_SELECTION,
        string="Status",
        default="pending",
        required=True,
        readonly=True,
        index=True,
        copy=False,
    )
    notes = fields.Text(
        string="Operational Notes",
        help="Free dispatcher notes for this checkpoint.",
    )
    event_ids = fields.One2many(
        "taj.transport.milestone.event",
        "milestone_id",
        string="Checkpoint History",
        readonly=True,
    )
    event_count = fields.Integer(
        string="Checkpoint Events",
        compute="_compute_event_count",
    )

    @api.depends("trip_id.planned_departure_at", "planned_offset_hours")
    def _compute_planned_arrival_at(self):
        for milestone in self:
            departure = milestone.trip_id.planned_departure_at
            if departure:
                # A zero offset means "at departure", not "not planned".
                milestone.planned_arrival_at = departure + timedelta(
                    hours=milestone.planned_offset_hours or 0.0
                )
            else:
                milestone.planned_arrival_at = False

    @api.depends("event_ids")
    def _compute_event_count(self):
        for milestone in self:
            milestone.event_count = len(milestone.event_ids)

    def init(self):
        # One milestone per template checkpoint and trip. A partial index
        # makes duplicated generation impossible even for concurrent
        # requests that both pass the in-memory duplicate check.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                taj_transport_milestone_trip_template_line_idx
            ON taj_transport_milestone (trip_id, template_line_id)
            WHERE template_line_id IS NOT NULL
        """)

    @api.constrains("trip_id", "name")
    def _check_unique_name_per_trip(self):
        for milestone in self:
            duplicate = self.search_count(
                [
                    ("trip_id", "=", milestone.trip_id.id),
                    ("id", "!=", milestone.id),
                    ("name", "=ilike", (milestone.name or "").strip()),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _("This trip already has a milestone with this name.")
                )

    @api.depends(
        "state", "actual_departure_at",
        "event_ids.event_type", "event_ids.occurred_at",
        "trip_id.initial_vehicle_id",
        "trip_id.assignment_ids",
        "trip_id.assignment_ids.vehicle_id",
        "trip_id.assignment_ids.effective_from",
        "trip_id.assignment_ids.effective_to",
    )
    def _compute_vehicle_id(self):
        """Attribute finished work by event time and open work by trip resources."""
        history = {}
        for trip in self.mapped("trip_id"):
            history[trip.id] = trip.assignment_ids.sorted(
                key=lambda assignment: (assignment.effective_from, assignment.id),
                reverse=True,
            )
        for milestone in self:
            trip = milestone.trip_id
            assignments = history.get(trip.id, self.env["taj.transport.assignment"])
            event_at = milestone.actual_departure_at
            finished = milestone.state in ("departed", "completed", "skipped")
            if milestone.state == "skipped":
                skipped = milestone.event_ids.filtered(
                    lambda event: event.event_type == "skipped"
                )[:1]
                event_at = skipped.occurred_at
            if event_at:
                # Newest start wins at a replacement boundary. Include the
                # final end so departure at trip completion keeps its truck.
                assignment = assignments.filtered(
                    lambda row: row.effective_from <= event_at
                    and (not row.effective_to or event_at <= row.effective_to)
                )[:1]
                milestone.vehicle_id = assignment.vehicle_id
            elif finished:
                # Historical work without an event time cannot be attributed.
                milestone.vehicle_id = False
            elif assignments:
                current = assignments.filtered(lambda row: not row.effective_to)
                milestone.vehicle_id = (current[:1] or assignments[:1]).vehicle_id
            else:
                milestone.vehicle_id = trip.initial_vehicle_id

    @api.depends("trip_id.name", "name")
    def _compute_display_name(self):
        """Show the checkpoint in the context of its trip."""
        for milestone in self:
            milestone.display_name = "%s: %s" % (
                milestone.trip_id.name,
                milestone.name,
            )

    # ------------------------------------------------------------------
    # Direct write / delete protection
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # A checkpoint always snapshots the type name it was planned
            # with, whether it came from a route template or from operations.
            if vals.get("milestone_type_id") and not vals.get("type_name"):
                vals["type_name"] = (
                    self.env["taj.transport.milestone.type"]
                    .browse(vals["milestone_type_id"])
                    .name
                    or False
                )
        return super().create(vals_list)

    def write(self, vals):
        forbidden = sorted(set(vals) - DIRECT_WRITABLE_FIELDS)
        if forbidden:
            raise UserError(
                _(
                    "Milestone values are managed by the checkpoint workflow. "
                    "Record arrival or departure through their "
                    "actions, and use the manager correction workflow (with a "
                    "reason) for recorded timestamps.\nRejected fields: %s"
                )
                % ", ".join(forbidden)
            )
        if "notes" in vals:
            # Free text is the only value a user may edit directly, so it is
            # also the only value that needs a state check: once a trip is
            # completed or cancelled its checkpoint history is closed, and
            # annotations must not drift away from it. No notes correction
            # policy exists, so this is refused for every role; recorded
            # timestamps keep their own audited manager correction workflow.
            closed = self.filtered(
                lambda milestone: milestone.trip_id.state in ("completed", "cancelled")
            )
            if closed:
                raise UserError(
                    _(
                        "Checkpoint notes cannot be changed on a completed or "
                        "cancelled trip: its checkpoint history is closed (%s)."
                    )
                    % ", ".join(closed.mapped("name"))
                )
        return super().write(vals)

    def unlink(self):
        # Dispatchers have no delete right at all: they get the permission
        # error, while a Transport Manager gets the operational explanation.
        self.check_access("unlink")
        raise UserError(
            _(
                "Trip milestones cannot be deleted: they are the operational "
                "history of the trip. Regenerate the trip's checkpoints before "
                "operations begin, or skip a checkpoint with a reason."
            )
        )

    def _unlink_pending_for_regeneration(self):
        """Only the trip's explicit regeneration path removes checkpoints.

        Rows are removable only while nothing at all was recorded on them,
        so no operational value or audit entry can be lost this way.
        """
        for milestone in self:
            if (
                self.env["taj.transport.milestone.event"]
                .sudo()
                .search_count(
                    [
                        ("milestone_id", "=", milestone.id),
                    ]
                )
            ):
                raise UserError(
                    _("%s already has checkpoint history and cannot be discarded.")
                    % milestone.name
                )
            if (
                milestone.actual_arrival_at
                or milestone.actual_departure_at
                or milestone.confirmed_at
                or milestone.state not in ("pending", "skipped")
            ):
                raise UserError(
                    _(
                        "%s already has recorded progress and cannot be "
                        "discarded. Add the missing checkpoint instead."
                    )
                    % milestone.name
                )
        return super(TajTransportMilestone, self.sudo()).unlink()

    # ------------------------------------------------------------------
    # Access helpers
    # ------------------------------------------------------------------
    def _is_transport_manager(self):
        """Superuser environments (cron, tests, OdooBot) already have all rights."""
        return self.env.su or self.env.user.has_group(MANAGER_GROUP)

    def _check_manager(self, action_label):
        if not self._is_transport_manager():
            raise AccessError(_("Only Transport Managers can %s.") % action_label)

    def _lock_trip_for_work(self):
        """Serialize checkpoint work with lifecycle transitions.

        The trip row lock is the same one used by Mark Ready / Start /
        Complete, so a checkpoint can never be recorded against a state that
        was already changed by a concurrent request.
        """
        self.ensure_one()
        trip = self.trip_id
        trip._lock_for_transition()
        trip.invalidate_recordset(["state", "actual_arrival_at"])
        self.invalidate_recordset(
            [
                "state",
                "actual_arrival_at",
                "actual_departure_at",
                "confirmed_at",
            ]
        )
        return trip

    def _check_recording_allowed(self, trip, manager_action=False):
        """Checkpoints are operational facts: ready and in-progress only."""
        if trip.state in ("ready", "in_progress"):
            return
        if trip.state == "draft":
            raise UserError(
                _(
                    "Checkpoint events can be recorded once the trip is Ready. "
                    "Finish planning (route, planned times and resources) first."
                )
            )
        if trip.state == "completed":
            raise UserError(
                _(
                    "This trip is completed. Recorded checkpoint values can only "
                    "be corrected by a Transport Manager with a reason."
                )
            )
        raise UserError(
            _(
                "This trip is cancelled. Checkpoint events can no longer be "
                "recorded or corrected."
            )
        )

    # ------------------------------------------------------------------
    # Manual recording workflow
    # ------------------------------------------------------------------
    def _validated_event_time(self, event_at, label):
        timestamp = fields.Datetime.to_datetime(event_at) or fields.Datetime.now()
        if timestamp > fields.Datetime.now():
            raise ValidationError(_("%s cannot be in the future.") % label)
        return timestamp

    def _log_event(
        self,
        event_type,
        *,
        occurred_at=False,
        reason=False,
        note=False,
        field_name=False,
        old_value=False,
        new_value=False,
    ):
        return (
            self.env["taj.transport.milestone.event"]
            .sudo()
            .create(
                {
                    "milestone_id": self.id,
                    "event_type": event_type,
                    "source": "manual",
                    "user_id": self.env.user.id,
                    "occurred_at": occurred_at or False,
                    "field_name": field_name or False,
                    "old_value": str(old_value) if old_value else False,
                    "new_value": str(new_value) if new_value else False,
                    "reason": reason or False,
                    "note": note or False,
                }
            )
        )

    def _post_trip_message(self, body):
        self.trip_id.message_post(body=body)

    def _record_arrival(self, event_at=None, note=None):
        self.ensure_one()
        self.check_access("write")
        trip = self._lock_trip_for_work()
        self._check_recording_allowed(trip)
        if self.actual_arrival_at:
            raise UserError(
                _(
                    "%(milestone)s already has an arrival at %(time)s. Use the "
                    "manager correction workflow to change a recorded value."
                )
                % {
                    "milestone": self.name,
                    "time": self.actual_arrival_at,
                }
            )
        timestamp = self._validated_event_time(event_at, _("Arrival"))
        trip._validate_milestone_timeline(
            {
                self.id: {"actual_arrival_at": timestamp},
            }
        )
        super(TajTransportMilestone, self).write(
            {
                "actual_arrival_at": timestamp,
                "arrived_by_id": self.env.user.id,
            }
        )
        self._recompute_state()
        self._log_event(
            "arrival",
            occurred_at=timestamp,
            note=note,
        )
        self._post_trip_message(
            Markup("<p>{header}</p><p>{label}: {time}</p>{note}").format(
                header=_("Checkpoint arrival recorded: %s") % self.name,
                label=_("Arrival"),
                time=fields.Datetime.context_timestamp(self, timestamp).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                note=Markup("<p>{}</p>").format(note) if note else "",
            )
        )
        return True

    def _record_departure(self, event_at=None, note=None):
        self.ensure_one()
        self.check_access("write")
        trip = self._lock_trip_for_work()
        self._check_recording_allowed(trip)
        if self.actual_departure_at:
            raise UserError(
                _(
                    "%(milestone)s already has a departure at %(time)s. Use the "
                    "manager correction workflow to change a recorded value."
                )
                % {
                    "milestone": self.name,
                    "time": self.actual_departure_at,
                }
            )
        if not self.actual_arrival_at:
            raise UserError(
                _(
                    "Record the arrival at %s before its departure: a departure "
                    "cannot exist without an arrival."
                )
                % self.name
            )
        timestamp = self._validated_event_time(event_at, _("Departure"))
        if timestamp < self.actual_arrival_at:
            raise ValidationError(
                _(
                    "The departure of %(milestone)s cannot be earlier than its "
                    "arrival (%(arrival)s)."
                )
                % {
                    "milestone": self.name,
                    "arrival": self.actual_arrival_at,
                }
            )
        trip._validate_milestone_timeline(
            {
                self.id: {"actual_departure_at": timestamp},
            }
        )
        super(TajTransportMilestone, self).write(
            {
                "actual_departure_at": timestamp,
                "departed_by_id": self.env.user.id,
            }
        )
        self._recompute_state()
        self._log_event("departure", occurred_at=timestamp, note=note)
        self._post_trip_message(
            Markup("<p>{header}</p><p>{label}: {time}</p>{note}").format(
                header=_("Checkpoint departure recorded: %s") % self.name,
                label=_("Departure"),
                time=fields.Datetime.context_timestamp(self, timestamp).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                note=Markup("<p>{}</p>").format(note) if note else "",
            )
        )
        return True

    def _confirm_checkpoint(self, note=None):
        """Reject stale clients using the retired confirmation workflow."""
        raise UserError(_(
            "Separate checkpoint confirmation is no longer used. "
            "Record Departure to complete the checkpoint."
        ))

    def _skip_checkpoint(self, reason):
        """Manager-only, reasoned: the checkpoint does not apply to this trip."""
        self.ensure_one()
        self.check_access("write")
        self._check_manager(_("skip a checkpoint"))
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError(_("Enter the reason for skipping this checkpoint."))
        trip = self._lock_trip_for_work()
        self._check_recording_allowed(trip)
        if self.state == "skipped":
            raise UserError(_("This checkpoint is already skipped."))
        if self.actual_arrival_at or self.actual_departure_at or self.confirmed_at:
            raise UserError(
                _(
                    "A checkpoint with recorded events cannot be skipped: "
                    "recorded checkpoint history is never removed."
                )
            )
        now = fields.Datetime.now()
        super(TajTransportMilestone, self).write({"state": "skipped"})
        self._log_event("skipped", occurred_at=now, reason=reason)
        self._post_trip_message(
            Markup("<p>{header}</p><p>{label}: {reason}</p>").format(
                header=_("Checkpoint skipped: %s") % self.name,
                label=_("Reason"),
                reason=reason,
            )
        )
        return True

    def _recompute_state(self):
        """Derive the status from the recorded events of this milestone."""
        self.ensure_one()
        if self.state == "skipped":
            return
        if self.actual_departure_at:
            state = "departed"
        elif self.actual_arrival_at:
            state = "arrived"
        else:
            state = "pending"
        super(TajTransportMilestone, self).write({"state": state})

    # ------------------------------------------------------------------
    # Manager corrections
    # ------------------------------------------------------------------
    def _correct_timestamp(self, field_name, value, reason, note=None):
        """Audited before/after correction of one recorded timestamp.

        Empty ``value`` clears the recorded event, which is why the reason
        is mandatory. Chronology is re-validated for the whole trip before
        anything is written.
        """
        self.ensure_one()
        self.check_access("write")
        self._check_manager(_("correct a recorded checkpoint timestamp"))
        if field_name not in ("actual_arrival_at", "actual_departure_at"):
            raise ValidationError(_("Unsupported checkpoint field."))
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError(
                _(
                    "Enter the reason for correcting this checkpoint value. "
                    "Corrections are kept in the audit trail."
                )
            )
        trip = self._lock_trip_for_work()
        if trip.state == "cancelled":
            raise UserError(
                _(
                    "A cancelled trip is closed: checkpoint values can no longer "
                    "be corrected."
                )
            )
        old_value = self[field_name]
        new_value = (
            self._validated_event_time(value, _("Corrected checkpoint time"))
            if value
            else False
        )
        if old_value == new_value:
            raise UserError(
                _(
                    "The corrected value is identical to the recorded value. "
                    "Nothing was changed."
                )
            )
        if (
            field_name == "actual_arrival_at"
            and not new_value
            and self.actual_departure_at
        ):
            raise UserError(
                _("Clear the departure of %s before clearing its arrival.") % self.name
            )
        if (
            field_name == "actual_departure_at"
            and new_value
            and not self.actual_arrival_at
        ):
            raise ValidationError(_(
                "Record the arrival before setting a checkpoint departure."
            ))
        if (
            field_name == "actual_departure_at"
            and new_value
            and self.actual_arrival_at
            and new_value < self.actual_arrival_at
        ):
            raise ValidationError(
                _(
                    "The departure of %(milestone)s cannot be earlier than its "
                    "arrival (%(arrival)s)."
                )
                % {"milestone": self.name, "arrival": self.actual_arrival_at}
            )
        trip._validate_milestone_timeline({self.id: {field_name: new_value}})
        author_id = self.env.user.id
        extra = {}
        if field_name == "actual_arrival_at":
            extra["arrived_by_id"] = author_id if new_value else False
        else:
            extra["departed_by_id"] = author_id if new_value else False
        # Historical confirmations remain independent, read-only evidence.
        kept_confirmation = self.confirmed_at
        # One savepoint for the whole change: a failure in the audit trail or
        # in the chatter leaves no half-corrected checkpoint behind.
        with self.env.cr.savepoint():
            super(TajTransportMilestone, self).write(
                {
                    field_name: new_value,
                    **extra,
                }
            )
            # Status follows arrival/departure; historical confirmations stay.
            self._recompute_state()
            self._log_event(
                "correction",
                occurred_at=new_value or old_value,
                reason=reason,
                note=note,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
            )
            label = self._fields[field_name].string
            recorded = lambda value: (
                fields.Datetime.context_timestamp(self, value).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if value
                else _("empty")
            )
            confirmation_note = (
                Markup("<p>{}</p>").format(
                    _("The historical business confirmation stays recorded.")
                )
                if kept_confirmation and self.confirmed_at
                else ""
            )
            self._post_trip_message(
                Markup(
                    "<p>{header}</p><p>{label}: {before} → {after}</p>"
                    "<p>{reason_label}: {reason}</p>{confirmation}{note}"
                ).format(
                    header=_("Checkpoint corrected: %(milestone)s (%(field)s)")
                    % {
                        "milestone": self.name,
                        "field": label,
                    },
                    label=label,
                    before=recorded(old_value),
                    after=recorded(new_value),
                    reason_label=_("Reason"),
                    reason=reason,
                    confirmation=confirmation_note,
                    note=Markup("<p>{}</p>").format(note) if note else "",
                )
            )
        return True

    def _cancel_confirmation(self, reason, note=None):
        """Historical confirmations remain read-only after the workflow change."""
        raise UserError(_(
            "Historical checkpoint confirmations are read-only. "
            "Use Edit to correct an arrival or departure time."
        ))

    # ------------------------------------------------------------------
    # UI entry points
    # ------------------------------------------------------------------
    def _action_wizard(self, wizard_model, name, context):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": wizard_model,
            "view_mode": "form",
            "target": "new",
            "context": context,
        }

    def _record_wizard_action(self, action_type, name):
        self.ensure_one()
        if action_type not in ("arrival", "departure", "skip"):
            raise UserError(_(
                "Separate checkpoint confirmation is no longer used. "
                "Record Departure to complete the checkpoint."
            ))
        if action_type == "skip":
            self._check_manager(_("skip a checkpoint"))
        return self._action_wizard(
            "taj.transport.milestone.action.wizard",
            name,
            {"default_milestone_id": self.id, "default_action_type": action_type},
        )

    def action_open_arrival_wizard(self):
        return self._record_wizard_action("arrival", _("Record Checkpoint Arrival"))

    def action_open_departure_wizard(self):
        return self._record_wizard_action(
            "departure",
            _("Record Checkpoint Departure"),
        )

    def action_open_confirmation_wizard(self):
        return self._record_wizard_action(
            "confirmation",
            _("Confirm Checkpoint"),
        )

    def action_open_cancel_confirmation_wizard(self):
        return self._record_wizard_action(
            "cancel_confirmation",
            _("Cancel Checkpoint Confirmation"),
        )

    def action_open_skip_wizard(self):
        return self._record_wizard_action("skip", _("Skip Checkpoint"))

    def action_open_correction_wizard(self):
        self.ensure_one()
        self._check_manager(_("correct a recorded checkpoint timestamp"))
        return self._action_wizard(
            "taj.transport.milestone.correction.wizard",
            _("Correct Checkpoint Timestamp"),
            {"default_milestone_id": self.id},
        )

    def action_open_trip(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": self.trip_id.id,
            "view_mode": "form",
            "target": "current",
        }


class TajTransportMilestoneEvent(models.Model):
    """Immutable audit trail of checkpoint operations.

    One row per recorded event or correction, including the acting user, the
    business timestamp, the recorded timestamp and before/after values for
    corrections. Rows are never updated or deleted.
    """

    _name = "taj.transport.milestone.event"
    _description = "Trip Milestone Event"
    _order = "recorded_at desc, id desc"
    _check_company_auto = True

    milestone_id = fields.Many2one(
        "taj.transport.milestone",
        string="Milestone",
        required=True,
        ondelete="restrict",
        index=True,
        check_company=True,
    )
    trip_id = fields.Many2one(
        "taj.transport.trip",
        related="milestone_id.trip_id",
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="milestone_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    event_type = fields.Selection(
        [
            ("arrival", "Arrival Recorded"),
            ("departure", "Departure Recorded"),
            ("confirmation", "Business Confirmation"),
            ("confirmation_cancelled", "Confirmation Cancelled"),
            ("skipped", "Checkpoint Skipped"),
            ("correction", "Correction"),
        ],
        string="Event",
        required=True,
        readonly=True,
    )
    source = fields.Selection(
        [("manual", "Manual"), ("gps", "GPS (Phase 4)")],
        string="Source",
        required=True,
        default="manual",
        readonly=True,
        help="Manual in this phase. The GPS option is reserved for the "
        "event-driven automation phase and is never set here.",
    )
    occurred_at = fields.Datetime(
        string="Event Time",
        readonly=True,
        help="The operational timestamp that was recorded.",
    )
    recorded_at = fields.Datetime(
        string="Recorded At",
        readonly=True,
        default=fields.Datetime.now,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Recorded By",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )
    field_name = fields.Char(string="Corrected Field", readonly=True)
    old_value = fields.Char(string="Value Before", readonly=True)
    new_value = fields.Char(string="Value After", readonly=True)
    reason = fields.Char(string="Reason", readonly=True)
    note = fields.Text(string="Note", readonly=True)

    def write(self, vals):
        raise UserError(
            _("Checkpoint audit records are immutable. They cannot be edited.")
        )

    def unlink(self):
        # Same split as the milestone itself: the permission error for users
        # without delete rights, the audit explanation for everyone else.
        self.check_access("unlink")
        raise UserError(
            _("Checkpoint audit records are immutable. They cannot be deleted.")
        )
