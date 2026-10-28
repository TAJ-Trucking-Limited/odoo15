"""One generated shipment file per confirmed product/service sales line."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class TajTransportTripFile(models.Model):
    _name = "taj.transport.trip.file"
    _description = "Trip File"
    _order = "id desc"
    _check_company_auto = True

    _unique_sale_line = models.Constraint(
        "UNIQUE(sale_line_id)", "A sales order line can only have one Trip File.",
    )
    _unique_reference = models.Constraint(
        "UNIQUE(name)", "The Trip File reference must be unique.",
    )
    _unique_trip = models.Constraint(
        "UNIQUE(trip_id)", "A trip can only be linked to one Trip File.",
    )

    name = fields.Char(
        string="Reference Number", required=True, readonly=True, copy=False,
        index=True,
    )
    bill_of_lading = fields.Char(string="Bill of Lading", copy=False)
    container_number = fields.Char(
        string="Container Number", readonly=True, copy=False,
        help="Copied from the sales line when the Trip File is created. "
        "Later changes to the sales line do not change this value.",
    )
    sale_line_id = fields.Many2one(
        "sale.order.line", string="Sales Order Line", required=True,
        readonly=True, check_company=True, ondelete="restrict", index=True,
        copy=False,
    )
    sale_order_id = fields.Many2one(
        "sale.order", string="Sales Order", related="sale_line_id.order_id",
        store=True, precompute=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        "res.company", related="sale_order_id.company_id", store=True,
        precompute=True, readonly=True, index=True,
    )
    trip_id = fields.Many2one(
        "taj.transport.trip", string="Trip", compute="_compute_trip_id",
        store=True, precompute=True, readonly=True, check_company=True, ondelete="restrict",
        index=True, copy=False,
        groups="taj_transport_operations.group_transport_user",
    )

    @api.depends("sale_line_id.transport_trip_ids")
    def _compute_trip_id(self):
        for trip_file in self:
            trip_file.trip_id = trip_file.sale_line_id.transport_trip_ids[:1]

    @api.model
    def _ensure_for_sale_lines(self, lines):
        """Generate missing files under a line lock; keep confirmation idempotent.

        Sales confirmation must work without a transport role. Check the
        source lines with the caller's access before generating system-owned
        records with elevated rights. No files from other lines are exposed.
        """
        if not lines:
            return
        lines.check_access("read")
        lines.flush_recordset(["order_id", "product_id", "display_type", "container_num"])
        self.env.cr.execute(
            "SELECT id FROM sale_order_line WHERE id = ANY(%s) "
            "ORDER BY id FOR UPDATE", (sorted(lines.ids),),
        )
        lines.invalidate_recordset(["order_id", "product_id", "display_type", "container_num"])
        eligible = lines.filtered(
            lambda line: line.order_id.state in ("sale", "done")
            and line.product_id and line.display_type in (False, "product")
        )
        if not eligible:
            return
        TripFile = self.sudo()
        existing = TripFile.search([("sale_line_id", "in", eligible.ids)])
        missing = eligible - existing.mapped("sale_line_id")
        if missing:
            TripFile.create([{"sale_line_id": line.id} for line in missing])

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        prepared = []
        for entry in vals_list:
            line = self.env["sale.order.line"].browse(
                entry.get("sale_line_id") or 0,
            ).exists()
            if (
                not line or line.order_id.state not in ("sale", "done")
                or not line.product_id or line.display_type not in (False, "product")
            ):
                raise ValidationError(_(
                    "Trip Files require a product/service line on a confirmed sales order."
                ))
            reference = self.env["ir.sequence"].with_company(line.company_id).next_by_code(
                "taj.transport.trip.file",
            )
            if not reference:
                raise UserError(_("Configure the Trip File reference sequence first."))
            prepared.append({
                "name": reference,
                "sale_line_id": line.id,
                "container_number": line.container_num or False,
                "bill_of_lading": entry.get("bill_of_lading") or False,
            })
        return super().create(prepared)

    def write(self, vals):
        if set(vals) - {"bill_of_lading"}:
            raise UserError(_(
                "Only Bill of Lading can be edited on a Trip File. "
                "Its reference, source line, container and trip link are system-managed."
            ))
        return super().write(vals)
