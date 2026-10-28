"""Phase 3 trip extensions: route template selection and trip checkpoints.

Generation policy (deliberate, documented and tested):

* Selecting a route template copies its ordered checkpoints into the trip as
  independent milestone records. The copy is a snapshot: editing the template
  later never rewrites a trip that was already generated.
* Checkpoints are planned while the trip is in Draft only. Once the trip is
  Ready its plan is in operational use, so the template can neither be
  changed, regenerated nor cleared: route variances are covered by adding a
  manual checkpoint, and a recorded time can only be corrected by a Transport
  Manager, with a reason, in the audited correction workflow.
* At most one generation happens per selected configuration. Re-selecting the
  same template is a no-op, re-clicking is a no-op, and a partial unique index
  on (trip_id, template_checkpoint) makes duplicated rows impossible even for
  concurrent requests.
* Recorded checkpoint progress is never discarded. Template changes are
  refused as soon as any checkpoint recorded an event, or as soon as the trip
  holds any audit entry at all. Generated checkpoints without progress may be
  replaced by an explicit action; manually added checkpoints are always kept.
* Clearing the template of an untouched Draft trip removes its generated
  checkpoints and the generation marker in the same transaction, and keeps the
  manually added checkpoints of the trip.
* A template is applied only when it exists, is active, belongs to the trip's
  company and is readable by the requesting user. The backend validates this
  for assignment, creation, generation and regeneration, so no direct ORM or
  RPC call can bypass the view filters.
* Trips without a route template stay valid and are never backfilled
  automatically, including existing Phase 2 trips.
* Outstanding checkpoints never block Ready / Start / Complete. Completion
  only reports them, so no surprise mandatory checkpoint is imposed.
"""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

DONE_STATES = ("completed", "departed")

#: Values the manual checkpoint workflow accepts. Everything else (state,
#: timestamps, type snapshot, template line and audit fields) is decided by
#: the workflow itself, so no caller can forge them.
MANUAL_MILESTONE_FIELDS = (
    "name",
    "milestone_type_id",
    "planned_offset_hours",
    "latitude",
    "longitude",
    "notes",
)


