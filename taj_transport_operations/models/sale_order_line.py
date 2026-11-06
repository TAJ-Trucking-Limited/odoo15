from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .transport_trip_file import SHIPMENT_FIELDS, _FILE_SHIPMENT_WRITE

_CONFIRMED_PRICE_WRITE = object()

class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    allocation_date = fields.Datetime(
        string="Allocation Date", readonly=True, copy=False,
        help="Time the Sales line first received a truck. Replacements preserve this date.",
    )

    def _lock_transport_vehicle(self, creating_trip=False):
        """Serialize first allocation with assignment backfill and trip creation."""
        self.check_access("read")
        self.flush_recordset(["vehicle_id", "allocation_date"])
        self.env.cr.execute(
            # The wizard/trip foreign keys hold KEY SHARE locks on this row.
            # Only non-key values change: avoid a lock upgrade deadlock between dialogs.
            "SELECT id FROM sale_order_line WHERE id = ANY(%s) ORDER BY id FOR NO KEY UPDATE",
            (sorted(self.ids),),
        )
        if creating_trip:
            # The trip is a reverse FK, invisible to an older repeatable-read
            # snapshot. Advance the source row version so allocators retry and
            # see the new trip; keep the commercial timestamp unchanged.
            self.env.cr.execute(
                "UPDATE sale_order_line SET write_date = write_date WHERE id = ANY(%s)",
                (sorted(self.ids),),
            )
        self.invalidate_recordset(["vehicle_id", "allocation_date", "transport_trip_ids"])

    transport_file_line_ids = fields.One2many(
        "taj.transport.trip.file.line",
        "sale_line_id",
        readonly=True,
        copy=False,
    )
    transport_file_id = fields.Many2one(
        "taj.transport.trip.file",
        string="File Number",
        compute="_compute_transport_file_id",
        store=True,
        readonly=True,
    )

    @api.depends("transport_file_line_ids.file_id")
    def _compute_transport_file_id(self):
        for line in self:
            line.transport_file_id = line.transport_file_line_ids[:1].file_id

    def _file_shipment_display_value(self, name):
        self.ensure_one()
        value = self[name]
        field = self._fields[name]
        if field.type == "many2one":
            return value.display_name or ""
        if field.type == "selection":
            return dict(field._description_selection(self.env)).get(value, "")
        if field.type == "datetime" and value:
            return "%s UTC" % fields.Datetime.to_string(value)
        return str(value) if value is not False and value is not None else ""

    def _write_file_shipment(self, values, row):
        self.ensure_one()
        row.ensure_one()
        row.check_access("write")
        self.check_access("read")
        if not (
            self.env.su
            or self.env.user.has_group(
                "taj_transport_operations.group_shipment_data_entry"
            )
        ):
            raise AccessError(_("Shipment Data Entry permission is required."))
        if (
            set(values) - set(SHIPMENT_FIELDS)
            or row.sale_line_id != self
            or row.file_id.sale_order_id != self.order_id
        ):
            raise AccessError(
                _(
                    "Only this file's shipment fields may be changed; prices cannot be edited."
                )
            )
        if self.company_id not in self.env.companies or self.order_id.state == "cancel":
            raise AccessError(
                _(
                    "This shipment cannot be edited in the current company or order state."
                )
            )
        if "product_id" in values and values["product_id"] != self.product_id.id:
            if (
                not values["product_id"]
                or self.transport_trip_ids
                or self.qty_invoiced
                or self.qty_delivered
            ):
                raise UserError(
                    _(
                        "The Product cannot be changed after a trip, invoice or delivery exists."
                    )
                )
        # Do not expose a general Sales write capability. All incoming keys were checked.
        values = dict(
            values,
            price_unit=self.price_unit,
            technical_price_unit=self.technical_price_unit,
        )
        return (
            self.sudo()
            .with_context(_file_shipment_write=_FILE_SHIPMENT_WRITE)
            .write(values)
        )

    def _get_protected_fields(self):
        protected = super()._get_protected_fields()
        if self.env.context.get("_confirmed_price_write") is _CONFIRMED_PRICE_WRITE:
            return [name for name in protected if name != "price_unit"]
        if self.env.context.get("_file_shipment_write") is _FILE_SHIPMENT_WRITE:
            allowed = set(SHIPMENT_FIELDS) | {
                "price_unit",
                "technical_price_unit",
                "name",
            }
            return [name for name in protected if name not in allowed]
        return protected

    def action_open_confirmed_price_wizard(self):
        self.ensure_one()
        self.check_access("write")
        if not self.env.user.has_group("taj_transport_operations.group_edit_confirmed_sale_orders"):
            raise AccessError(_("Confirmed Sales Order Price Editing permission is required."))
        if self.order_id.state not in ("sale", "done") or self.display_type or self.is_downpayment:
            raise UserError(_("Choose a confirmed product line."))
        return {
            "type": "ir.actions.act_window", "name": _("Edit Unit Price"),
            "res_model": "taj.transport.sale.price.wizard", "view_mode": "form", "target": "new",
            "context": {"default_sale_line_id": self.id, "default_price_unit": self.price_unit},
        }

    @api.depends(
        "product_id",
        "state",
        "qty_invoiced",
        "qty_delivered",
        "order_id.locked",
        "transport_trip_ids",
    )
    @api.depends_context("_file_shipment_write")
    def _compute_product_updatable(self):
        super()._compute_product_updatable()
        if self.env.context.get("_file_shipment_write") is _FILE_SHIPMENT_WRITE:
            for line in self:
                if not (
                    line.is_downpayment
                    or line.state == "cancel"
                    or line.transport_trip_ids
                    or line.qty_invoiced
                    or line.qty_delivered
                ):
                    line.product_updatable = True

    vehicle_id = fields.Many2one(readonly=True, copy=False)
    file_type_id = fields.Many2one(
        "taj.transport.file.type",
        string="File Type",
        ondelete="restrict",
    )
    clearing_service = fields.Selection(
        [("included", "Included"), ("not_included", "Not Included")],
        string="Clearing Service",
    )
    bill_of_lading = fields.Char(string="Bill of Lading")
    eta = fields.Datetime(string="ETA")
    etol = fields.Date(
        string="ETOL",
        help="Estimated time of loading (estimated loading date).",
    )
    shipping_line = fields.Char(string="Shipping Line")

    @api.model
    def _suggest_route_file_type(self, product_tmpl, company):
        """Suggest a type only when all active product routes agree.

        Never guess between differently classified routes; blank legacy
        routes also make the choice ambiguous. Explicit Sales choices win.
        """
        FileType = self.env["taj.transport.file.type"]
        if not product_tmpl or not company:
            return FileType
        routes = self.env["taj.transport.route"].sudo().search([
            ("product_tmpl_id", "=", product_tmpl.id),
            ("company_id", "=", company.id),
            ("active", "=", True),
        ])
        if not routes:
            return FileType
        types = routes.mapped("file_type_id")
        if (
            len(types) == 1
            and types.active
            and all(route.file_type_id == types for route in routes)
        ):
            return types
        return FileType

    @api.onchange("product_id", "product_template_id", "order_id")
    def _onchange_route_file_type(self):
        for line in self:
            if line.display_type not in (False, "product") or line.is_downpayment:
                continue
            template = line.product_id.product_tmpl_id or line.product_template_id
            company = line.order_id.company_id or self.env.company
            # The user may change this selection afterward. Do not use a
            # computed/related field, which would overwrite manual choices.
            line.file_type_id = self._suggest_route_file_type(template, company)

    @api.model
    def action_open_duplicate_line_wizard(self):
        order = self.env["sale.order"].browse(self.env.context.get("order_id")).exists()
        if not order:
            raise UserError(
                _("Save the quotation or sales order before duplicating a line.")
            )
        order.ensure_one()
        order.check_access("write")
        order._check_confirmed_sale_edit_access()
        if order.locked or order.state not in ("draft", "sent", "sale"):
            raise UserError(
                _(
                    "Lines can only be duplicated on an unlocked quotation or sales order."
                )
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Duplicate Line"),
            "res_model": "taj.transport.duplicate.sale.line.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sale_order_id": order.id},
        }

    @api.constrains(
        "product_id", "file_type_id", "order_id", "display_type", "is_downpayment"
    )
    def _check_goods_transport_configuration(self):
        for line in self:
            if (
                line.display_type not in (False, "product")
                or line.is_downpayment
                or line.product_id.type != "consu"
            ):
                continue
            if not line.file_type_id:
                raise ValidationError(
                    _("Select a File Type for Goods product %(product)s.")
                    % {"product": line.product_id.display_name}
                )
            # Sales users need not have route-maintenance read access.
            routes = self.env["taj.transport.route"].sudo().search([
                ("product_tmpl_id", "=", line.product_id.product_tmpl_id.id),
                ("company_id", "=", line.company_id.id),
                ("active", "=", True),
            ])
            if not routes:
                raise ValidationError(
                    _(
                        "Goods product %(product)s needs an active Route Template "
                        "linked to it in company %(company)s."
                    )
                    % {
                        "product": line.product_id.display_name,
                        "company": line.company_id.display_name,
                    }
                )
            # Unclassified legacy templates are compatible with any chosen
            # File Type; classified templates must agree with the Sales line.
            if not any(
                not route.file_type_id or route.file_type_id == line.file_type_id
                for route in routes
            ):
                raise ValidationError(
                    _(
                        "No active Route Template for Goods product %(product)s "
                        "matches File Type %(file_type)s. Choose a matching "
                        "File Type or configure a matching Route Template."
                    )
                    % {
                        "product": line.product_id.display_name,
                        "file_type": line.file_type_id.display_name,
                    }
                )

    @api.constrains("file_type_id")
    def _check_existing_trip_route_file_type(self):
        # File Numbers can change shipment fields after confirmation. Do not
        # let that path silently conflict with a trip's selected route.
        for line in self:
            if any(
                trip.route_id.file_type_id
                and trip.route_id.file_type_id != line.file_type_id
                for trip in line.transport_trip_ids
            ):
                raise ValidationError(_(
                    "The File Type cannot differ from the Route Template "
                    "of an existing transport trip."
                ))

    transport_planned_departure_at = fields.Datetime(
        string="Planned Departure",
        copy=False,
        help="Copied to a new trip; later edits do not update existing trips.",
    )
    transport_planned_arrival_at = fields.Datetime(
        string="Planned Arrival",
        copy=False,
        help="Copied to a new trip; later edits do not update existing trips.",
    )

    @api.constrains("transport_planned_departure_at", "transport_planned_arrival_at")
    def _check_transport_planned_schedule(self):
        for line in self:
            if (
                line.transport_planned_departure_at
                and line.transport_planned_arrival_at
                and line.transport_planned_arrival_at
                < line.transport_planned_departure_at
            ):
                raise ValidationError(
                    _(
                        "Transport planned arrival cannot be earlier than the planned departure."
                    )
                )

    transport_trip_ids = fields.One2many(
        "taj.transport.trip",
        "sale_line_id",
        string="Transport Trip",
        readonly=True,
    )
    transport_trip_count = fields.Integer(
        string="Transport Trips",
        compute="_compute_transport_trip_count",
    )

    @api.depends("transport_trip_ids")
    def _compute_transport_trip_count(self):
        for line in self:
            line.transport_trip_count = len(line.transport_trip_ids)

    @api.depends(
        "product_id",
        "order_id",
        "order_partner_id",
        "name",
        "vehicle_id",
        "vehicle_id.license_plate",
        "container_num",
    )
    @api.depends_context("transport_line_label")
    def _compute_display_name(self):
        if not self.env.context.get("transport_line_label"):
            return super()._compute_display_name()
        for line in self:
            label = "%s — %s" % (
                line.product_id.display_name or line.name or "",
                line.vehicle_id.license_plate or _("No truck"),
            )
            if line.container_num:
                label += " — " + line.container_num
            # Distinguish repeated product/truck/container combinations.
            line.display_name = "%s [#%s]" % (label, line.id)

    def action_open_assign_truck_wizard(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Assign Truck"),
            "res_model": "taj.transport.assign.truck.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sale_line_id": self.id},
        }

    def action_prepare_transport_trip(self):
        self.ensure_one()
        self.check_access("read")
        existing = self.transport_trip_ids[:1]
        if existing:
            return existing.get_formview_action()
        self.env["taj.transport.trip"]._validate_sale_line(self)
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Transport Trip"),
            "res_model": "taj.transport.trip",
            "view_mode": "form",
            "target": "current",
            "views": [
                (
                    self.env.ref(
                        "taj_transport_operations.view_transport_trip_form"
                    ).id,
                    "form",
                )
            ],
            "context": {
                "default_sale_order_id": self.order_id.id,
                "default_sale_line_id": self.id,
                "transport_trip_entry": True,
            },
        }

    cargo_type_id = fields.Many2one(
        "taj.transport.cargo.type",
        string="Cargo Type",
        help="Optional cargo classification copied onto the new trip for "
        "this sales line. Later line edits do not update existing trips.",
    )
    cargo_description = fields.Text(
        string="Cargo Description",
        help="Optional goods description copied onto the new trip for "
        "this sales line. Later line edits do not update existing trips.",
    )
    special_handling_instructions = fields.Text(
        string="Special Handling Instructions",
        help="Optional handling notes copied onto the new trip for "
        "this sales line. Later line edits do not update existing trips.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any("allocation_date" in vals for vals in vals_list):
            raise UserError(_("Allocation Date is recorded automatically."))
        default_vehicle = self.default_get(["vehicle_id"]).get("vehicle_id")
        vals_list = [dict(vals) for vals in vals_list]
        default_order_id = (
            self.default_get(["order_id"]).get("order_id")
            if any("order_id" not in vals for vals in vals_list)
            else False
        )
        for vals in vals_list:
            vals["allocation_date"] = (
                fields.Datetime.now()
                if vals.get("vehicle_id", default_vehicle) else False
            )
            if (
                "file_type_id" not in vals
                and "default_file_type_id" not in self.env.context
                and vals.get("display_type") in (False, None, "product")
                and not vals.get("is_downpayment")
            ):
                template = (
                    self.env["product.template"].browse(vals["product_template_id"])
                    if vals.get("product_template_id")
                    else self.env["product.product"].browse(
                        vals.get("product_id")
                    ).product_tmpl_id
                )
                order = self.env["sale.order"].browse(
                    vals.get("order_id") or default_order_id
                )
                file_type = self._suggest_route_file_type(
                    template, order.company_id or self.env.company
                )
                if file_type:
                    vals["file_type_id"] = file_type.id
        order_ids = [vals.get("order_id", default_order_id) for vals in vals_list]
        self.env["sale.order"].browse(
            [order_id for order_id in order_ids if order_id]
        )._check_confirmed_sale_edit_access()
        lines = super().create(vals_list)
        confirmed = lines.filtered(lambda line: line.order_id.state in ("sale", "done"))
        self.env["taj.transport.trip.file"]._ensure_for_sale_lines(confirmed)
        return lines

    def write(self, vals):
        if "allocation_date" in vals:
            raise UserError(_("Allocation Date is recorded automatically."))
        if vals:
            self.order_id._check_confirmed_sale_edit_access()
            if vals.get("order_id"):
                self.env["sale.order"].browse(
                    vals["order_id"]
                )._check_confirmed_sale_edit_access()
        if "order_id" in vals and any(
            line.order_id.id != vals["order_id"]
            and self.env["taj.transport.trip"]
            .sudo()
            .search_count([("sale_line_id", "=", line.id)])
            for line in self
        ):
            raise UserError(
                _("A sales line linked to a trip cannot move to another Sales Order.")
            )
        if vals.get("order_id") and any(
            line.order_id.id != vals["order_id"] and line.transport_file_line_ids
            for line in self
        ):
            raise UserError(
                _(
                    "A shipment linked to a File Number cannot move to another Sales Order."
                )
            )
        tracked = set(vals) & set(SHIPMENT_FIELDS)
        rows = (
            self.env["taj.transport.trip.file.line"]
            .sudo()
            .search([("sale_line_id", "in", self.ids)])
            if tracked
            else self.env["taj.transport.trip.file.line"]
        )
        before = {
            row.sale_line_id.id: {
                name: row.sale_line_id._file_shipment_display_value(name)
                for name in tracked
            }
            for row in rows
        }
        if vals.get("vehicle_id"):
            self._lock_transport_vehicle()
            first_allocations = self.filtered(lambda line: not line.allocation_date)
            if first_allocations:
                super(SaleOrderLine, first_allocations).write({
                    "allocation_date": fields.Datetime.now(),
                })
        result = super().write(vals)
        for row in rows:
            line = row.sale_line_id
            changes = []
            for name in sorted(tracked):
                old = before[line.id][name]
                new = line._file_shipment_display_value(name)
                if old != new:
                    changes.append(
                        Markup("<li><strong>%s</strong>: %s → %s</li>")
                        % (
                            line._fields[name]._description_string(self.env),
                            old or _("Empty"),
                            new or _("Empty"),
                        )
                    )
            if changes:
                body = Markup("<p>Shipment line #%s — %s</p><ul>%s</ul>") % (
                    line.id,
                    line.product_id.display_name,
                    Markup("").join(changes),
                )
                row.file_id.sudo()._log_shipment_change(body)
        return result

    def unlink(self):
        self.order_id._check_confirmed_sale_edit_access()
        return super().unlink()

    def action_open_or_create_transport_trip(self):
        self.ensure_one()
        trip = self.transport_trip_ids[:1]
        if trip:
            return {
                "type": "ir.actions.act_window",
                "name": _("Transport Trip"),
                "res_model": "taj.transport.trip",
                "res_id": trip.id,
                "view_mode": "form",
                "target": "current",
            }
        Trip = self.env["taj.transport.trip"]
        Trip._validate_sale_line(self)
        routes = self.env["taj.transport.route"]._matching_for_sale_line(self)
        if not routes:
            raise UserError(
                _(
                    "No active route template is linked to product %(product)s "
                    "for company %(company)s. Ask a Transport Manager to link a "
                    "route template to this product, then try again."
                )
                % {
                    "product": self.product_id.display_name,
                    "company": (
                        self.company_id or self.order_id.company_id
                    ).display_name,
                }
            )
        if len(routes) > 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Choose Route Template"),
                "res_model": "taj.transport.trip.route.chooser.wizard",
                "view_mode": "form",
                "target": "new",
                "context": {"default_sale_line_id": self.id},
            }
        trip = Trip.create(
            {
                "sale_line_id": self.id,
                "route_id": routes.id,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Trip"),
            "res_model": "taj.transport.trip",
            "res_id": trip.id,
            "view_mode": "form",
            "target": "current",
        }
