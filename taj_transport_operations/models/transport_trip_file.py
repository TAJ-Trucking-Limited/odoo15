"""File Numbers group Sales shipment lines and their operational trips."""

from markupsafe import Markup

from odoo import _, api, fields, models, Command
from odoo.exceptions import AccessError, UserError, ValidationError


SHIPMENT_FIELDS = (
    "product_id",
    "file_type_id",
    "clearing_service",
    "consignee",
    "bill_of_lading",
    "container_num",
    "eta",
    "etol",
    "shipping_line",
    "cargo_type_id",
    "size",
    "weight",
    "cargo_description",
    "special_handling_instructions",
    "product_uom_qty",
)
# This capability stays inside the validated shipment-write method, never in actions.
_FILE_SHIPMENT_WRITE = object()


class TajTransportTripFile(models.Model):
    _name = "taj.transport.trip.file"
    _description = "File Number"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True

    _unique_reference = models.Constraint(
        "UNIQUE(name)", "The File Number must be unique."
    )

    name = fields.Char(
        string="File Number", required=True, readonly=True, copy=False, index=True
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Related Sales Order",
        required=True,
        readonly=True,
        ondelete="restrict",
        index=True,
        copy=False,
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company", related="sale_order_id.company_id", store=True, precompute=True
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer Name",
        related="sale_order_id.partner_id",
        store=True,
    )
    line_ids = fields.One2many(
        "taj.transport.trip.file.line", "file_id", string="Shipments", copy=False
    )
    trip_ids = fields.Many2many(
        "taj.transport.trip",
        string="Related Trips",
        compute="_compute_summary",
        store=True,
    )
    line_count = fields.Integer(
        string="Shipment Lines", compute="_compute_summary", store=True
    )
    trip_count = fields.Integer(
        string="Related Trips", compute="_compute_summary", store=True
    )
    unplanned_line_count = fields.Integer(
        string="Lines Without Trip", compute="_compute_summary", store=True
    )
    total_quantity = fields.Float(
        string="Total QTY", compute="_compute_summary", store=True
    )
    products = fields.Char(string="Products", compute="_compute_summary", store=True)
    file_types = fields.Char(
        string="File Types", compute="_compute_summary", store=True
    )
    clearing_services = fields.Char(
        string="Clearing Services", compute="_compute_summary", store=True
    )
    consignees = fields.Char(
        string="Consignees", compute="_compute_summary", store=True
    )
    bill_of_lading = fields.Char(
        string="Bills of Lading", compute="_compute_summary", store=True
    )
    container_number = fields.Char(
        string="Container Numbers", compute="_compute_summary", store=True
    )
    etas = fields.Char(string="ETAs (UTC)", compute="_compute_summary", store=True)
    etols = fields.Char(string="ETOLs", compute="_compute_summary", store=True)
    shipping_lines = fields.Char(
        string="Shipping Lines", compute="_compute_summary", store=True
    )
    cargo_types = fields.Char(
        string="Cargo Types", compute="_compute_summary", store=True
    )
    sizes = fields.Char(string="Sizes", compute="_compute_summary", store=True)
    weights = fields.Char(string="Weights", compute="_compute_summary", store=True)
    cargo_descriptions = fields.Char(
        string="Cargo Descriptions", compute="_compute_summary", store=True
    )
    handling_instructions = fields.Char(
        string="Special Handling Instructions", compute="_compute_summary", store=True
    )
    units = fields.Char(string="Units", compute="_compute_summary", store=True)
    can_edit_shipments = fields.Boolean(compute="_compute_can_edit_shipments")
    # Preserve source identifiers and original snapshots when upgrading older files.
    legacy_sale_line_id = fields.Many2one(
        "sale.order.line", readonly=True, ondelete="restrict", copy=False
    )
    legacy_container_number = fields.Char(
        string="Original Container Snapshot", readonly=True, copy=False
    )
    legacy_bill_of_lading = fields.Char(
        string="Original Bill of Lading", readonly=True, copy=False
    )
    # Read-only compatibility aliases; grouped clients use line_ids and trip_ids.
    sale_line_id = fields.Many2one(
        "sale.order.line",
        compute="_compute_summary",
        store=True,
        readonly=True,
        ondelete="restrict",
    )
    trip_id = fields.Many2one(
        "taj.transport.trip",
        compute="_compute_summary",
        store=True,
        readonly=True,
        ondelete="restrict",
    )

    @api.depends("sale_order_id.state")
    @api.depends_context("uid")
    def _compute_can_edit_shipments(self):
        allowed = self.env.su or any(
            self.env.user.has_group(group)
            for group in (
                "taj_transport_operations.group_shipment_data_entry",
                "taj_transport_operations.group_transport_user",
                "base.group_system",
            )
        )
        for file in self:
            file.can_edit_shipments = allowed and file.sale_order_id.state != "cancel"

    @api.depends(
        "line_ids",
        *["line_ids.sale_line_id." + name for name in SHIPMENT_FIELDS],
        "line_ids.sale_line_id.transport_trip_ids",
        "line_ids.sale_line_id.product_uom_id",
    )
    def _compute_summary(self):
        summary_fields = (
            ("products", "product_id"),
            ("file_types", "file_type_id"),
            ("clearing_services", "clearing_service"),
            ("consignees", "consignee"),
            ("bill_of_lading", "bill_of_lading"),
            ("container_number", "container_num"),
            ("etas", "eta"),
            ("etols", "etol"),
            ("shipping_lines", "shipping_line"),
            ("cargo_types", "cargo_type_id"),
            ("sizes", "size"),
            ("weights", "weight"),
            ("cargo_descriptions", "cargo_description"),
            ("handling_instructions", "special_handling_instructions"),
            ("units", "product_uom_id"),
        )
        for file in self:
            lines = file.line_ids.sale_line_id
            trips = lines.transport_trip_ids
            file.line_count = len(lines)
            file.trip_ids = trips
            file.trip_count = len(trips)
            file.unplanned_line_count = len(
                lines.filtered(lambda line: not line.transport_trip_ids)
            )
            file.total_quantity = sum(lines.mapped("product_uom_qty"))
            file.sale_line_id = lines[:1]
            file.trip_id = trips[:1]
            for target, source in summary_fields:
                values = [line._file_shipment_display_value(source) for line in lines]
                file[target] = (
                    ", ".join(dict.fromkeys(value for value in values if value))
                    or False
                )

    @api.model
    def _ensure_for_sale_lines(self, lines):
        """Serialize by order; add new eligible lines to its first file."""
        if not lines:
            return
        lines.check_access("read")
        orders = lines.order_id.filtered(lambda order: order.state in ("sale", "done"))
        if not orders:
            return
        orders.flush_recordset(["state"])
        # NO KEY UPDATE permits the FK key-share locks taken by new sales lines.
        self.env.cr.execute(
            "SELECT id FROM sale_order WHERE id = ANY(%s) ORDER BY id FOR NO KEY UPDATE",
            (sorted(orders.ids),),
        )
        # Advance the row version so repeatable-read waiters serialize and retry.
        # The stored timestamp itself remains unchanged.
        self.env.cr.execute(
            "UPDATE sale_order SET write_date = write_date WHERE id = ANY(%s)",
            (sorted(orders.ids),),
        )
        for order in orders:
            eligible = order.order_line.filtered(
                lambda line: (
                    line.product_id
                    and not line.is_downpayment
                    and line.display_type in (False, "product")
                )
            )
            if not eligible:
                continue
            file = self.sudo().search(
                [("sale_order_id", "=", order.id)], order="id", limit=1
            )
            if not file:
                file = self.sudo().create({"sale_order_id": order.id})
            file._ensure_shipment_lines(eligible)

    def _ensure_shipment_lines(self, lines):
        self.ensure_one()
        rows = self.env["taj.transport.trip.file.line"].sudo()
        existing = rows.search([("sale_line_id", "in", lines.ids)])
        missing = lines - existing.sale_line_id
        if missing:
            rows.create(
                [{"file_id": self.id, "sale_line_id": line.id} for line in missing]
            )
            self._log_shipment_change(_("Added %s shipment line(s).", len(missing)))

    def _log_shipment_change(self, body):
        self.ensure_one()
        # Keep the index's update information aligned with source-side edits.
        super(TajTransportTripFile, self).write(
            {
                "write_date": fields.Datetime.now(),
                "write_uid": self.env.uid,
            }
        )
        self.message_post(body=body, author_id=self.env.user.partner_id.id)

    def action_open_related_trips(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Related Trips"),
            "res_model": "taj.transport.trip",
            "view_mode": "list,form",
            "views": [
                (
                    self.env.ref(
                        "taj_transport_operations.view_file_related_trip_list"
                    ).id,
                    "list",
                ),
                (
                    self.env.ref(
                        "taj_transport_operations.view_file_related_trip_form"
                    ).id,
                    "form",
                ),
            ],
            "domain": [("id", "in", self.trip_ids.ids)],
        }

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        prepared = []
        for entry in vals_list:
            order = (
                self.env["sale.order"].browse(entry.get("sale_order_id") or 0).exists()
            )
            if not order or order.state not in ("sale", "done"):
                raise ValidationError(
                    _("File Numbers require a confirmed Sales Order.")
                )
            reference = (
                self.env["ir.sequence"]
                .with_company(order.company_id)
                .next_by_code("taj.transport.trip.file")
            )
            if not reference:
                raise UserError(_("Configure the File Number sequence first."))
            prepared.append({"name": reference, "sale_order_id": order.id})
        context = {
            key: value
            for key, value in self.env.context.items()
            if not key.startswith("default_")
        }
        return super(TajTransportTripFile, self.with_context(context)).create(prepared)

    def write(self, vals):
        if "line_ids" in vals:
            self.ensure_one()
            self.check_access("write")
            if set(vals) != {"line_ids"}:
                raise AccessError(_("Only shipment values can be edited here."))
            rows = {row.id: row for row in self.line_ids}
            for command in vals["line_ids"]:
                if command[0] != Command.UPDATE or command[1] not in rows:
                    raise AccessError(
                        _("Only existing shipment rows in this file can be updated.")
                    )
            for command in vals["line_ids"]:
                rows[command[1]].write(command[2])
            return True
        if set(vals) - {"message_main_attachment_id"}:
            raise UserError(
                _("File Number and document links are managed automatically.")
            )
        return super().write(vals)


