from ast import literal_eval

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


# Only trusted accounting methods can supply this object; RPC context values cannot.
_CONFIRMED_SALE_ACCOUNTING = object()
# Same for Cancel: keep commercial edits locked, but allow action_cancel writes.
_CONFIRMED_SALE_CANCEL = object()


class SaleOrder(models.Model):
    _inherit = "sale.order"

    transport_confirmed_readonly = fields.Boolean(compute="_compute_transport_confirmed_readonly")

    @api.depends("state")
    @api.depends_context("uid")
    def _compute_transport_confirmed_readonly(self):
        allowed = self.env.su
        for order in self:
            order.transport_confirmed_readonly = order.state in ("sale", "done") and not allowed

    def _check_confirmed_sale_edit_access(self):
        if self.env.context.get("_confirmed_sale_accounting") is _CONFIRMED_SALE_ACCOUNTING:
            return
        if self.env.context.get("_confirmed_sale_cancel") is _CONFIRMED_SALE_CANCEL:
            return
        if (not self.env.su
                and any(order.state in ("sale", "done") for order in self)):
            raise AccessError(_(
                "Confirmed sales orders are locked. The analytical account can be edited; authorized users can also use Edit Unit Price."
            ))

    def _should_be_locked(self):
        self.ensure_one()
        return True

    def action_cancel(self):
        """Keep confirmed orders locked for edits, but allow Cancel."""
        self.check_access("write")
        return super(SaleOrder, self.with_context(
            _confirmed_sale_cancel=_CONFIRMED_SALE_CANCEL,
        )).action_cancel()

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == "form" and arch.xpath(".//field[@name='transport_confirmed_readonly'][not(ancestor::field)]"):
            # Include fields supplied by other Sales addons, preserving native modifiers.
            for node in arch.xpath(".//field[not(ancestor::field)]"):
                field = self._fields.get(node.get("name"))
                if node.get("name") == "order_line":
                    # Keep the one2many editable so its analytical account cell can be saved.
                    node.set("readonly", "state == 'cancel'")
                    for child in node.xpath("./list//field | ./form//field"):
                        if child.get("name") != "analytic_account_id":
                            readonly = child.get("readonly", "False")
                            if readonly not in ("1", "True"):
                                child.set("readonly", "(%s) or parent.locked or parent.transport_confirmed_readonly" % readonly)
                    options = literal_eval(node.get("options", "{}"))
                    editable_order = [("state", "not in", ["sale", "done"]), ("locked", "=", False)]
                    options.update(create=editable_order, delete=editable_order)
                    node.set("options", repr(options))
                    continue
                if field and not field.readonly:
                    readonly = node.get("readonly", "False")
                    node.set("readonly", "(%s) or locked or transport_confirmed_readonly" % readonly)
        return arch, view

    def _create_down_payment_section_line_if_needed(self):
        self.check_access("write")
        order = self.with_context(_confirmed_sale_accounting=_CONFIRMED_SALE_ACCOUNTING)
        line = super(SaleOrder, order)._create_down_payment_section_line_if_needed()
        return line.with_env(self.env) if line else line

    def _create_down_payment_lines_from_base_lines(self, down_payment_base_lines):
        self.check_access("write")
        order = self.with_context(_confirmed_sale_accounting=_CONFIRMED_SALE_ACCOUNTING)
        lines = super(SaleOrder, order)._create_down_payment_lines_from_base_lines(down_payment_base_lines)
        return lines.with_env(self.env)

    def write(self, vals):
        # Accept only updates to existing own lines, and only the requested field.
        if set(vals) == {"order_line"} and any(order.state in ("sale", "done") for order in self):
            self.check_access("write")
            commands = vals["order_line"]
            if not commands or any(
                len(command) != 3 or command[0] != 1
                or not isinstance(command[2], dict)
                or set(command[2]) != {"analytic_account_id"}
                for command in commands
            ):
                raise AccessError(_("Only analytical account changes to existing lines are allowed."))
            lines = self.env["sale.order.line"].browse([command[1] for command in commands]).exists()
            if len(lines) != len(set(command[1] for command in commands)) or any(line.order_id not in self for line in lines):
                raise AccessError(_("The line must belong to this Sales Order."))
            for command in commands:
                lines.browse(command[1]).write(command[2])
            return True
        # Confirmation may lock the order without granting permission to edit it.
        if vals and vals != {"locked": True}:
            self._check_confirmed_sale_edit_access()
        return super().write(vals)

    transport_customer_email = fields.Char(
        string="Customer Email", related="partner_id.email",
        store=True, readonly=True,
        help="Email taken from the customer record. The trips of this order "
             "show the same address.",
    )

    transport_trip_ids = fields.One2many(
        "taj.transport.trip", "sale_order_id",
        string="Transport Trips", readonly=True,
    )
    transport_trip_count = fields.Integer(
        compute="_compute_transport_trip_count",
        string="Transport Trips",
    )
    trip_file_ids = fields.One2many(
        "taj.transport.trip.file", "sale_order_id", string="Trip File",
        readonly=True, copy=False,
        groups="sales_team.group_sale_salesman,taj_transport_operations.group_transport_user,taj_transport_operations.group_shipment_data_entry",
    )
    trip_file_count = fields.Integer(
        string="Trip File", compute="_compute_trip_file_count",
        groups="sales_team.group_sale_salesman,taj_transport_operations.group_transport_user,taj_transport_operations.group_shipment_data_entry",
    )

    @api.depends("transport_trip_ids")
    def _compute_transport_trip_count(self):
        for order in self:
            order.transport_trip_count = len(order.transport_trip_ids)

    @api.depends("name", "partner_id")
    @api.depends_context("transport_order_label")
    def _compute_display_name(self):
        if not self.env.context.get("transport_order_label"):
            return super()._compute_display_name()
        for order in self:
            order.display_name = order.name

    @api.depends("trip_file_ids")
    def _compute_trip_file_count(self):
        for order in self:
            order.trip_file_count = len(order.trip_file_ids)

    def _action_confirm(self):
        self.order_line._check_goods_transport_configuration()
        result = super()._action_confirm()
        self.env["taj.transport.trip.file"]._ensure_for_sale_lines(self.order_line)
        return result

    def action_view_trip_files(self):
        self.ensure_one()
        self.check_access("read")
        action = self.env.ref(
            "taj_transport_operations.action_transport_trip_file"
        ).sudo().read()[0]
        action["domain"] = [("sale_order_id", "=", self.id)]
        return action

    def action_view_transport_trips(self):
        self.ensure_one()
        action = self.env.ref(
            "taj_transport_operations.action_transport_trip"
        ).read()[0]
        action["domain"] = [("sale_order_id", "=", self.id)]
        return action

    def action_prepare_transport_trips(self):
        self.ensure_one()
        if self.state not in ("sale", "done"):
            raise UserError(_(
                "Confirm the sales order before creating transport trips."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Transport Trips"),
            "res_model": "taj.transport.trip.create.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sale_order_id": self.id},
        }