class TajTransportTrip(models.Model):
    _inherit = "taj.transport.trip"

    route_id = fields.Many2one(
        "taj.transport.route",
        string="Route Template",
        check_company=True,
        ondelete="restrict",
        copy=False,
        tracking=True,
        domain="[('active', '=', True), ('company_id', '=', company_id)]",
        help="Optional. Selecting a template copies its ordered checkpoints "
        "into this trip as its own milestone records.",
    )
    generated_route_id = fields.Many2one(
        "taj.transport.route",
        string="Checkpoints Generated From",
        readonly=True,
        copy=False,
        ondelete="restrict",
        check_company=True,
        help="Traceability of the last template that generated checkpoints "
        "on this trip.",
    )
    milestone_ids = fields.One2many(
        "taj.transport.milestone",
        "trip_id",
        string="Milestones",
        readonly=True,
        copy=False,
    )
    milestone_count = fields.Integer(
        string="Milestones",
        compute="_compute_milestone_stats",
    )
    milestone_done_count = fields.Integer(
        string="Checkpoints Done",
        compute="_compute_milestone_stats",
    )
    milestone_open_count = fields.Integer(
        string="Open Checkpoints",
        compute="_compute_milestone_stats",
    )
    milestone_progress = fields.Float(
        string="Checkpoint Progress (%)",
        compute="_compute_milestone_stats",
        help="Share of applicable checkpoints that have departed, including "
        "historically completed checkpoints. Skipped checkpoints are excluded. "
        "Reporting only: it never blocks a trip transition.",
    )

    @api.depends(
        "milestone_ids",
        "milestone_ids.state",
        "milestone_ids.actual_arrival_at",
        "milestone_ids.actual_departure_at",
    )
    def _compute_milestone_stats(self):
        for trip in self:
            applicable = trip.milestone_ids.filtered(
                lambda milestone: milestone.state != "skipped"
            )
            done = applicable.filtered(lambda milestone: milestone.state in DONE_STATES)
            trip.milestone_count = len(trip.milestone_ids)
            trip.milestone_done_count = len(done)
            trip.milestone_open_count = len(applicable) - len(done)
            trip.milestone_progress = (
                100.0 * len(done) / len(applicable) if applicable else 0.0
            )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def _milestone_generation_lock(self):
        """Serialize generation with lifecycle transitions on this trip."""
        self.ensure_one()
        self.flush_recordset(["state"])
        self.env.cr.execute(
            "SELECT id FROM taj_transport_trip WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset(["state", "milestone_ids"])

    def _has_recorded_milestone_progress(self):
        """True when at least one checkpoint holds an operational event."""
        self.ensure_one()
        return any(
            milestone.actual_arrival_at
            or milestone.actual_departure_at
            or milestone.confirmed_at
            or milestone.state not in ("pending", "skipped")
            for milestone in self.milestone_ids
        )

    def _milestone_state_label(self):
        """Human readable trip status, for the refusal messages."""
        self.ensure_one()
        return dict(self._fields["state"].selection).get(self.state, self.state)

    def _milestone_audit_event_count(self):
        """Audit rows of this trip (a skipped checkpoint is history too)."""
        self.ensure_one()
        return (
            self.env["taj.transport.milestone.event"]
            .sudo()
            .search_count([("trip_id", "=", self.id)])
        )

    def _check_milestone_change_allowed(self):
        """Discarding planned checkpoints is a Draft-only, history-free act."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(
                _(
                    "Checkpoints can only be re-planned while the trip is in "
                    "Draft: %(trip)s is %(state)s, so its checkpoint plan is "
                    "already in operational use. Add a manual checkpoint for a "
                    "route variance instead; a recorded time can only be "
                    "corrected by a Transport Manager, with a reason."
                )
                % {"trip": self.name, "state": self._milestone_state_label()}
            )
        if self._has_recorded_milestone_progress():
            raise UserError(
                _(
                    "This trip already has recorded checkpoint progress "
                    "(%(trip)s). Recorded checkpoint history is never discarded: "
                    "keep the current checkpoints, or add the missing checkpoint "
                    "instead of regenerating them."
                )
                % {"trip": self.name}
            )
        if self._milestone_audit_event_count():
            raise UserError(
                _(
                    "This trip already has checkpoint history (%(trip)s). "
                    "Recorded checkpoint history is never discarded: add the "
                    "missing checkpoint instead of regenerating the plan."
                )
                % {"trip": self.name}
            )

    def _route_from_value(self, value):
        """Normalize a route value (id, recordset, list or False).

        Direct ORM calls and RPC payloads reach the backend with whatever the
        caller sent, so every entry point normalizes the value the same way
        instead of assuming a browsed recordset.
        """
        route = (
            value
            if isinstance(value, models.BaseModel)
            else self.env["taj.transport.route"].browse(value)
        )
        return route.exists()

    def _check_route_template_usable(self, route):
        """Refuse missing, foreign, unreadable and archived templates.

        The view filter is a convenience only: assignment, creation,
        generation and regeneration all validate the template here, so a
        direct ORM or RPC call cannot apply a template that was archived,
        that belongs to another company, or that the user may not read.
        """
        self.ensure_one()
        route = self._route_from_value(route)
        if not route:
            raise ValidationError(_("Select an existing route template."))
        route.check_access("read")
        if route.company_id != self.company_id:
            raise ValidationError(
                _("The route template %s belongs to another company.") % route.name
            )
        if not route.active:
            raise ValidationError(
                _(
                    "%(route)s is archived. Select an active route template, or "
                    "ask a Transport Manager to activate it again."
                )
                % {"route": route.name}
            )
        return route

    @api.model
    def _prepare_milestone_values(self, trip, route, line):
        return {
            "trip_id": trip.id,
            "route_id": route.id,
            "template_line_id": line.id,
            "sequence": line.sequence or 10,
            "name": line.name,
            "milestone_type_id": line.milestone_type_id.id or False,
            "type_name": line.milestone_type_id.name or False,
            "planned_offset_hours": line.planned_offset_hours or 0.0,
            "latitude": line.latitude or 0.0,
            "longitude": line.longitude or 0.0,
            "notes": line.notes or False,
            "state": "pending",
        }

    def _generate_milestones_from_route(self, route, replace_pending=False):
        """Copy the route's checkpoints into this trip; safe to retry.

        Planning is a Draft-state activity, and the template must be usable
        (existing, active, same company, readable). Both are enforced here so
        that no direct ORM or RPC call can bypass the view filters or plan a
        trip whose operational plan is already in use.

        Returns the newly created milestones. Existing rows are matched by
        their template checkpoint, so repeating the call creates nothing.
        """
        self.ensure_one()
        self.check_access("write")
        route = self._check_route_template_usable(route)
        self._milestone_generation_lock()
        if self.state != "draft":
            raise UserError(
                _(
                    "Checkpoints can only be generated while the trip is in "
                    "Draft: %(trip)s is %(state)s, so its checkpoint plan is "
                    "already in operational use. Add a manual checkpoint for a "
                    "route variance instead."
                )
                % {"trip": self.name, "state": self._milestone_state_label()}
            )
        if replace_pending:
            self._check_milestone_change_allowed()
            self._discard_generated_pending_milestones()
        existing_lines = set(self.milestone_ids.mapped("template_line_id").ids)
        missing = route.line_ids.filtered(
            lambda line: line.id not in existing_lines
        ).sorted(key=lambda line: (line.sequence, line.id))
        Milestone = self.env["taj.transport.milestone"].sudo()
        created = (
            Milestone.create(
                [self._prepare_milestone_values(self, route, line) for line in missing]
            )
            if missing
            else Milestone
        )
        # ``super()`` skips this file's ``write()`` guard on purpose: the
        # generation workflow is the single allowed writer of the marker.
        super(TajTransportTrip, self).write({"generated_route_id": route.id})
        self.invalidate_recordset(["milestone_ids"])
        if created:
            # Label hoisted out of the generator: _() called from a genexpr
            # frame is not translatable (Odoo logs a spurious warning).
            no_type = _("No type")
            items = Markup("").join(
                Markup("<li>{}</li>").format(
                    "%s (%s)" % (milestone.name, milestone.type_name or no_type)
                )
                for milestone in created
            )
            self.message_post(
                body=Markup("<p>{header}</p><ul>{items}</ul>").format(
                    header=_(
                        "%(count)s checkpoint(s) generated from route template "
                        "%(route)s:"
                    )
                    % {"count": len(created), "route": route.display_name},
                    items=items,
                )
            )
        return created

    def _discard_generated_pending_milestones(self):
        """Remove untouched, template-generated checkpoints only.

        Rows with any recorded event are never removable. Manually added
        checkpoints (no template line) are always preserved.
        """
        self.ensure_one()
        generated = self.milestone_ids.filtered("template_line_id")
        if generated:
            generated._unlink_pending_for_regeneration()
        self.invalidate_recordset(["milestone_ids"])

    def _clear_generated_milestones(self):
        """Forget the selected template of a trip that has not started.

        Manually added checkpoints are always kept; only checkpoints that were
        generated from a template and hold no event at all are removed. The
        generation marker is cleared in the same transaction, so a trip never
        keeps a marker for a template it no longer selects.
        """
        self.ensure_one()
        self._milestone_generation_lock()
        self._check_milestone_change_allowed()
        generated = self.milestone_ids.filtered("template_line_id")
        had_marker = bool(self.generated_route_id)
        if generated:
            generated._unlink_pending_for_regeneration()
        # ``super()`` skips this file's ``write()`` guard on purpose: clearing
        # the template is the second (and last) allowed writer of the marker.
        super(TajTransportTrip, self).write({"generated_route_id": False})
        self.invalidate_recordset(["milestone_ids"])
        if generated or had_marker:
            self.message_post(
                body=Markup("<p>{}</p>").format(
                    _(
                        "Route template cleared: %(count)s generated "
                        "checkpoint(s) removed, manually added checkpoints kept."
                    )
                    % {"count": len(generated)}
                )
            )

    def _check_route_change_allowed(self, new_route_id):
        """Validate a template change, including clearing it, before writing.

        Re-selecting the template that generated the checkpoints changes
        nothing and stays allowed. Every other change is a planning decision:
        it is only accepted while the trip is in Draft and no checkpoint event
        or audit entry exists. Ready and In Progress trips keep the plan they
        were released with; Completed and Cancelled trips are closed.
        """
        for trip in self:
            new_route = trip._route_from_value(new_route_id)
            if not new_route and not trip.generated_route_id:
                # No template selected and nothing was ever generated.
                continue
            if new_route and trip.generated_route_id.id == new_route.id:
                continue
            if trip.state != "draft":
                raise UserError(
                    _(
                        "The route template can only be changed while the trip "
                        "is in Draft: %(trip)s is %(state)s, so its checkpoint "
                        "plan is already in operational use. Keep the template, "
                        "or add a manual checkpoint for a route variance."
                    )
                    % {"trip": trip.name, "state": trip._milestone_state_label()}
                )
            if trip._has_recorded_milestone_progress():
                raise UserError(
                    _(
                        "The route template cannot be changed: this trip already "
                        "has recorded checkpoint progress, and checkpoint history "
                        "is never overwritten. Add the missing checkpoint instead."
                    )
                )
            if trip._milestone_audit_event_count():
                raise UserError(
                    _(
                        "The route template cannot be changed: this trip already "
                        "has checkpoint history, and checkpoints with recorded "
                        "events are never discarded. Add the missing checkpoint "
                        "instead."
                    )
                )
            if new_route:
                trip._check_route_template_usable(new_route)

    def _sync_milestones_with_route(self, route_id):
        """Apply the trip's selected template after it was saved.

        Clearing the template removes the generated checkpoints and the
        marker; selecting another one replaces the generated, unrecorded
        checkpoints. Both are refused before the write by
        ``_check_route_change_allowed`` as soon as the trip left Draft.
        """
        for trip in self:
            route = trip._route_from_value(route_id)
            if not route:
                # Clearing a trip that never generated a checkpoint is a
                # genuine no-op: it must stay possible whatever the trip state.
                if trip.generated_route_id or trip.milestone_ids.filtered(
                    "template_line_id"
                ):
                    trip._clear_generated_milestones()
                continue
            if trip.generated_route_id.id == route.id:
                continue
            trip._generate_milestones_from_route(route, replace_pending=True)

    def _regenerate_milestones_from_route(self):
        """Explicit, confirmed regeneration from the trip's current template."""
        self.ensure_one()
        self.check_access("write")
        if not self.route_id:
            raise UserError(
                _("Select a route template before regenerating checkpoints.")
            )
        return self._generate_milestones_from_route(
            self.route_id,
            replace_pending=True,
        )

    def _add_manual_milestone(self, vals):
        """Add one checkpoint that the route template does not cover.

        Used for operational route variances, including on a trip whose plan
        is already in use: it is additive, so it destroys nothing. The new
        checkpoint is placed after the existing ones and is never generated
        from a template, so a later regeneration keeps it. Only the values of
        ``MANUAL_MILESTONE_FIELDS`` are accepted, so a caller cannot inject a
        state, a timestamp or a snapshot.
        """
        self.ensure_one()
        self.check_access("write")
        name = (vals.get("name") or "").strip()
        if not name:
            raise ValidationError(_("Enter the name of the checkpoint."))
        rejected = sorted(set(vals) - set(MANUAL_MILESTONE_FIELDS))
        if rejected:
            raise UserError(
                _(
                    "The checkpoint workflow decides these values, they cannot "
                    "be provided: %s"
                )
                % ", ".join(rejected)
            )
        self._milestone_generation_lock()
        if self.state in ("completed", "cancelled"):
            raise UserError(
                _("Checkpoints cannot be added to a completed or cancelled trip.")
            )
        sequences = self.milestone_ids.mapped("sequence") or [0]
        milestone = (
            self.env["taj.transport.milestone"]
            .sudo()
            .create(
                {
                    **vals,
                    "name": name,
                    "trip_id": self.id,
                    "sequence": max(sequences) + 10,
                    "state": "pending",
                }
            )
        )
        self.invalidate_recordset(["milestone_ids"])
        self.message_post(
            body=Markup("<p>{}</p>").format(
                _("Checkpoint added manually: %s") % milestone.name,
            )
        )
        return milestone

    def action_generate_milestones(self):
        """Explicit, idempotent generation for trips created without a route."""
        self.ensure_one()
        self.check_access("write")
        if not self.route_id:
            raise UserError(
                _("Select a route template on the trip before generating checkpoints.")
            )
        created = self._generate_milestones_from_route(self.route_id)
        if not created:
            raise UserError(
                _("All checkpoints of %s are already present on this trip.")
                % self.route_id.display_name
            )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_regenerate_milestones_wizard(self):
        self.ensure_one()
        self.check_access("write")
        if not self.route_id:
            raise UserError(
                _("Select a route template before regenerating checkpoints.")
            )
        # Re-planning is a Draft-only decision: refuse before opening the
        # wizard instead of failing at the end of it.
        self._check_milestone_change_allowed()
        return {
            "type": "ir.actions.act_window",
            "name": _("Regenerate Checkpoints"),
            "res_model": "taj.transport.regenerate.milestones.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_trip_id": self.id},
        }

    def action_open_add_milestone_wizard(self):
        self.ensure_one()
        self.check_access("write")
        return {
            "type": "ir.actions.act_window",
            "name": _("Add Trip Checkpoint"),
            "res_model": "taj.transport.milestone.add.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_trip_id": self.id},
        }

    def action_open_trip_milestones(self):
        self.ensure_one()
        action = self.env.ref(
            "taj_transport_operations.action_transport_milestone"
        ).read()[0]
        action["domain"] = [("trip_id", "=", self.id)]
        action["context"] = {
            "search_default_group_vehicle": 1, "search_default_group_trip": 2,
        }
        return action

    # ------------------------------------------------------------------
    # create / write integration
    # ------------------------------------------------------------------
    def _location_updates_for_new_route(self, new_route):
        """Draft route change: copy only non-empty template locations.

        Same route is a no-op (including non-draft trips). Clearing the
        template retains existing locations. Applies to every entry point
        that writes ``route_id``, including Fill Empty Fields.
        """
        self.ensure_one()
        new_route = self._route_from_value(new_route)
        current_id = self.route_id.id if self.route_id else False
        new_id = new_route.id if new_route else False
        if current_id == new_id:
            return {}
        if self.state != "draft":
            return {}
        if not new_route:
            return {}
        updates = {}
        if (new_route.origin_location or "").strip():
            updates["origin_location"] = new_route.origin_location
        if (new_route.destination_location or "").strip():
            updates["destination_location"] = new_route.destination_location
        return updates

    @api.onchange("route_id")
    def _onchange_route_id_suggest_locations(self):
        """Mirror Draft write on genuine template changes and new records.

        Re-selecting the same template on a persisted trip is a no-op.
        Non-Draft trips never rewrite locations from this onchange. Blank
        template fields keep the form value. Never writes back to Sales.
        """
        for trip in self:
            if not trip.route_id:
                continue
            origin = trip._origin
            if origin.ids:
                if origin.state != "draft":
                    continue
                if origin.route_id.id == trip.route_id.id:
                    continue
            if (trip.route_id.origin_location or "").strip():
                trip.origin_location = trip.route_id.origin_location
            if (trip.route_id.destination_location or "").strip():
                trip.destination_location = trip.route_id.destination_location

    @api.model_create_multi
    def create(self, vals_list):
        self._check_system_field_values(vals_list)
        trips = super().create(vals_list)
        # A template selected at creation time is validated (existing, active,
        # same company, readable) and generates its checkpoints once, in the
        # same transaction as the trip itself, so a refused template leaves no
        # trip behind.
        for trip in trips.filtered("route_id"):
            trip._check_route_template_usable(trip.route_id)
            trip._generate_milestones_from_route(trip.route_id)
        return trips

    def write(self, vals):
        self._check_system_field_values([vals])
        route_selected = "route_id" in vals
        if route_selected:
            # Validated before writing so a refused template change leaves
            # the trip, its checkpoints and their history untouched.
            self._check_route_change_allowed(vals["route_id"])
            # Multi-record writes may need different location updates per trip
            # (each keeps its own filled blanks). Group by the location delta.
            new_route_id = vals["route_id"]
            groups = {}
            for trip in self:
                loc_key = frozenset(
                    trip._location_updates_for_new_route(new_route_id).items()
                )
                groups.setdefault(loc_key, self.browse())
                groups[loc_key] |= trip
            result = True
            for loc_items, trips in groups.items():
                trip_vals = dict(vals)
                trip_vals.update(dict(loc_items))
                result = super(TajTransportTrip, trips).write(trip_vals) and result
                trips._sync_milestones_with_route(new_route_id)
            return result
        return super().write(vals)

    def _check_system_field_values(self, vals_list):
        """Refuse hand-written values for the generation marker.

        ``generated_route_id`` records which template a trip's checkpoints
        were generated from. Only the generation workflow sets it, writing
        through ``super()`` past this guard.
        """
        for vals in vals_list:
            if "generated_route_id" in vals:
                raise UserError(
                    _("The generated checkpoints marker is system-managed.")
                )

    # ------------------------------------------------------------------
    # Timeline integrity
    # ------------------------------------------------------------------
    def _validate_milestone_timeline(self, pending_values=None):
        """Reject impossible or out-of-order checkpoint chronology.

        ``pending_values`` maps a milestone id to the values about to be
        written, so a refused change can be reported before it is stored.
        Checkpoints are compared in route order: no checkpoint may be
        timestamped before an earlier checkpoint's recorded events, which
        keeps one coherent operational timeline per trip.
        """
        self.ensure_one()
        pending_values = pending_values or {}
        now = fields.Datetime.now()
        trip_end = (
            self.actual_arrival_at
            if self.state in ("in_progress", "completed")
            else False
        )
        applicable = self.milestone_ids.filtered(
            lambda milestone: milestone.state != "skipped"
        ).sorted(key=lambda milestone: (milestone.sequence, milestone.id))
        last_value, last_label = False, False
        for milestone in applicable:
            overrides = pending_values.get(milestone.id, {})
            arrival = overrides.get(
                "actual_arrival_at",
                milestone.actual_arrival_at,
            )
            departure = overrides.get(
                "actual_departure_at",
                milestone.actual_departure_at,
            )
            if arrival and departure and departure < arrival:
                raise ValidationError(
                    _(
                        "The departure of %(milestone)s cannot be earlier than "
                        "its arrival."
                    )
                    % {"milestone": milestone.name}
                )
            for label, value in ((_("Arrival"), arrival), (_("Departure"), departure)):
                if not value:
                    continue
                if value > now:
                    raise ValidationError(
                        _("%(label)s of %(milestone)s cannot be in the future.")
                        % {"label": label, "milestone": milestone.name}
                    )
                if trip_end and value > trip_end:
                    raise ValidationError(
                        _(
                            "%(label)s of %(milestone)s (%(value)s) is later than "
                            "the trip's actual arrival (%(end)s). Correct the trip "
                            "timeline first."
                        )
                        % {
                            "label": label,
                            "milestone": milestone.name,
                            "value": value,
                            "end": trip_end,
                        }
                    )
                if last_value and value < last_value:
                    raise ValidationError(
                        _(
                            "Checkpoints follow the route order: %(label)s of "
                            "%(milestone)s (%(value)s) cannot be earlier than the "
                            "recorded %(earlier)s."
                        )
                        % {
                            "label": label,
                            "milestone": milestone.name,
                            "value": value,
                            "earlier": last_label,
                        }
                    )
            for label, value in ((_("Arrival"), arrival), (_("Departure"), departure)):
                if value and (not last_value or value > last_value):
                    last_value = value
                    last_label = "%s - %s (%s)" % (
                        milestone.name,
                        label,
                        value,
                    )
        return True