class TajTransportTripFileLine(models.Model):
    _name = "taj.transport.trip.file.line"
    _description = "Shipment Line"
    _order = "id"
    _check_company_auto = True

    _unique_source = models.Constraint(
        "UNIQUE(sale_line_id)", "A Sales Order line can belong to only one File Number."
    )
    file_id = fields.Many2one(
        "taj.transport.trip.file",
        required=True,
        readonly=True,
        ondelete="cascade",
        index=True,
    )
    sale_line_id = fields.Many2one(
        "sale.order.line",
        required=True,
        readonly=True,
        ondelete="restrict",
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company", related="file_id.company_id", store=True, precompute=True
    )
    sale_order_id = fields.Many2one(
        "sale.order", related="file_id.sale_order_id", store=True, precompute=True
    )
    product_id = fields.Many2one(
        related="sale_line_id.product_id", readonly=False, string="Product"
    )
    file_type_id = fields.Many2one(related="sale_line_id.file_type_id", readonly=False)
    clearing_service = fields.Selection(
        related="sale_line_id.clearing_service", readonly=False
    )
    consignee = fields.Char(
        related="sale_line_id.consignee", readonly=False, string="Consignee"
    )
    bill_of_lading = fields.Char(related="sale_line_id.bill_of_lading", readonly=False)
    container_num = fields.Char(
        related="sale_line_id.container_num", readonly=False, string="Container Number"
    )
    eta = fields.Datetime(related="sale_line_id.eta", readonly=False)
    etol = fields.Date(related="sale_line_id.etol", readonly=False, string="ETOL")
    shipping_line = fields.Char(related="sale_line_id.shipping_line", readonly=False)
    cargo_type_id = fields.Many2one(
        related="sale_line_id.cargo_type_id", readonly=False
    )
    size = fields.Char(related="sale_line_id.size", readonly=False, string="Size")
    weight = fields.Char(related="sale_line_id.weight", readonly=False, string="Weight")
    cargo_description = fields.Text(
        related="sale_line_id.cargo_description", readonly=False, string="Cargo Desc."
    )
    special_handling_instructions = fields.Text(
        related="sale_line_id.special_handling_instructions", readonly=False
    )
    product_uom_qty = fields.Float(
        related="sale_line_id.product_uom_qty", readonly=False, string="QTY"
    )
    trip_id = fields.Many2one(
        "taj.transport.trip", compute="_compute_trip", string="Trip"
    )
    product_editable = fields.Boolean(compute="_compute_trip")

    @api.depends(
        "sale_line_id.transport_trip_ids",
        "sale_line_id.qty_invoiced",
        "sale_line_id.qty_delivered",
    )
    def _compute_trip(self):
        for row in self:
            row.trip_id = row.sale_line_id.transport_trip_ids[:1]
            row.product_editable = not (
                row.trip_id
                or row.sale_line_id.qty_invoiced
                or row.sale_line_id.qty_delivered
            )

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        prepared = []
        for values in vals_list:
            file = (
                self.env["taj.transport.trip.file"]
                .browse(values.get("file_id") or 0)
                .exists()
            )
            line = (
                self.env["sale.order.line"]
                .browse(values.get("sale_line_id") or 0)
                .exists()
            )
            if (
                not file
                or not line
                or line.order_id != file.sale_order_id
                or not line.product_id
                or line.is_downpayment
                or line.display_type not in (False, "product")
            ):
                raise ValidationError(
                    _("A shipment must be a product line from this file's Sales Order.")
                )
            prepared.append({"file_id": file.id, "sale_line_id": line.id})
        context = {
            key: value
            for key, value in self.env.context.items()
            if not key.startswith("default_")
        }
        return super(TajTransportTripFileLine, self.with_context(context)).create(
            prepared
        )

    def write(self, vals):
        self.check_access("write")
        if set(vals) - set(SHIPMENT_FIELDS):
            raise AccessError(
                _(
                    "Only shipment fields can be edited; prices and document links cannot be changed."
                )
            )
        for row in self:
            row.sale_line_id._write_file_shipment(vals, row)
        self.invalidate_recordset(list(vals))
        return True
