"""Transport trip foundation; invoicing and operational assignments stay separate."""

import re
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class TajTransportTrip(models.Model):
    _name = "taj.transport.trip"
    _description = "TAJ Transport Trip"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True

    _unique_sale_line = models.Constraint(
        "UNIQUE(sale_line_id)",
        "A sales order line can only be linked to one transport trip.",
    )

    name = fields.Char(
        string="Trip Reference", required=True, readonly=True,
        copy=False, default=lambda self: _("New"), index=True,
    )
    sale_line_id = fields.Many2one(
        "sale.order.line", string="Sales Order Line",
        required=True, ondelete="restrict", index=True, check_company=True,
        copy=False,
    )
    sale_order_id = fields.Many2one(
        "sale.order", string="Sales Order",
        related="sale_line_id.order_id", store=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        "res.company", related="sale_order_id.company_id",
        store=True, readonly=True, index=True,
    )
    partner_id = fields.Many2one(
        "res.partner", related="sale_order_id.partner_id",
        store=True, readonly=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft", string="Status",
        required=True, readonly=True, tracking=True, copy=False,
    )
    ready_at = fields.Datetime(
        string="Marked Ready At", readonly=True, copy=False,
    )
    started_at = fields.Datetime(
        string="Started At", readonly=True, copy=False,
    )
    completed_at = fields.Datetime(
        string="Completed At", readonly=True, copy=False,
    )
    cancelled_at = fields.Datetime(
        string="Cancelled At", readonly=True, copy=False,
    )
    cancellation_reason = fields.Text(
        string="Cancellation Reason", readonly=True, copy=False,
    )
    cancelled_by_id = fields.Many2one(
        "res.users", string="Cancelled By", readonly=True, copy=False,
    )

    _workflow_fields = frozenset({
        "state", "ready_at", "started_at", "completed_at",
        "cancelled_at", "cancellation_reason", "cancelled_by_id",
    })
    _operational_fields = frozenset({
        "transporter_id", "transport_reference", "cargo_type", "cargo_type_id",
        "cargo_description", "special_handling_instructions",
        "origin_location", "destination_location", "route_notes",
        "planned_departure_at", "planned_arrival_at",
        "actual_departure_at", "actual_arrival_at",
        # Phase 3: selected route template (guarded by the milestone mixin).
        "route_id",
    })
    initial_vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Truck at Trip Creation",
        readonly=True, copy=False,
        help="Snapshot of the sales line's original truck. Future trip "
             "truck replacements will not update the sales line or invoice.",
    )

    sale_description = fields.Text(
        related="sale_line_id.name", string="Sales Line Description",
        readonly=True,
    )
    container_num = fields.Char(
        related="sale_line_id.container_num", readonly=True,
    )
    srn = fields.Char(related="sale_line_id.srn", readonly=True)
    weight = fields.Char(related="sale_line_id.weight", readonly=True)
    size = fields.Char(related="sale_line_id.size", readonly=True)
    consignee = fields.Char(related="sale_line_id.consignee", readonly=True)
    currency_id = fields.Many2one(
        "res.currency", related="sale_order_id.currency_id", readonly=True,
    )
    price_unit = fields.Float(
        string="Sales Unit Rate", related="sale_line_id.price_unit",
        readonly=True,
    )

    # Commercial source-of-truth stays on Sales. These non-stored related
    # values always reflect Sales and cannot be edited through a Trip.
    customer_reference = fields.Char(
        string="Customer Reference",
        related="sale_order_id.client_order_ref", readonly=True,
    )
    source_document = fields.Char(
        string="Source Document",
        related="sale_order_id.origin", readonly=True,
    )
    payment_term_id = fields.Many2one(
        "account.payment.term", string="Sales Payment Terms",
        related="sale_order_id.payment_term_id", readonly=True,
    )
    cargo_quantity = fields.Float(
        string="Sales Line Quantity",
        related="sale_line_id.product_uom_qty", readonly=True,
        help="Quantity recorded on the sales line, not the number of trips.",
    )
    cargo_uom_id = fields.Many2one(
        "uom.uom", string="Sales Quantity Unit",
        related="sale_line_id.product_uom_id", readonly=True,
    )
    file_name = fields.Char(
        string="Sales File Name",
        related="sale_line_id.file_name", readonly=True,
    )

    # Dispatcher-owned details. Never write these back to Sales or Accounting.
    # Tracked fields record changes, authors and timestamps in the Chatter.
    transporter_id = fields.Many2one(
        "taj.transport.transporter", string="Transporter", tracking=True,
        ondelete="restrict", copy=False,
        help="Transporter responsible for executing this transport, maintained "
             "under Transport Operations > Configuration > Transporters.",
    )
    transport_reference = fields.Char(
        string="Transport / Booking Reference", tracking=True, copy=False,
        help="Optional dispatcher or carrier reference for this trip.",
    )
    # Keep the historical Char column so existing trips, integrations and
    # change-tracking retain their original text after the catalog upgrade.
    # Only cargo_type_id is displayed in the UI as a searchable dropdown.
    cargo_type = fields.Char(
        string="Cargo Type", tracking=True, copy=False,
        help="Historical cargo type text, synchronized with the dropdown.",
    )
    cargo_type_id = fields.Many2one(
        "taj.transport.cargo.type", string="Cargo Type",
        tracking=False, copy=False, index=True, ondelete="restrict",
        help="Choose a type maintained by the Transport Manager.",
    )
    cargo_description = fields.Text(
        string="Operational Cargo Description", tracking=True, copy=False,
        help="What is actually being transported. Kept separate from "
             "the read-only Sales Line Description.",
    )
    special_handling_instructions = fields.Text(
        string="Special Handling Instructions", tracking=True, copy=False,
        help="Optional loading, storage, security or handling instructions.",
    )

    # Dispatcher-entered route and timing: no route template or GPS automation
    # is introduced here (those are separate future phases).
    origin_location = fields.Char(
        string="Origin", tracking=True, copy=False,
        help="Free-text starting location for this trip.",
    )
    destination_location = fields.Char(
        string="Destination", tracking=True, copy=False,
        help="Free-text delivery destination for this trip.",
    )
    planned_departure_at = fields.Datetime(
        string="Planned Departure", tracking=True, copy=False,
    )
    planned_arrival_at = fields.Datetime(
        string="Planned Arrival", tracking=True, copy=False,
    )
    actual_departure_at = fields.Datetime(
        string="Actual Departure", tracking=True, copy=False,
    )
    actual_arrival_at = fields.Datetime(
        string="Actual Arrival", tracking=True, copy=False,
    )
    route_notes = fields.Text(
        string="Route Instructions", tracking=True, copy=False,
    )

    @api.constrains(
        "planned_departure_at", "planned_arrival_at",
        "actual_departure_at", "actual_arrival_at",
    )
    def _check_route_timing(self):
        for trip in self:
            if (
                trip.planned_departure_at and trip.planned_arrival_at
                and trip.planned_arrival_at < trip.planned_departure_at
            ):
                raise ValidationError(_(
                    "Planned arrival cannot be earlier than planned departure."
                ))
            if (
                trip.actual_departure_at and trip.actual_arrival_at
                and trip.actual_arrival_at < trip.actual_departure_at
            ):
                raise ValidationError(_(
                    "Actual arrival cannot be earlier than actual departure."
                ))

    # Keep the original Sales truck snapshot untouched. The operational
    # assignment is independently time-based for future replacements.
    assignment_ids = fields.One2many(
        "taj.transport.assignment", "trip_id",
        string="Assignment History", readonly=True,
    )
    current_assignment_id = fields.Many2one(
        "taj.transport.assignment", string="Current Assignment",
        compute="_compute_current_assignment", store=True,
        readonly=True, index=True,
    )
    current_vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Current Truck",
        related="current_assignment_id.vehicle_id", readonly=True,
    )
    current_trailer_id = fields.Many2one(
        "fleet.vehicle", string="Current Trailer",
        related="current_assignment_id.trailer_id", readonly=True,
    )
    current_driver_id = fields.Many2one(
        "res.partner", string="Current Driver",
        related="current_assignment_id.driver_id", readonly=True,
    )
    current_co_driver_id = fields.Many2one(
        "res.partner", string="Current Co-driver",
        related="current_assignment_id.co_driver_id", readonly=True,
    )

    # Compute the last assignment and its resource snapshot together.
    # Related fields through a *non-stored computed* last_assignment_id
    # cannot resolve inverse dependency triggers in Odoo 19: writing
    # fleet.vehicle then emits "last_assignment_id should be searchable".
    # Direct dependencies on assignment_ids are searchable and preserve
    # accurate history without adding a stored column or migration.
    last_assignment_id = fields.Many2one(
        "taj.transport.assignment", string="Last Assignment",
        compute="_compute_last_assignment", readonly=True,
    )
    last_vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Last Truck",
        compute="_compute_last_assignment", readonly=True,
    )
    last_trailer_id = fields.Many2one(
        "fleet.vehicle", string="Last Trailer",
        compute="_compute_last_assignment", readonly=True,
    )
    last_driver_id = fields.Many2one(
        "res.partner", string="Last Driver",
        compute="_compute_last_assignment", readonly=True,
    )
    last_co_driver_id = fields.Many2one(
        "res.partner", string="Last Co-driver",
        compute="_compute_last_assignment", readonly=True,
    )

    @api.depends(
        "assignment_ids", "assignment_ids.effective_from",
        "assignment_ids.vehicle_id", "assignment_ids.trailer_id",
        "assignment_ids.driver_id", "assignment_ids.co_driver_id",
    )
    def _compute_last_assignment(self):
        for trip in self:
            last = trip.assignment_ids.sorted(
                key=lambda assignment: (assignment.effective_from, assignment.id),
                reverse=True,
            )[:1]
            trip.last_assignment_id = last
            trip.last_vehicle_id = last.vehicle_id
            trip.last_trailer_id = last.trailer_id
            trip.last_driver_id = last.driver_id
            trip.last_co_driver_id = last.co_driver_id

    @api.depends("assignment_ids", "assignment_ids.effective_to")
    def _compute_current_assignment(self):
        for trip in self:
            current = trip.assignment_ids.filtered(
                lambda row: not row.effective_to
            )
            trip.current_assignment_id = current[:1]

    def action_open_initial_assignment_wizard(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_(
                "Initial resources can only be assigned on Draft trips."
            ))
        if self.current_assignment_id:
            raise UserError(_(
                "An active assignment already exists. Use the upcoming "
                "replacement workflow instead of overwriting history."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("Assign Initial Resources"),
            "res_model": "taj.transport.initial.assignment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_trip_id": self.id,
                "default_vehicle_id": self.initial_vehicle_id.id or False,
            },
        }

    def action_open_replace_assignment_wizard(self):
        self.ensure_one()
        self.check_access("write")
        if self.state not in ("draft", "ready", "in_progress"):
            raise UserError(_(
                "Resources cannot be replaced after completion or cancellation."
            ))
        current = self.current_assignment_id
        if not current:
            raise UserError(_(
                "Assign initial resources before making a replacement."
            ))
        current.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Replace Trip Resources"),
            "res_model": "taj.transport.replace.assignment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_trip_id": self.id,
                "default_expected_assignment_id": current.id,
                "default_vehicle_id": current.vehicle_id.id or False,
                "default_trailer_id": current.trailer_id.id or False,
                "default_driver_id": current.driver_id.id or False,
                "default_co_driver_id": current.co_driver_id.id or False,
            },
        }

    @api.model
    def _validate_sale_line(self, sale_line):
        if not sale_line.exists():
            raise ValidationError(_("Select an existing sales order line."))
        if sale_line.order_id.state not in ("sale", "done"):
            raise ValidationError(_(
                "Create transport trips only from confirmed sales orders."
            ))
        if (
            sale_line.display_type not in (False, "product")
            or not sale_line.product_id
        ):
            raise ValidationError(_(
                "Only product/service lines can create transport trips, "
                "not section or note lines."
            ))

    @api.constrains("sale_line_id")
    def _check_sale_line(self):
        for trip in self:
            trip._validate_sale_line(trip.sale_line_id)

    @api.model
    def _route_from_product_name(self, name):
        """Extract a simple Origin - Destination pair, ignoring cargo specs.

        Read the product template name (not the Sales line description or
        display_name, which may contain product codes/variants). A product
        that does not have exactly one spaced route delimiter is not a
        safe source for a route suggestion.
        """
        name = (name or "").strip()
        # Remove an optional cargo-spec suffix *before* splitting, since
        # container descriptions themselves sometimes contain dashes.
        # Keep meaningful place qualifiers such as "(Port)".
        spec = re.search(r"\s+\(([^()]*)\)\s*$", name)
        if spec and (
            "," in spec.group(1)
            or re.search(
                r"\b(?:20|40|45)\s*(?:ft|feet|hc)\b", spec.group(1), re.I,
            )
        ):
            name = name[:spec.start()].strip()
        parts = re.split(r"\s+[-\u2013\u2014]\s+", name)
        if len(parts) != 2:
            return False, False
        origin, destination = (part.strip() for part in parts)
        if not origin or not destination:
            return False, False
        return origin, destination

    @api.model
    def _route_from_sale_line_product(self, line):
        if not line or not line.product_id:
            return False, False
        name = line.product_id.product_tmpl_id.name
        return self._route_from_product_name(name)

    @api.model
    def _suggest_missing_route_values(self, vals, line):
        if vals.get("origin_location") and vals.get("destination_location"):
            return vals
        origin, destination = self._route_from_sale_line_product(line)
        if origin and not (vals.get("origin_location") or "").strip():
            vals["origin_location"] = origin
        if destination and not (vals.get("destination_location") or "").strip():
            vals["destination_location"] = destination
        return vals

    @api.onchange("sale_line_id")
    def _onchange_sale_line_suggest_route(self):
        # Standalone trip creation: suggest from the selected product before
        # saving. Operators may edit either field, including independently.
        for trip in self:
            origin, destination = trip._route_from_sale_line_product(trip.sale_line_id)
            if origin and not (trip.origin_location or "").strip():
                trip.origin_location = origin
            if destination and not (trip.destination_location or "").strip():
                trip.destination_location = destination

    def action_prefill_route_from_product(self):
        """Opt-in suggestion for existing Draft trips; never overwrite routes."""
        self.ensure_one()
        self.check_access("write")
        if self.state != "draft":
            raise UserError(_("Route suggestion is only available for Draft trips."))
        vals = self._suggest_missing_route_values({}, self.sale_line_id)
        missing = {
            key: value for key, value in vals.items()
            if not (getattr(self, key) or "").strip()
        }
        if not vals:
            raise UserError(_(
                "The Sales product name does not contain a clear "
                "'Origin - Destination' route. Enter locations manually."
            ))
        if missing:
            self.write(missing)
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.model
    def _sync_cargo_type_values(self, entry):
        """Synchronize the dropdown and existing textual cargo type field."""
        vals = dict(entry)
        CargoType = self.env["taj.transport.cargo.type"].sudo()
        if "cargo_type_id" in vals:
            type_id = vals["cargo_type_id"]
            cargo_type = CargoType.browse(type_id).exists() if type_id else CargoType
            if type_id and (not cargo_type or not cargo_type.active):
                raise ValidationError(_("Choose an active cargo type."))
            vals["cargo_type"] = cargo_type.name if type_id else False
        elif "cargo_type" in vals:
            # Legacy API callers can still write the original text. Map known
            # names without creating a catalog entry or discarding their text.
            name = (vals["cargo_type"] or "").strip()
            cargo_type = CargoType.with_context(active_test=False).search(
                [("name", "=ilike", name)], limit=1
            ) if name else CargoType
            vals["cargo_type_id"] = cargo_type.id or False
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        new_vals_list = []
        for entry in vals_list:
            vals = self._sync_cargo_type_values(entry)
            if not vals.get("sale_line_id"):
                raise ValidationError(_("A sales order line is required."))
            line = self.env["sale.order.line"].browse(
                vals["sale_line_id"]
            ).exists()
            self._validate_sale_line(line)
            # Creation via Sales button, batch wizard, API or Trip form all
            # pass through this defaulting logic. Explicit values win.
            vals = self._suggest_missing_route_values(vals, line)
            if vals.get("state", "draft") != "draft":
                raise ValidationError(_("Transport trips must start in Draft."))
            if self._workflow_fields.intersection(vals) - {"state"}:
                raise ValidationError(_(
                    "Workflow timestamps and cancellation metadata are system-managed."
                ))
            if vals.get("company_id") and vals["company_id"] != line.company_id.id:
                raise ValidationError(_("Company must match the sales order."))
            # Ignore caller supplied values: source assignments are immutable.
            vals["initial_vehicle_id"] = line.vehicle_id.id or False
            # Trip references are server-generated, never caller-controlled.
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("taj.transport.trip")
                or _("New")
            )
            new_vals_list.append(vals)
        return super().create(new_vals_list)

    def write(self, vals):
        vals = self._sync_cargo_type_values(vals)
        if "sale_line_id" in vals:
            for trip in self:
                if vals["sale_line_id"] != trip.sale_line_id.id:
                    raise UserError(_(
                        "The sales order line cannot be changed after a trip "
                        "is created."
                    ))
        if "initial_vehicle_id" in vals or "name" in vals:
            raise UserError(_(
                "Trip reference and original truck are immutable."
            ))
        if self._workflow_fields.intersection(vals):
            raise UserError(_(
                "Trip states and workflow audit fields may only be changed "
                "through their dedicated actions."
            ))
        if self._operational_fields.intersection(vals):
            # Serialize with lifecycle transitions. A stale form submission
            # must not modify an already completed/cancelled trip.
            self.flush_recordset(["state"])
            self.env.cr.execute(
                "SELECT id FROM taj_transport_trip WHERE id = ANY(%s) "
                "ORDER BY id FOR UPDATE",
                (sorted(self.ids),),
            )
            self.invalidate_recordset(["state"])
            if any(trip.state in ("completed", "cancelled") for trip in self):
                raise UserError(_(
                    "Completed or cancelled trips cannot be edited."
                ))
        return super().write(vals)

    def _lock_for_transition(self):
        """Serialize lifecycle actions and resource swaps on this trip."""
        self.ensure_one()
        self.check_access("write")
        self.flush_recordset(["state"])
        self.env.cr.execute(
            "SELECT id FROM taj_transport_trip WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset(["state", "current_assignment_id"])

    def _locked_active_assignment(self):
        """Fetch the actual open resource period, not a stale computed value."""
        self.ensure_one()
        self.env["taj.transport.assignment"].flush_model([
            "trip_id", "effective_to",
        ])
        self.env.cr.execute(
            "SELECT id FROM taj_transport_assignment "
            "WHERE trip_id = %s AND effective_to IS NULL FOR UPDATE",
            (self.id,),
        )
        ids = [row[0] for row in self.env.cr.fetchall()]
        if len(ids) != 1:
            raise ValidationError(_(
                "The trip needs exactly one active resource assignment."
            ))
        assignment = self.env["taj.transport.assignment"].browse(ids[0])
        assignment.check_access("read")
        assignment.invalidate_recordset([
            "effective_from", "effective_to", "vehicle_id",
            "trailer_id", "driver_id", "co_driver_id",
        ])
        return assignment

    def _validate_ready_requirements(self, assignment):
        self.ensure_one()
        if self.sale_order_id.state not in ("sale", "done"):
            raise ValidationError(_(
                "The linked Sales Order must remain confirmed."
            ))
        if not (self.origin_location or "").strip():
            raise ValidationError(_("Enter an origin before marking the trip Ready."))
        if not (self.destination_location or "").strip():
            raise ValidationError(_("Enter a destination before marking the trip Ready."))
        if not self.planned_departure_at or not self.planned_arrival_at:
            raise ValidationError(_(
                "Both planned departure and planned arrival are required."
            ))
        if self.planned_arrival_at <= self.planned_departure_at:
            raise ValidationError(_(
                "Planned arrival must be later than planned departure."
            ))
        if not assignment.vehicle_id:
            raise ValidationError(_("Assign a truck before marking the trip Ready."))
        if not assignment.driver_id:
            raise ValidationError(_("Assign a driver before marking the trip Ready."))
        if not assignment.driver_id.active:
            raise ValidationError(_("The primary driver must be an active contact."))
        for kind, vehicle in (
            ("truck", assignment.vehicle_id),
            ("trailer", assignment.trailer_id),
        ):
            if vehicle:
                category = self.env.ref(
                    "taj_transport_operations.fleet_category_" + kind,
                )
                if not vehicle.active or vehicle.category_id != category:
                    raise ValidationError(_(
                        "%s must be active and categorized as %s in Fleet."
                    ) % (vehicle.display_name, kind.title()))
                if vehicle.company_id and vehicle.company_id != self.company_id:
                    raise ValidationError(_(
                        "Assigned Fleet resources must belong to the trip company."
                    ))
        for partner in (assignment.driver_id, assignment.co_driver_id):
            if partner and partner.company_id and partner.company_id != self.company_id:
                raise ValidationError(_(
                    "Assigned drivers must belong to the trip company."
                ))

    def action_mark_ready(self):
        self.ensure_one()
        self._lock_for_transition()
        if self.state != "draft":
            raise UserError(_("Only Draft trips can be marked Ready."))
        assignment = self._locked_active_assignment()
        self._validate_ready_requirements(assignment)
        # Deliberately bypass *this* model's direct state-write guard only
        # after access, source, resources and chronology have been checked.
        super(TajTransportTrip, self).write({
            "state": "ready",
            "ready_at": fields.Datetime.now(),
        })
        return True

    def action_start_trip(self):
        self.ensure_one()
        self._lock_for_transition()
        if self.state != "ready":
            raise UserError(_("Only Ready trips can be started."))
        assignment = self._locked_active_assignment()
        self._validate_ready_requirements(assignment)
        now = fields.Datetime.now()
        if assignment.effective_from > now:
            raise ValidationError(_(
                "The active resource assignment has not started yet."
            ))
        if self.actual_arrival_at:
            raise ValidationError(_(
                "Clear the actual arrival before starting the trip."
            ))
        departure = self.actual_departure_at or now
        if departure > now:
            raise ValidationError(_("Actual departure cannot be in the future."))
        if departure < assignment.effective_from:
            raise ValidationError(_(
                "Actual departure cannot precede the current resource assignment."
            ))
        super(TajTransportTrip, self).write({
            "state": "in_progress",
            "started_at": now,
            "actual_departure_at": departure,
        })
        return True

    def action_complete_trip(self):
        self.ensure_one()
        self._lock_for_transition()
        if self.state != "in_progress":
            raise UserError(_("Only In Progress trips can be completed."))
        assignment = self._locked_active_assignment()
        now = fields.Datetime.now()
        departure = self.actual_departure_at
        if not departure:
            raise ValidationError(_(
                "The trip needs an actual departure time before completion."
            ))
        arrival = self.actual_arrival_at or now
        if arrival > now:
            raise ValidationError(_("Actual arrival cannot be in the future."))
        if arrival <= departure:
            raise ValidationError(_(
                "Actual arrival must be later than actual departure."
            ))
        if arrival < assignment.effective_from:
            raise ValidationError(_(
                "Actual arrival cannot precede the current assignment."
            ))
        # Closing the final period releases the resources. Last Assignment
        # remains visible on the trip, without claiming they are still active.
        assignment._close_for_trip_end(arrival)
        super(TajTransportTrip, self).write({
            "state": "completed",
            "completed_at": now,
            "actual_arrival_at": arrival,
        })
        self._report_open_checkpoints_on_completion()
        return True

    def _report_open_checkpoints_on_completion(self):
        """Report outstanding checkpoints without blocking completion.

        Phase 3 checkpoints are operational facts: they never impose a
        surprise mandatory step on an existing Phase 2 trip. The trip records
        what is still open so the gap stays visible for follow-up.
        """
        self.ensure_one()
        if not self.milestone_open_count:
            return
        from markupsafe import Markup
        open_names = self.milestone_ids.filtered(
            lambda milestone: (
                milestone.state not in ("completed", "departed")
                and milestone.state != "skipped"
            )
        ).mapped("name")
        self.message_post(body=Markup("<p>{header}</p><ul>{items}</ul>").format(
            header=_("Trip completed with %s checkpoint(s) still open:")
            % len(open_names),
            items=Markup("").join(
                Markup("<li>{}</li>").format(name) for name in open_names
            ),
        ))

    def _check_can_cancel(self):
        self.ensure_one()
        if self.state not in ("draft", "ready", "in_progress"):
            raise UserError(_("Completed or cancelled trips cannot be cancelled."))
        if self.state == "in_progress" and not self.env.user.has_group(
            "taj_transport_operations.group_transport_manager"
        ):
            raise AccessError(_(
                "Only Transport Managers can cancel a trip in progress."
            ))

    def action_open_cancel_trip_wizard(self):
        self.ensure_one()
        self.check_access("write")
        self._check_can_cancel()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancel Transport Trip"),
            "res_model": "taj.transport.cancel.trip.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_trip_id": self.id},
        }

    def _cancel_with_reason(self, reason):
        self.ensure_one()
        self._lock_for_transition()
        self._check_can_cancel()
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError(_("Enter a reason for cancelling the trip."))
        now = fields.Datetime.now()
        # A Draft/Ready trip can have an assignment starting in the future.
        # End it at its start (zero active duration), never before its start.
        self.env["taj.transport.assignment"].flush_model([
            "trip_id", "effective_to",
        ])
        self.env.cr.execute(
            "SELECT id FROM taj_transport_assignment "
            "WHERE trip_id = %s AND effective_to IS NULL FOR UPDATE",
            (self.id,),
        )
        ids = [r[0] for r in self.env.cr.fetchall()]
        if len(ids) > 1:
            raise ValidationError(_("Multiple active assignments found."))
        if ids:
            assignment = self.env["taj.transport.assignment"].browse(ids[0])
            assignment.check_access("read")
            assignment.invalidate_recordset(["effective_from", "effective_to"])
            assignment._close_for_trip_end(max(now, assignment.effective_from))
        super(TajTransportTrip, self).write({
            "state": "cancelled",
            "cancelled_at": now,
            "cancelled_by_id": self.env.user.id,
            "cancellation_reason": reason,
        })
        from markupsafe import Markup
        self.message_post(
            body=Markup("<p>%s</p>") % (_("Trip cancelled: %s") % reason),
        )
        return True

    def action_open_sale_order(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Sales Order"),
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }
