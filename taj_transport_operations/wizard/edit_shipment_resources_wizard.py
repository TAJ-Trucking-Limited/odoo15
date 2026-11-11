"""Edit Truck / Trailer / Driver / ETOL for an unstarted Sales shipment."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class TajTransportEditShipmentResourcesWizard(models.TransientModel):
    _name = "taj.transport.edit.shipment.resources.wizard"
    _description = "Edit Shipment Resources"

    sale_line_id = fields.Many2one(
        "sale.order.line", string="Sales Order Line", required=True, readonly=True,
    )
    sale_order_id = fields.Many2one(
        related="sale_line_id.order_id", string="Sales Order", readonly=True,
    )
    company_id = fields.Many2one(
        related="sale_line_id.company_id", string="Company", readonly=True,
    )
    trip_id = fields.Many2one("taj.transport.trip", string="Trip", readonly=True)
    expected_assignment_id = fields.Many2one(
        "taj.transport.assignment", string="Expected Assignment", readonly=True,
    )
    expected_source_vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Expected Source Truck", readonly=True,
        help="Sale line planned truck when the dialog opened.",
    )
    expected_vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Expected Operational Truck", readonly=True,
    )
    expected_trailer_id = fields.Many2one("fleet.vehicle", readonly=True)
    expected_driver_id = fields.Many2one("res.partner", readonly=True)
    expected_etol = fields.Date(readonly=True)
    expected_trip_state = fields.Char(readonly=True)
    expected_has_assignment = fields.Boolean(readonly=True)
    # Tracks the last truck for which Fleet defaults were applied. Seeded to the
    # opening operational truck so the initial Form onchange keeps the prefill.
    defaults_vehicle_id = fields.Many2one("fleet.vehicle", readonly=True)

    vehicle_id = fields.Many2one(
        "fleet.vehicle", string="Truck", required=True,
        domain=lambda self: (
            "[('active', '=', True), '|', ('category_id', '=', False), "
            "('category_id', '=', %s), '|', ('company_id', '=', False), "
            "('company_id', '=', company_id)]"
            % self.env.ref("taj_transport_operations.fleet_category_truck").id
        ),
    )
    trailer_id = fields.Many2one(
        "fleet.vehicle", string="Trailer",
        domain=lambda self: (
            "[('active', '=', True), ('category_id', '=', %s), '|', "
            "('company_id', '=', False), ('company_id', '=', company_id)]"
            % self.env.ref("taj_transport_operations.fleet_category_trailer").id
        ),
    )
    driver_id = fields.Many2one(
        "res.partner", string="Driver",
        domain="[('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    etol = fields.Date(string="ETOL (Estimated Loading Date)")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        line_id = values.get("sale_line_id") or self.env.context.get("default_sale_line_id")
        if not line_id:
            return values
        line = self.env["sale.order.line"].browse(line_id).exists()
        if not line:
            return values
        line.check_access("read")
        trip = line.transport_trip_ids[:1]
        current = self._operational_resources_for(line, trip)
        assignment = current["assignment"]
        defaults = {
            "sale_line_id": line.id,
            "trip_id": trip.id or False,
            "expected_assignment_id": assignment.id if assignment else False,
            "expected_has_assignment": bool(assignment),
            "expected_source_vehicle_id": line.vehicle_id.id or False,
            "expected_vehicle_id": current["vehicle_id"],
            "expected_trailer_id": current["trailer_id"],
            "expected_driver_id": current["driver_id"],
            "expected_etol": line.etol or False,
            "expected_trip_state": trip.state if trip else False,
            "defaults_vehicle_id": current["vehicle_id"],
            "vehicle_id": current["vehicle_id"],
            "trailer_id": current["trailer_id"],
            "driver_id": current["driver_id"],
            "etol": line.etol or False,
        }
        for key, value in defaults.items():
            if not fields_list or key in fields_list:
                values[key] = value
        return values

    @api.onchange("vehicle_id")
    def _onchange_vehicle_id(self):
        """Refresh Fleet defaults on every truck change, including return to original.

        The opening prefill is kept when Form fires onchange for the same truck
        already seeded in default_get.
        """
        if (self.vehicle_id.id or False) == (self.defaults_vehicle_id.id or False):
            return
        self.defaults_vehicle_id = self.vehicle_id
        company = self.company_id or self.env.company
        trailer_category = self.env.ref("taj_transport_operations.fleet_category_trailer")
        trailer = self.vehicle_id.transport_default_trailer_id
        self.trailer_id = trailer if (
            trailer and trailer.active and trailer.category_id == trailer_category
            and (not trailer.company_id or trailer.company_id == company)
        ) else False
        driver = self.vehicle_id.driver_id
        self.driver_id = driver if (
            driver and driver.active
            and (not driver.company_id or driver.company_id == company)
        ) else False

    @api.model
    def _operational_resources_for(self, line, trip):
        assignment = trip.current_assignment_id if trip else False
        if assignment:
            return {
                "vehicle_id": assignment.vehicle_id.id or False,
                "trailer_id": assignment.trailer_id.id or False,
                "driver_id": assignment.driver_id.id or False,
                "co_driver_id": assignment.co_driver_id.id or False,
                "assignment": assignment,
            }
        return {
            "vehicle_id": line.vehicle_id.id or False,
            "trailer_id": (
                line.transport_allocated_trailer_id.id
                if line.transport_trailer_choice_set else False
            ),
            "driver_id": (
                line.transport_allocated_driver_id.id
                if line.transport_driver_choice_set
                else (line.vehicle_id.driver_id.id or False)
            ),
            "co_driver_id": False,
            "assignment": False,
        }

    def _require_transport_access(self):
        if not (
            self.env.su
            or self.env.user.has_group("taj_transport_operations.group_transport_user")
        ):
            raise AccessError(_("Transport Operations access is required."))

    def _assert_not_stale(self, line, trip, current):
        assignment = current["assignment"]
        if (trip.state if trip else False) != (self.expected_trip_state or False):
            raise UserError(_(
                "The trip state changed after this dialog was opened. Close it and try again."
            ))
        if (trip.id if trip else False) != (self.trip_id.id if self.trip_id else False):
            raise UserError(_(
                "The trip changed after this dialog was opened. Close it and try again."
            ))
        if bool(assignment) != self.expected_has_assignment:
            raise UserError(_(
                "The resources changed after this dialog was opened. Close it and try again."
            ))
        if assignment and assignment.id != self.expected_assignment_id.id:
            raise UserError(_(
                "The resources changed after this dialog was opened. Close it and try again."
            ))
        if (line.vehicle_id.id or False) != (self.expected_source_vehicle_id.id or False):
            raise UserError(_(
                "The shipment changed after this dialog was opened. Close it and try again."
            ))
        if (
            current["vehicle_id"] != (self.expected_vehicle_id.id or False)
            or current["trailer_id"] != (self.expected_trailer_id.id or False)
            or current["driver_id"] != (self.expected_driver_id.id or False)
            or (line.etol or False) != (self.expected_etol or False)
        ):
            raise UserError(_(
                "The resources changed after this dialog was opened. Close it and try again."
            ))

    def action_save(self):
        self.ensure_one()
        self._require_transport_access()
        line = self.sale_line_id.exists()
        if not line:
            raise UserError(_("The shipment no longer exists."))
        line.check_access("read")
        if (
            line.order_id.state not in ("sale", "done")
            or line.display_type not in (False, "product")
            or not line.product_id
            or line.is_downpayment
        ):
            raise UserError(_("Select a confirmed product shipment that has not started."))
        if not self.etol:
            raise UserError(_("ETOL is required."))

        vehicle = self.vehicle_id.exists()
        trailer = self.trailer_id.exists()
        driver = self.driver_id.exists()
        if not vehicle:
            raise UserError(_("Choose a truck."))
        vehicle._require_transport_dialog_truck(line.company_id)
        trailer._require_transport_dialog_trailer(line.company_id)
        self.env["fleet.vehicle"]._require_transport_dialog_driver(driver, line.company_id)

        trip = line.transport_trip_ids
        if len(trip) > 1:
            raise UserError(_("This shipment has more than one trip. Refresh and review it."))
        trip = trip[:1]
        if trip:
            trip.check_access("write")
            trip._lock_for_transition()
            trip.invalidate_recordset([
                "state", "assignment_ids", "current_assignment_id", "initial_vehicle_id",
            ])
        line._lock_transport_vehicle()
        line.invalidate_recordset([
            "vehicle_id", "etol", "transport_trip_ids",
            "transport_allocated_driver_id", "transport_driver_choice_set",
            "transport_allocated_trailer_id", "transport_trailer_choice_set",
            "allocation_date",
        ])
        trip = line.transport_trip_ids[:1]
        if trip and trip.state not in ("draft", "ready"):
            raise UserError(_(
                "Only shipments without a Trip, or with a Draft/Ready trip, can be edited."
            ))

        current = self._operational_resources_for(line, trip)
        self._assert_not_stale(line, trip, current)
        assignment = current["assignment"]

        new_resources = {
            "vehicle_id": vehicle.id,
            "trailer_id": trailer.id if trailer else False,
            "driver_id": driver.id if driver else False,
        }
        resources_changed = any(
            new_resources[name] != current[name]
            for name in ("vehicle_id", "trailer_id", "driver_id")
        )
        etol_changed = (line.etol or False) != self.etol
        if not resources_changed and not etol_changed:
            return {"type": "ir.actions.act_window_close"}

        if resources_changed and trip and trip.state == "ready" and not driver:
            raise UserError(_("Ready trips require a truck and primary driver."))

        if etol_changed:
            file_row = line.transport_file_line_ids
            if len(file_row) != 1:
                raise UserError(_(
                    "The Sales line needs its File Number shipment row before saving."
                ))
            line._write_file_shipment({"etol": self.etol}, file_row)

        if resources_changed:
            # Fleet default sync only when truck/trailer choice changes. Driver-only
            # edits keep newer Fleet pairing and the shipment's operational trailer.
            if (
                new_resources["vehicle_id"] != current["vehicle_id"]
                or new_resources["trailer_id"] != current["trailer_id"]
            ):
                vehicle._apply_default_trailer_from_transport_dialog(trailer)
            line._write_transport_allocation_snapshot({
                "vehicle_id": vehicle.id,
                "transport_allocated_driver_id": driver.id if driver else False,
                "transport_driver_choice_set": True,
                "transport_allocated_trailer_id": trailer.id if trailer else False,
                "transport_trailer_choice_set": True,
            })
            if trip and assignment:
                self.env["taj.transport.replace.assignment.wizard"].create({
                    "trip_id": trip.id,
                    "expected_assignment_id": assignment.id,
                    "vehicle_id": vehicle.id,
                    "trailer_id": trailer.id if trailer else False,
                    "driver_id": driver.id if driver else False,
                    "co_driver_id": current["co_driver_id"],
                    "effective_from": fields.Datetime.now(),
                    "reason": _("Updated from Edit Info"),
                }).action_replace_resources()
            elif trip and not assignment:
                self.env["taj.transport.assignment"].create({
                    "trip_id": trip.id,
                    "vehicle_id": vehicle.id,
                    "trailer_id": trailer.id if trailer else False,
                    "driver_id": driver.id if driver else False,
                    "effective_from": fields.Datetime.now(),
                    "reason": _("Initial resources from Edit Info"),
                })

        return {"type": "ir.actions.act_window_close"}
