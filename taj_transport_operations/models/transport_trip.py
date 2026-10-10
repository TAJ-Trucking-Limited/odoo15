"""Transport trip foundation; invoicing and operational assignments stay separate."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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
        [("draft", "Draft")], default="draft", string="Status",
        required=True, readonly=True, tracking=True,
    )
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
        "res.partner", string="Transporter", tracking=True,
        check_company=True, copy=False,
        help="Company or contact responsible for executing this transport.",
    )
    transport_reference = fields.Char(
        string="Transport / Booking Reference", tracking=True, copy=False,
        help="Optional dispatcher or carrier reference for this trip.",
    )
    cargo_type = fields.Char(
        string="Cargo Type", tracking=True, copy=False,
        help="Operational description of the cargo category.",
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

    @api.model_create_multi
    def create(self, vals_list):
        new_vals_list = []
        for entry in vals_list:
            vals = dict(entry)
            if not vals.get("sale_line_id"):
                raise ValidationError(_("A sales order line is required."))
            line = self.env["sale.order.line"].browse(
                vals["sale_line_id"]
            ).exists()
            self._validate_sale_line(line)
            if vals.get("state", "draft") != "draft":
                raise ValidationError(_(
                    "Only Draft transport trips can be created in this release."
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
        if vals.get("state", "draft") != "draft":
            raise UserError(_(
                "Trip status transitions will be introduced in a later batch."
            ))
        return super().write(vals)

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
