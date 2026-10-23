"""Fill missing trip details without overwriting existing or source data."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TajTransportFillEmptyFieldsWizard(models.TransientModel):
    _name = "taj.transport.fill.empty.fields.wizard"
    _description = "Fill Empty Trip Fields"

    _editable_trip_fields = (
        "transporter_id", "transport_reference",
        "cargo_type_id", "cargo_description", "special_handling_instructions",
        "route_id", "origin_location", "destination_location", "route_notes",
        "planned_departure_at", "planned_arrival_at",
        "actual_departure_at", "actual_arrival_at",
        "pod_file", "pod_notes",
    )
    _sales_fields = (
        "customer_reference", "source_document", "price_unit", "currency_id",
        "payment_term_id", "container_num", "file_name", "srn", "consignee",
        "weight", "size", "cargo_quantity", "cargo_uom_id", "sale_description",
    )
    _lifecycle_states = {
        "ready_at": ("ready", "in_progress", "completed"),
        "started_at": ("in_progress", "completed"),
        "completed_at": ("completed",),
        "cancelled_at": ("cancelled",),
        "cancelled_by_id": ("cancelled",),
        "cancellation_reason": ("cancelled",),
    }

    trip_id = fields.Many2one(
        "taj.transport.trip", required=True, readonly=True, string="Trip",
    )
    company_id = fields.Many2one(related="trip_id.company_id", readonly=True)
    state = fields.Selection(related="trip_id.state", readonly=True)
    has_assignment = fields.Boolean(compute="_compute_has_assignment")
    milestone_count = fields.Integer(related="trip_id.milestone_count", readonly=True)
    empty_field_names = fields.Json(readonly=True, default=list)
    missing_sales_fields = fields.Text(string="Empty Sales Fields", readonly=True)
    missing_resource_fields = fields.Text(string="Empty Resource Fields", readonly=True)
    missing_milestone_fields = fields.Text(string="Empty Milestone Fields", readonly=True)
    missing_lifecycle_fields = fields.Text(string="Empty Lifecycle Fields", readonly=True)

    transporter_id = fields.Many2one("taj.transport.transporter", string="Transporter")
    transport_reference = fields.Char(string="Transport / Booking Reference")
    cargo_type_id = fields.Many2one("taj.transport.cargo.type", string="Cargo Type")
    cargo_description = fields.Text(string="Operational Cargo Description")
    special_handling_instructions = fields.Text(string="Special Handling Instructions")
    route_id = fields.Many2one(
        "taj.transport.route", string="Route Template",
        domain="[('active', '=', True), ('company_id', '=', company_id)]",
    )
    origin_location = fields.Char(string="Origin")
    destination_location = fields.Char(string="Destination")
    route_notes = fields.Text(string="Route Instructions")
    planned_departure_at = fields.Datetime(string="Planned Departure")
    planned_arrival_at = fields.Datetime(string="Planned Arrival")
    actual_departure_at = fields.Datetime(string="Actual Departure")
    actual_arrival_at = fields.Datetime(string="Actual Arrival")
    pod_file = fields.Binary(string="Proof of Delivery")
    pod_filename = fields.Char(string="POD Filename")
    pod_notes = fields.Text(string="Delivery Notes")

    @api.depends("trip_id.current_assignment_id")
    def _compute_has_assignment(self):
        for wizard in self:
            wizard.has_assignment = bool(wizard.trip_id.current_assignment_id)

    @api.model
    def _is_empty(self, record, name):
        value = record[name]
        if record._fields[name].type in ("boolean", "integer", "float", "monetary"):
            return False
        return not value or (isinstance(value, str) and not value.strip())

    @api.model
    def _editable_empty_names(self, trip):
        return [
            name for name in self._editable_trip_fields
            if not (
                trip.state in ("completed", "cancelled")
                and name in trip._operational_fields
            )
            and not (name == "route_id" and trip.state != "draft")
            and self._is_empty(trip, name)
        ]

    @api.model
    def _missing_labels(self, trip, names):
        return "\n".join(
            trip._fields[name].get_description(self.env)["string"]
            for name in names if self._is_empty(trip, name)
        )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        trip_id = values.get("trip_id") or self.env.context.get("default_trip_id")
        trip = self.env["taj.transport.trip"].browse(trip_id).exists()
        if not trip:
            raise UserError(_("Choose an existing trip."))
        trip.check_access("write")
        trip = trip.with_context(bin_size=True)
        closed = trip.state in ("completed", "cancelled")
        resources = (
            ("last_vehicle_id", "last_trailer_id", "last_driver_id", "last_co_driver_id")
            if closed else
            ("current_vehicle_id", "current_trailer_id", "current_driver_id", "current_co_driver_id")
        )
        defaults = {
            "empty_field_names": self._editable_empty_names(trip),
            "missing_sales_fields": self._missing_labels(trip, self._sales_fields),
            "missing_resource_fields": self._missing_labels(trip, (*resources, "assignment_ids")),
            "missing_milestone_fields": self._missing_labels(trip, ("generated_route_id", "milestone_ids")),
            "missing_lifecycle_fields": self._missing_labels(trip, (
                name for name, states in self._lifecycle_states.items() if trip.state in states
            )),
        }
        values.update({name: value for name, value in defaults.items() if name in fields_list})
        return values

    @api.onchange("route_id")
    def _onchange_route_id(self):
        for wizard in self:
            for name in ("origin_location", "destination_location"):
                if (
                    name in (wizard.empty_field_names or [])
                    and wizard._is_empty(wizard, name)
                    and wizard.route_id[name]
                ):
                    wizard[name] = wizard.route_id[name]

    def _apply_values(self):
        self.ensure_one()
        trip = self.trip_id.exists()
        if not trip:
            raise UserError(_("The trip no longer exists."))
        trip._lock_for_transition()
        # Re-read after locking: another form may have filled a field or ended
        # the trip while this wizard was open. Never overwrite that newer data.
        trip.invalidate_recordset(list(self._editable_trip_fields))
        current_empty = set(self._editable_empty_names(trip.with_context(bin_size=True)))
        vals = {}
        for name in self._editable_trip_fields:
            if name not in (self.empty_field_names or []) or self._is_empty(self, name):
                continue
            if name not in current_empty:
                raise UserError(_(
                    "%(field)s is no longer empty or editable. "
                    "Reopen Fill Empty Fields to use the latest trip values."
                ) % {"field": trip._fields[name].get_description(self.env)["string"]})
            vals[name] = self[name].id if self._fields[name].type == "many2one" else self[name]
        if "pod_file" in vals:
            vals["pod_file"] = self.with_context(bin_size=False).pod_file
            vals["pod_filename"] = self.pod_filename or False
        if vals:
            trip.write(vals)
        return trip

    def action_save(self):
        trip = self._apply_values()
        return {
            "type": "ir.actions.act_window", "name": _("Transport Trip"),
            "res_model": "taj.transport.trip", "res_id": trip.id,
            "view_mode": "form", "target": "current",
        }

    def action_open_sale_order(self):
        return self._apply_values().action_open_sale_order()

    def action_open_resources(self):
        trip = self._apply_values()
        if trip.current_assignment_id:
            return trip.action_open_replace_assignment_wizard()
        return trip.action_open_initial_assignment_wizard()

    def action_open_add_checkpoint(self):
        return self._apply_values().action_open_add_milestone_wizard()
