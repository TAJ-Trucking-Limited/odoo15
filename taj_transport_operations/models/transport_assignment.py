"""Audited, time-based trip resource assignments with controlled replacement."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class TajTransportAssignment(models.Model):
    _name = "taj.transport.assignment"
    _description = "Trip Resource Assignment"
    _order = "effective_from desc, id desc"
    _check_company_auto = True

    _valid_period = models.Constraint(
        "CHECK(effective_to IS NULL OR effective_to >= effective_from)",
        "An assignment end cannot precede its start.",
    )
    _different_drivers = models.Constraint(
        "CHECK(driver_id IS NULL OR co_driver_id IS NULL OR driver_id != co_driver_id)",
        "The primary driver and co-driver must be different people.",
    )
    _has_resource = models.Constraint(
        "CHECK(vehicle_id IS NOT NULL OR driver_id IS NOT NULL OR "
        "co_driver_id IS NOT NULL OR trailer_id IS NOT NULL)",
        "Assign at least one resource to the trip.",
    )

    trip_id = fields.Many2one(
        "taj.transport.trip", string="Trip",
        required=True, ondelete="restrict", index=True,
        check_company=True, copy=False,
    )
    company_id = fields.Many2one(
        "res.company", related="trip_id.company_id",
        store=True, readonly=True, index=True,
    )
    vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Truck", check_company=True, copy=False,
        ondelete="restrict", index=True,
        domain=lambda self: self.env["fleet.vehicle"]._transport_resource_domain("truck"),
    )
    trailer_id = fields.Many2one(
        "fleet.vehicle", string="Trailer", ondelete="restrict",
        check_company=True, copy=False, index=True,
        domain=lambda self: self.env["fleet.vehicle"]._transport_resource_domain("trailer"),
    )
    driver_id = fields.Many2one(
        "res.partner", string="Driver",
        check_company=True, copy=False,
        help="Driver contact from Odoo Fleet/Contacts. This does not alter the "
             "vehicle's default driver.",
    )
    co_driver_id = fields.Many2one(
        "res.partner", string="Co-driver", check_company=True, copy=False,
    )
    effective_from = fields.Datetime(
        string="Effective From", required=True, copy=False,
        default=fields.Datetime.now,
    )
    effective_to = fields.Datetime(
        string="Effective To", readonly=True, copy=False,
        help="Set only by the controlled resource replacement workflow.",
    )
    effective_location = fields.Char(
        string="Effective Location", copy=False,
    )
    reason = fields.Char(
        string="Assignment Reason", required=True, copy=False,
        default="Initial assignment",
    )
    notes = fields.Text(string="Assignment Notes", copy=False)
    assigned_by_id = fields.Many2one(
        "res.users", string="Recorded By",
        readonly=True, copy=False,
    )

    def init(self):
        # Prevent concurrent initial assignments from creating two active rows.
        # A partial unique index permits historical (closed) periods later.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                taj_transport_assignment_one_current_trip_idx
            ON taj_transport_assignment (trip_id)
            WHERE effective_to IS NULL
        """)

    @api.constrains(
        "vehicle_id", "trailer_id", "driver_id", "co_driver_id",
        "effective_from", "effective_to",
    )
    def _check_initial_assignment(self):
        for assignment in self:
            for kind, vehicle in (("truck", assignment.vehicle_id),
                                  ("trailer", assignment.trailer_id)):
                if vehicle and not assignment.effective_to:
                    # Preserve closed history even if a vehicle was later archived.
                    category = self.env.ref("taj_transport_operations.fleet_category_" + kind)
                    if not vehicle.active:
                        raise ValidationError(_(
                            "%s is archived in Fleet. Choose an active %s."
                        ) % (vehicle.display_name, kind))
                    if vehicle.category_id != category and (kind != "truck" or vehicle.category_id):
                        raise ValidationError(_(
                            "%(vehicle)s has Fleet category %(actual)s; expected "
                            "%(expected)s. A Fleet administrator must correct "
                            "the vehicle/model category before it can be assigned."
                        ) % {
                            "vehicle": vehicle.display_name,
                            "actual": vehicle.category_id.display_name or _("Not set"),
                            "expected": category.display_name,
                        })
            if not any((
                assignment.vehicle_id, assignment.trailer_id,
                assignment.driver_id, assignment.co_driver_id,
            )):
                raise ValidationError(_(
                    "Choose at least one truck, trailer or driver."
                ))
            if (
                assignment.driver_id
                and assignment.driver_id == assignment.co_driver_id
            ):
                raise ValidationError(_(
                    "The driver and co-driver cannot be the same person."
                ))
            if (
                assignment.effective_to
                and assignment.effective_to < assignment.effective_from
            ):
                raise ValidationError(_(
                    "An assignment cannot end before it starts."
                ))

    @api.constrains("trip_id", "effective_from", "effective_to")
    def _check_non_overlapping_periods(self):
        """A closed period may touch the next start, but may not overlap it."""
        for assignment in self:
            domain = [
                ("trip_id", "=", assignment.trip_id.id),
                ("id", "!=", assignment.id),
                "|",
                ("effective_to", "=", False),
                ("effective_to", ">", assignment.effective_from),
            ]
            if assignment.effective_to:
                domain.append(("effective_from", "<", assignment.effective_to))
            if self.search_count(domain):
                raise ValidationError(_(
                    "Resource assignment periods must not overlap for the same trip."
                ))

    @api.model
    def _prepare_assignment_values(self, entry, replacement=False):
        vals = dict(entry)
        trip = self.env["taj.transport.trip"].browse(
            vals.get("trip_id") or 0
        ).exists()
        if not trip:
            raise ValidationError(_("Select an existing trip."))
        trip.check_access("write")
        trip._lock_for_transition()
        trip.invalidate_recordset(["state", "assignment_ids", "current_assignment_id"])
        for field, model in (
            ("vehicle_id", "fleet.vehicle"),
            ("trailer_id", "fleet.vehicle"),
            ("driver_id", "res.partner"),
            ("co_driver_id", "res.partner"),
        ):
            if vals.get(field):
                resource = self.env[model].browse(vals[field]).exists()
                if not resource:
                    raise ValidationError(_("Select an existing resource."))
                resource.check_access("read")
        vehicles = self.env["fleet.vehicle"].browse([
            vals[field] for field in ("vehicle_id", "trailer_id") if vals.get(field)
        ])
        vehicles._lock_for_transport_assignment()
        allowed_states = (
            ("draft", "ready", "in_progress")
            if replacement else ("draft",)
        )
        if trip.state not in allowed_states:
            raise UserError(_(
                "Initial resources require Draft; replacements are allowed "
                "only in Draft, Ready or In Progress."
            ))
        any_history = bool(self.search_count([("trip_id", "=", trip.id)]))
        if replacement and not any_history:
            raise UserError(_("Assign initial resources before replacing them."))
        if not replacement and any_history:
            raise UserError(_(
                "This trip already has assignment history. "
                "Use Replace Resources; do not create another initial assignment."
            ))
        if self.search_count([
            ("trip_id", "=", trip.id), ("effective_to", "=", False),
        ]):
            raise UserError(_("This trip already has an active assignment."))
        if vals.get("effective_to"):
            raise ValidationError(_(
                "An active assignment cannot be created with an end time."
            ))
        if vals.get("company_id") and vals["company_id"] != trip.company_id.id:
            raise ValidationError(_("The assignment company must match the trip."))
        vals.pop("company_id", None)
        vals["effective_to"] = False
        vals["assigned_by_id"] = self.env.user.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        # Match the trip -> vehicle order used by lifecycle/replacement dialogs.
        for trip in self.env["taj.transport.trip"].browse(sorted({
            vals["trip_id"] for vals in vals_list if vals.get("trip_id")
        })).exists():
            trip._lock_for_transition()
        self.env["fleet.vehicle"].browse(sorted({
            vals[field] for vals in vals_list for field in ("vehicle_id", "trailer_id")
            if vals.get(field)
        }))._lock_for_transport_assignment()
        prepared = [
            self._prepare_assignment_values(entry) for entry in vals_list
        ]
        return super().create(prepared)

    @api.model
    def _create_from_replacement(self, vals):
        """Private entrypoint: only a locked, authorized replacement uses this."""
        prepared = self._prepare_assignment_values(vals, replacement=True)
        return super().create([prepared])

    def _close_for_replacement(self, effective_to):
        """Only close the current row; keep every other field immutable."""
        self.ensure_one()
        if self.effective_to:
            raise UserError(_("This assignment has already been closed."))
        if effective_to <= self.effective_from:
            raise ValidationError(_(
                "Replacement time must be later than the current assignment start."
            ))
        # Dispatchers can create assignments but have no general write ACL.
        # This private ORM method bypasses that ACL for the end timestamp only.
        return super(TajTransportAssignment, self.sudo()).write({
            "effective_to": effective_to,
        })

    def _close_for_trip_end(self, effective_to):
        """Release resources at completion/cancellation; keep history intact."""
        self.ensure_one()
        if self.effective_to:
            raise UserError(_("This resource assignment is already closed."))
        if effective_to < self.effective_from:
            raise ValidationError(_(
                "The assignment cannot end before its effective start."
            ))
        # Only the trusted private trip lifecycle invokes this method. Direct
        # writes and unlink remain forbidden, including for administrators.
        return super(TajTransportAssignment, self.sudo()).write({
            "effective_to": effective_to,
        })

    def write(self, vals):
        if vals:
            raise UserError(_(
                "Assignment history cannot be edited directly. "
                "Use the controlled Replace Resources wizard."
            ))
        return True

    def unlink(self):
        raise UserError(_("Assignment history cannot be deleted."))
