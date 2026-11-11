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
            columns.xpath("field[@name='vehicle_id']")[0].get("readonly"),
            "1",
        )
        self.assertEqual(columns.xpath("field[@name='size']")[0].get("optional"), "hide")
        etol = columns.xpath("field[@name='etol']")[0]
        self.assertEqual(etol.get("optional"), "hide")
        self.assertIn("'numeric': True", etol.get("options") or "")
        self.assertEqual(
            [node.get("name") for node in columns if node.get("name") in ("eta", "etol")],
            ["eta", "etol"],
        )
        line_popup = arch.xpath("//field[@name='order_line']/form")[0]
        self.assertEqual(len(line_popup.xpath(".//field[@name='analytic_account_id']")), 1)
        self.assertEqual(len(columns.xpath("field[@name='analytic_account_id']")), 1)

    def test_fleet_vehicle_list_shows_transport_availability_and_last_activity(self):
        fleet_user = new_test_user(
            self.env,
            login="shipment_fleet_list_transport_user",
            groups=(
                "base.group_user,fleet.fleet_group_user,"
                "taj_transport_operations.group_transport_user"
            ),
        )
        Fleet = self.env["fleet.vehicle"].with_user(fleet_user)
        view = Fleet.get_view(
            view_id=self.env.ref("fleet.fleet_vehicle_view_tree").id,
            view_type="list",
        )
        listing = etree.fromstring(view["arch"]).xpath("//list")[0]
        availability = listing.xpath("./field[@name='transport_availability_state']")[0]
        activity = listing.xpath("./field[@name='transport_group_last_activity_at']")[0]
        self.assertEqual(availability.get("optional"), "show")
        self.assertEqual(availability.get("readonly"), "1")
        self.assertEqual(availability.get("widget"), "badge")
        self.assertEqual(activity.get("optional"), "show")
        self.assertEqual(activity.get("readonly"), "1")
        self.assertIn("'numeric': True", activity.get("options") or "")
        self.assertTrue(Fleet._fields["transport_availability_state"].groups)
        self.assertTrue(Fleet._fields["transport_group_last_activity_at"].groups)
        # Field groups keep the values readable for transport users without sudo.
        truck = Fleet.search([], limit=1)
        if truck:
            truck.read(["transport_availability_state", "transport_group_last_activity_at"])

        availability_list = etree.fromstring(
            self.env.ref("taj_transport_operations.view_transport_trailer_list").get_combined_arch()
        )
        self.assertEqual(
            availability_list.xpath("./field[@name='transport_group_last_activity_at']")[0].get(
                "optional"
            ),
            "show",
        )

    def test_without_allocation_list_only_edits_etol_inline(self):
        dispatcher = new_test_user(
            self.env,
            login="shipment_inline_etol_view_user",
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        view = self.env["sale.order.line"].with_user(dispatcher).get_view(
            view_id=self.env.ref(
                "taj_transport_operations.view_sale_order_line_without_allocation_list"
            ).id,
            view_type="list",
        )
        listing = etree.fromstring(view["arch"]).xpath("//list")[0]
        self.assertEqual(listing.get("editable"), "bottom")
        self.assertEqual(listing.get("create"), "0")
        self.assertEqual(listing.get("delete"), "0")
        for field in listing.xpath("./field"):
            if field.get("name") == "etol":
                self.assertNotEqual(field.get("readonly"), "1")
            else:
                self.assertEqual(field.get("readonly"), "1", field.get("name"))
        self.assertEqual(
            len(listing.xpath("./header/button[@name='action_assign_selected_truck']")),
            1,
        )
        self.assertEqual(
            len(listing.xpath("./button[@name='action_open_assign_truck_wizard']")),
            1,
        )

    def test_draft_trip_checkpoint_badges_and_progress_are_hidden_per_row(self):
        summary_view = self.env.ref("taj_transport_operations.view_transport_trip_list")
        summary = etree.fromstring(summary_view.get_combined_arch())
        progress = summary.xpath("./field[@name='milestone_progress']")
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0].get("invisible"), "state == 'draft'")
        trip_status = summary.xpath("./field[@name='state']")
        self.assertEqual(len(trip_status), 1)
        self.assertFalse(trip_status[0].get("invisible"))

        milestone_view = self.env.ref("taj_transport_operations.view_transport_milestone_list")
        milestones = etree.fromstring(milestone_view.get_combined_arch())
        checkpoint_status = milestones.xpath("./field[@name='state']")
        self.assertEqual(len(checkpoint_status), 1)
        self.assertEqual(checkpoint_status[0].get("invisible"), "trip_state == 'draft'")
        trip_status = milestones.xpath("./field[@name='trip_state']")
        self.assertEqual(len(trip_status), 1)
        self.assertFalse(trip_status[0].get("invisible"))

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
