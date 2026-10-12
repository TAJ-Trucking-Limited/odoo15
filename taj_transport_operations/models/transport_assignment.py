"""Time-based trip assignments: immutable initial assignment in this batch."""
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
        ondelete="restrict",
        domain=lambda self: self.env["fleet.vehicle"]._transport_resource_domain("truck"),
    )
    trailer_id = fields.Many2one(
        "fleet.vehicle", string="Trailer", ondelete="restrict",
        check_company=True, copy=False,
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
        help="Reserved for assignment replacements in the next batch.",
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
                if vehicle:
                    category = self.env.ref("taj_transport_operations.fleet_category_" + kind)
                    if not vehicle.active or vehicle.category_id != category:
                        raise ValidationError(_("Select an active Fleet vehicle in the correct resource category."))
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

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for entry in vals_list:
            vals = dict(entry)
            trip = self.env["taj.transport.trip"].browse(
                vals.get("trip_id") or 0
            ).exists()
            if not trip:
                raise ValidationError(_("Select an existing trip."))
            trip.check_access("read")
            for field, model in (("vehicle_id", "fleet.vehicle"),
                                 ("trailer_id", "fleet.vehicle"),
                                 ("driver_id", "res.partner"),
                                 ("co_driver_id", "res.partner")):
                if vals.get(field):
                    resource = self.env[model].browse(vals[field]).exists()
                    if not resource:
                        raise ValidationError(_("Select an existing resource."))
                    resource.check_access("read")
            if trip.state != "draft":
                raise UserError(_(
                    "Initial resources can only be assigned to a Draft trip."
                ))
            if trip.current_assignment_id:
                raise UserError(_(
                    "This trip already has an assignment. Use the replacement "
                    "workflow when it becomes available."
                ))
            if vals.get("effective_to"):
                raise ValidationError(_(
                    "An initial assignment cannot have an end time."
                ))
            if vals.get("company_id") and vals["company_id"] != trip.company_id.id:
                raise ValidationError(_("The assignment company must match the trip."))
            vals.pop("company_id", None)
            vals["effective_to"] = False
            vals["assigned_by_id"] = self.env.user.id
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        if vals:
            raise UserError(_(
                "Assignment history is immutable in this batch. "
                "Use the replacement workflow in a later batch."
            ))
        return True

    def unlink(self):
        raise UserError(_("Assignment history cannot be deleted."))
