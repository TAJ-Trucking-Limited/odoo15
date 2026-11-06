"""Sales shipment entry, Goods validation and assignment backfill."""

from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestSaleLineColumns(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Shipment customer"})
        cls.goods = cls.env["product.product"].create(
            {"name": "Shipment goods", "type": "consu"}
        )
        cls.service = cls.env["product.product"].create(
            {"name": "Shipment service", "type": "service"}
        )
        cls.order = cls.env["sale.order"].create({"partner_id": cls.customer.id})
        cls.file_type = cls.env.ref("taj_transport_operations.file_type_transit_import")

    def _line(self, product=None, **values):
        return self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "product_id": (product or self.goods).id,
                "product_uom_qty": 1,
                **values,
            }
        )

    def _route(self, **values):
        return self.env["taj.transport.route"].create(
            {
                "name": "Goods route",
                "company_id": self.order.company_id.id,
                "product_tmpl_id": self.goods.product_tmpl_id.id,
                **values,
            }
        )

    def test_goods_line_requires_file_type_and_route(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._line()

    def test_goods_file_type_requires_active_product_linked_route(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._line(file_type_id=self.file_type.id)
        route = self._route(active=False)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._line(file_type_id=self.file_type.id)
        route.active = True
        line = self._line(file_type_id=self.file_type.id)
        self.assertEqual(line.file_type_id, self.file_type)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            line.file_type_id = False

    def test_file_type_defaults_from_single_matching_route(self):
        route = self._route(file_type_id=self.file_type.id)
        line = self._line()
        self.assertEqual(line.file_type_id, self.file_type)
        self.assertEqual(
            self.env["taj.transport.route"]._matching_for_sale_line(line), route
        )
        # The inline quotation form uses the same suggestion.
        unsaved = self.env["sale.order.line"].new({
            "order_id": self.order.id,
            "product_id": self.goods.id,
        })
        unsaved._onchange_route_file_type()
        self.assertEqual(unsaved.file_type_id, self.file_type)
        # This is an editable default, not a related/computed field.
        unsaved.file_type_id = self.env.ref(
            "taj_transport_operations.file_type_transit_export"
        )
        self.assertNotEqual(unsaved.file_type_id, self.file_type)

    def test_manual_file_type_change_must_have_matching_route(self):
        import_route = self._route(file_type_id=self.file_type.id)
        export = self.env.ref(
            "taj_transport_operations.file_type_transit_export"
        )
        line = self._line()
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            line.file_type_id = export
        export_route = self.env["taj.transport.route"].create({
            "name": "Goods export route",
            "product_tmpl_id": self.goods.product_tmpl_id.id,
            "company_id": self.order.company_id.id,
            "file_type_id": export.id,
        })
        line.file_type_id = export
        self.assertEqual(line.file_type_id, export)
        self.assertEqual(
            self.env["taj.transport.route"]._matching_for_sale_line(line),
            export_route,
        )
        Trip = self.env["taj.transport.trip"]
        with self.assertRaises(UserError), self.env.cr.savepoint():
            Trip._require_matching_route_for_sale_line(line, import_route)
        self.order.action_confirm()
        # Direct API creates must respect the same type rule.
        with self.assertRaisesRegex(ValidationError, "File Type"), self.env.cr.savepoint():
            Trip.create({"sale_line_id": line.id, "route_id": import_route.id})
        trip = Trip.create({"sale_line_id": line.id, "route_id": export_route.id})
        with self.assertRaisesRegex(ValidationError, "File Type"), self.env.cr.savepoint():
            trip.write({"route_id": import_route.id})
        # Even a privileged shipment update cannot desynchronize an existing trip.
        with self.assertRaisesRegex(ValidationError, "File Type"), self.env.cr.savepoint():
            line.sudo().write({"file_type_id": self.file_type.id})

    def test_mixed_route_types_do_not_pick_an_ambiguous_default(self):
        self._route(file_type_id=self.file_type.id)
        export = self.env.ref(
            "taj_transport_operations.file_type_transit_export"
        )
        self.env["taj.transport.route"].create({
            "name": "Goods export route",
            "product_tmpl_id": self.goods.product_tmpl_id.id,
            "company_id": self.order.company_id.id,
            "file_type_id": export.id,
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._line()
        line = self._line(file_type_id=export.id)
        self.assertEqual(line.file_type_id, export)

    def test_same_file_type_across_multiple_routes_is_unambiguous(self):
        self._route(file_type_id=self.file_type.id)
        self.env["taj.transport.route"].create({
            "name": "Second goods import route",
            "product_tmpl_id": self.goods.product_tmpl_id.id,
            "company_id": self.order.company_id.id,
            "file_type_id": self.file_type.id,
        })
        self.assertEqual(self._line().file_type_id, self.file_type)

    def test_legacy_unclassified_route_stays_usable_for_manual_type(self):
        route = self._route()
        line = self._line(file_type_id=self.file_type.id)
        self.assertEqual(
            self.env["taj.transport.route"]._matching_for_sale_line(line), route
        )

    def test_services_and_notes_need_no_file_type_or_route(self):
        self.assertTrue(self._line(self.service))
        self.assertTrue(
            self.env["sale.order.line"].create(
                {
                    "order_id": self.order.id,
                    "display_type": "line_note",
                    "name": "Shipment notes",
                }
            )
        )

    def test_changing_service_line_to_goods_requires_configuration(self):
        line = self._line(self.service)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            line.product_id = self.goods

    def test_confirmation_rechecks_route_configuration(self):
        route = self._route()
        self._line(file_type_id=self.file_type.id)
        route.active = False
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.order.action_confirm()
        self.assertEqual(self.order.state, "draft")

    def test_bill_of_lading_is_copied_to_generated_trip_file(self):
        line = self._line(
            self.service,
            bill_of_lading="BL-123",
            clearing_service="included",
            eta="2026-12-01",
            shipping_line="Shipment carrier",
        )
        self.order.action_confirm()
        trip_file = self.env["taj.transport.trip.file"].search(
            [("sale_line_id", "=", line.id)]
        )
        self.assertEqual(trip_file.bill_of_lading, "BL-123")

    def test_quotation_columns_follow_shipment_entry_order(self):
        self.env.user.group_ids |= self.env.ref("analytic.group_analytic_accounting")
        view = self.env["sale.order"].get_view(
            view_id=self.env.ref("sale.view_order_form").id,
            view_type="form",
        )
        arch = etree.fromstring(view["arch"])
        columns = arch.xpath("//field[@name='order_line']/list")[0]
        visible = [
            node.get("name")
            for node in columns
            if node.tag == "field"
            and node.get("column_invisible") not in ("True", "1")
            and node.get("optional") != "hide"
            and node.get("widget") != "handle"
        ]
        self.assertEqual(
            visible,
            [
                "product_template_id",
                "file_type_id",
                "clearing_service",
                "consignee",
                "bill_of_lading",
                "container_num",
                "eta",
                "shipping_line",
                "cargo_type_id",
                "size",
                "weight",
                "cargo_description",
                "special_handling_instructions",
                "product_uom_qty",
                "price_unit",
                "tax_ids",
                "price_subtotal",
                "price_total",
                "analytic_account_id",
                "vehicle_id",
            ],
        )
        self.assertEqual(
            columns.xpath("field[@name='vehicle_id']")[0].get("readonly"), "1"
        )

    def test_sales_user_can_select_file_type_without_transport_role(self):
        salesperson = new_test_user(
            self.env,
            login="shipment_salesperson",
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        self.order.user_id = salesperson
        self._route()
        line = (
            self.env["sale.order.line"]
            .with_user(salesperson)
            .create(
                {
                    "order_id": self.order.id,
                    "product_id": self.goods.id,
                    "file_type_id": self.file_type.id,
                    "product_uom_qty": 1,
                }
            )
        )
        self.assertEqual(line.file_type_id.name, "Transit Import")

    def test_route_from_another_company_does_not_satisfy_goods_rule(self):
        other = self.env["res.company"].create({"name": "Other shipment company"})
        self._route(company_id=other.id)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._line(file_type_id=self.file_type.id)
