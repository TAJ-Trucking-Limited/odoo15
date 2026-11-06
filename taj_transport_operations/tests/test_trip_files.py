"""Trip File generation, source identity, late trip links and Sales access."""

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestTripFiles(TransactionCase):
    def test_trip_summary_uses_full_views_not_file_number_views(self):
        full_list = self.env.ref("taj_transport_operations.view_transport_trip_list")
        full_form = self.env.ref("taj_transport_operations.view_transport_trip_form")
        related_form = self.env.ref("taj_transport_operations.view_file_related_trip_form")
        related_list = self.env.ref("taj_transport_operations.view_file_related_trip_list")
        action = self.env.ref("taj_transport_operations.action_transport_trip")
        self.assertEqual(action.views, [(full_list.id, "list"), (full_form.id, "form")])
        self.assertEqual(self.env["taj.transport.trip"].get_view(view_type="form")["id"], full_form.id)
        self.assertIn('name="trip_summary"', full_form.arch_db)
        self.assertIn('name="action_open_start_trip_wizard"', full_form.arch_db)
        self.assertIn('edit="0"', related_form.arch_db)
        for field in ("planned_departure_at", "planned_arrival_at"):
            self.assertIn(f'name="{field}"', related_form.arch_db)
            self.assertIn(f'name="{field}" optional="hide"', related_list.arch_db)

    def test_trip_form_operation_summary_resources_and_hidden_sales_fields(self):
        view = self.env.ref("taj_transport_operations.view_transport_trip_form")
        arch = etree.fromstring(
            self.env["taj.transport.trip"].get_view(
                view_id=view.id, view_type="form",
            )["arch"]
        )

        operation = arch.xpath("//page[@name='commercial_details']")
        self.assertEqual(len(operation), 1)
        self.assertEqual(operation[0].get("string"), "Operation")

        # These remain on the Sales source but are not displayed on a Trip.
        self.assertFalse(arch.xpath("//field[@name='file_name' or @name='srn']"))

        summary = arch.xpath("//page[@name='trip_summary']")
        self.assertEqual(len(summary), 1)
        columns = summary[0].xpath("./group/group")
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0].get("string"), "Shipment")
        self.assertEqual(columns[1].get("col"), "1")
        sections = columns[1].xpath("./group")
        self.assertEqual([node.get("string") for node in sections],
                         ["Resources", "Trip Progress"])
        self.assertEqual(sections[1].get("name"), "trip_progress_section")
        self.assertEqual(
            [node.get("name") for node in sections[0].xpath("./field")],
            ["current_vehicle_id", "current_driver_id"],
        )
        self.assertEqual(
            [node.get("name") for node in sections[1].xpath("./field")],
            [
                "allocation_date", "actual_departure_at",
                "last_milestone_activity", "last_milestone_at",
                "milestone_progress", "activity_status", "last_activity_at",
                "email_position_preview", "email_position_source",
                "email_position_updated_at",
            ],
        )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Trip File customer"})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Trip File freight",
                "type": "service",
                "list_price": 100,
            }
        )
        cls.brand = cls.env["fleet.vehicle.model.brand"].create(
            {
                "name": "Trip File brand",
            }
        )
        cls.truck_model = cls.env["fleet.vehicle.model"].create(
            {
                "name": "Trip File truck model",
                "brand_id": cls.brand.id,
                "category_id": cls.env.ref(
                    "taj_transport_operations.fleet_category_truck"
                ).id,
            }
        )
        cls.truck = cls.env["fleet.vehicle"].create(
            {
                "model_id": cls.truck_model.id,
                "license_plate": "TRIP-FILE-01",
            }
        )

    def _order(self):
        return self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 5,
                            "container_num": "CONT-A",
                            "price_unit": 100,
                            "vehicle_id": self.truck.id,
                        }
                    ),
                    Command.create(
                        {
                            "display_type": "line_section",
                            "name": "Shipment notes",
                        }
                    ),
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 1,
                            "container_num": "CONT-B",
                            "price_unit": 100,
                            "vehicle_id": self.truck.id,
                        }
                    ),
                    Command.create({"display_type": "line_note", "name": "Keep dry"}),
                ],
            }
        )

    def _confirm(self, orders):
        orders.action_confirm()
        # Read persisted related links, as a fresh UI request does. The test
        # context can retain both precomputed NewIds and their persisted rows.
        orders.env.flush_all()
        orders.invalidate_recordset(["trip_file_ids"])

    def test_confirmation_groups_product_lines_in_one_file_without_trips(self):
        order = self._order()
        self.assertFalse(order.trip_file_ids)
        self._confirm(order)
        files = order.trip_file_ids
        self.assertEqual(len(files), 1, files.ids)
        self.assertEqual(order.trip_file_count, 1)
        self.assertEqual(len(files.line_ids), 2)
        self.assertEqual(files.line_count, 2)
        self.assertEqual(files.partner_id, self.customer)
        self.assertEqual(len(set(files.mapped("name"))), 1)
        self.assertTrue(all(name.startswith("TF/") for name in files.mapped("name")))
        self.assertEqual(
            set(files.line_ids.mapped("container_num")), {"CONT-A", "CONT-B"}
        )
        self.assertFalse(any(files.mapped("bill_of_lading")))
        self.assertFalse(files.mapped("trip_id"))
        self.assertFalse(order.transport_trip_ids)
        self.assertEqual(files.mapped("sale_order_id"), order)

    def test_batch_confirmation_generates_distinct_references_for_all_orders(self):
        orders = self._order() | self._order()
        self._confirm(orders)
        files = orders.trip_file_ids
        self.assertEqual(len(files), 2)
        self.assertEqual(len(set(files.mapped("name"))), 2)

    def test_reconfirmation_reuses_files_and_preserves_container_snapshot(self):
        order = self._order()
        self._confirm(order)
        files = order.trip_file_ids
        files.line_ids.write({"bill_of_lading": "BOL-RETAIN"})
        original = {file.id: file.name for file in files}
        files.line_ids.sale_line_id.write({"container_num": "CHANGED-LATER"})
        if order.locked:
            order.action_unlock()
        order.action_cancel()
        self.assertEqual(order.trip_file_ids, files)
        order.action_draft()
        self._confirm(order)
        self.env["taj.transport.trip.file"]._ensure_for_sale_lines(order.order_line)
        self.assertEqual(order.trip_file_ids, files)
        self.assertEqual(original, {file.id: file.name for file in order.trip_file_ids})
        self.assertEqual(files.bill_of_lading, "BOL-RETAIN")
        self.assertEqual(files.container_number, "CHANGED-LATER")

    def test_each_trip_backfills_only_its_own_line_file(self):
        order = self._order()
        self._confirm(order)
        lines = order.order_line.filtered("product_id")
        trips = self.env["taj.transport.trip"].create(
            [{"sale_line_id": line.id} for line in lines]
        )
        files = order.trip_file_ids
        files.flush_recordset(["trip_id"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files.trip_count, 2)
        self.assertEqual(files.trip_ids, trips)
        for file in files:
            self.assertEqual(
                file.trip_id,
                trips.filtered(lambda trip: trip.sale_line_id == file.sale_line_id),
            )
        with self.assertRaises(UserError):
            files[:1].write({"trip_id": trips[-1].id})
        with self.assertRaises(UserError):
            files[:1].write({"name": "FORGED-REFERENCE"})

    def test_line_added_after_confirmation_gets_file_with_optional_container(self):
        order = self._order()
        self._confirm(order)
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 100,
            }
        )
        self.assertEqual(len(order.trip_file_ids), 1)
        self.assertEqual(order.trip_file_ids.line_count, 3)
        row = order.trip_file_ids.line_ids.filtered(
            lambda row: row.sale_line_id == line
        )
        self.assertEqual(len(row), 1)
        self.assertFalse(row.container_num)

    def test_older_order_gets_file_only_when_its_line_is_used_for_trip(self):
        order = self._order()
        # Represent an order confirmed before the module introduced Trip Files.
        order.write({"state": "sale"})
        self.assertFalse(order.trip_file_ids)
        line = order.order_line.filtered("product_id")[:1]
        trip = self.env["taj.transport.trip"].create({"sale_line_id": line.id})
        self.env.flush_all()
        order.invalidate_recordset(["trip_file_ids"])
        self.assertEqual(len(order.trip_file_ids), 1)
        self.assertEqual(order.trip_file_ids.trip_id, trip)
        self.assertEqual(order.trip_file_ids.sale_line_id, line)

    def test_order_and_trip_links_cannot_be_replaced_by_context_defaults(self):
        other_order = self._order()
        self._confirm(other_order)
        other_trip = self.env["taj.transport.trip"].create(
            {
                "sale_line_id": other_order.order_line.filtered("product_id")[0].id,
            }
        )
        order = self._order()
        self._confirm(
            order.with_context(
                default_sale_order_id=other_order.id,
                default_trip_id=other_trip.id,
            )
        )
        self.assertEqual(order.trip_file_ids.sale_order_id, order)
        self.assertFalse(order.trip_file_ids.trip_id)

    def test_copying_an_order_generates_new_files_only_on_confirmation(self):
        order = self._order()
        self._confirm(order)
        duplicate = order.copy()
        self.assertFalse(duplicate.trip_file_ids)
        self._confirm(duplicate)
        self.assertEqual(len(duplicate.trip_file_ids), 1)
        self.assertEqual(duplicate.trip_file_ids.line_count, 2)
        self.assertFalse(
            set(order.trip_file_ids.ids) & set(duplicate.trip_file_ids.ids)
        )
        self.assertFalse(
            set(order.trip_file_ids.mapped("name"))
            & set(duplicate.trip_file_ids.mapped("name"))
        )

    def test_salesperson_can_confirm_and_read_without_transport_role(self):
        salesperson = new_test_user(
            self.env,
            login="trip_file_salesperson",
            company_id=self.env.company.id,
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        order = self._order()
        order.user_id = salesperson
        self._confirm(order.with_user(salesperson))
        files = order.trip_file_ids.with_user(salesperson)
        self.assertEqual(files.line_count, 2)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            files.line_ids.write({"bill_of_lading": "NOT-AUTHORIZED"})
        other_order = self._order()
        self._confirm(other_order)
        with self.assertRaises(AccessError):
            other_order.trip_file_ids.with_user(salesperson).read(["name"])
        with self.assertRaises(AccessError):
            self.env["taj.transport.trip.file"].with_user(salesperson).create(
                {
                    "sale_line_id": order.order_line.filtered("product_id")[0].id,
                }
            )

    def test_transport_access_stays_within_allowed_companies(self):
        operator = new_test_user(
            self.env,
            login="trip_file_company_operator",
            company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        other_company = self.env["res.company"].create(
            {"name": "Trip File other company"}
        )
        other_order = (
            self.env["sale.order"]
            .with_company(other_company)
            .create(
                {
                    "partner_id": self.customer.id,
                    "order_line": [
                        Command.create(
                            {
                                "product_id": self.product.id,
                                "product_uom_qty": 1,
                                "price_unit": 100,
                            }
                        )
                    ],
                }
            )
        )
        self._confirm(other_order)
        self.assertEqual(other_order.trip_file_ids.company_id, other_company)
        with self.assertRaises(AccessError):
            other_order.trip_file_ids.with_user(operator).with_context(
                allowed_company_ids=[self.env.company.id],
            ).read(["name"])

    def _data_entry_user(self):
        return new_test_user(
            self.env,
            login="file_shipment_entry",
            company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_shipment_data_entry",
        )

    def test_dispatcher_edits_file_form_and_updates_confirmed_sales_line(self):
        order = self._order()
        self._confirm(order)
        dispatcher = new_test_user(
            self.env,
            login="file_form_dispatcher_edit",
            company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        file = order.trip_file_ids.with_user(dispatcher)
        row = file.line_ids[:1]
        source = row.sale_line_id
        old_price = source.price_unit
        self.assertTrue(order.locked)
        self.assertTrue(file.can_edit_shipments)
        file.write({
            "line_ids": [Command.update(row.id, {
                "bill_of_lading": "DISPATCH-BL-EDIT",
                "container_num": "DISPATCH-CONT-EDIT",
                "cargo_description": "Updated from File Number",
                "product_uom_qty": 3,
            })],
        })
        self.assertEqual(source.bill_of_lading, "DISPATCH-BL-EDIT")
        self.assertEqual(source.container_num, "DISPATCH-CONT-EDIT")
        self.assertEqual(source.cargo_description, "Updated from File Number")
        self.assertEqual(source.product_uom_qty, 3)
        self.assertEqual(source.price_unit, old_price)
        self.assertTrue(order.locked)
        self.assertIn("DISPATCH-CONT-EDIT", file.container_number)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            file.write({"line_ids": [Command.update(row.id, {"price_unit": 1})]})

    def test_data_entry_updates_source_without_unlocking_or_changing_price(self):
        order = self._order()
        self._confirm(order)
        actor = self._data_entry_user()
        file = order.trip_file_ids.with_user(actor)
        self.assertEqual(
            order.with_user(actor).action_view_trip_files()["domain"],
            [("sale_order_id", "=", order.id)],
        )
        row = file.line_ids[:1]
        source = row.sale_line_id
        price = source.price_unit
        file.write(
            {
                "line_ids": [
                    Command.update(
                        row.id,
                        {
                            "container_num": "ENTRY-CONT",
                            "consignee": "Entry consignee",
                            "bill_of_lading": "ENTRY-BL",
                            "size": "40ft",
                            "weight": "14000",
                            "cargo_description": "Entry machinery",
                            "special_handling_instructions": "Keep dry",
                            "product_uom_qty": 7,
                        },
                    )
                ]
            }
        )
        self.assertEqual(source.container_num, "ENTRY-CONT")
        self.assertEqual(source.bill_of_lading, "ENTRY-BL")
        self.assertEqual(source.product_uom_qty, 7)
        self.assertEqual(source.price_unit, price)
        self.assertEqual(source.price_subtotal, 7 * price)
        self.assertTrue(order.locked)
        self.assertEqual(file.trip_count, 0)
        self.assertIn("ENTRY-CONT", file.container_number)
        self.assertTrue(file.can_edit_shipments)
        self.assertEqual(file.write_uid, actor)
        messages = self.env["mail.message"].search(
            [
                ("model", "=", "taj.transport.trip.file"),
                ("res_id", "=", file.id),
                ("author_id", "=", actor.partner_id.id),
            ]
        )
        history = " ".join(messages.mapped("body"))
        self.assertIn("CONT-A", history)
        self.assertIn("ENTRY-CONT", history)
        self.assertIn("Bill of Lading", history)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            source.with_context(_file_shipment_write=True).write(
                {"container_num": "BYPASS"}
            )

    def test_file_entry_cannot_edit_prices_references_or_foreign_rows(self):
        order = self._order()
        other = self._order()
        self._confirm(order | other)
        actor = self._data_entry_user()
        file = order.trip_file_ids.with_user(actor)
        row = file.line_ids[:1]
        for values in (
            {"price_unit": 0},
            {"discount": 100},
            {"tax_ids": [Command.clear()]},
            {"sale_line_id": other.order_line.filtered("product_id")[0].id},
            {"file_id": other.trip_file_ids.id},
        ):
            with (
                self.subTest(values=values),
                self.assertRaises(AccessError),
                self.env.cr.savepoint(),
            ):
                row.write(values)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            file.write(
                {
                    "line_ids": [
                        Command.update(
                            other.trip_file_ids.line_ids[0].id,
                            {"container_num": "FOREIGN"},
                        )
                    ]
                }
            )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            file.write({"line_ids": [Command.create({"product_id": self.product.id})]})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            file.write({"name": "FORGED"})
        self.assertEqual(row.sale_line_id.price_unit, 100)

    def test_product_change_preserves_unit_price_until_trip_exists(self):
        order = self._order()
        self._confirm(order)
        actor = self._data_entry_user()
        row = order.trip_file_ids.with_user(actor).line_ids[:1]
        product = self.env["product.product"].create(
            {
                "name": "Replacement shipment product",
                "type": "service",
                "list_price": 9999,
            }
        )
        row.write({"product_id": product.id})
        self.assertEqual(row.sale_line_id.product_id, product)
        self.assertEqual(row.sale_line_id.price_unit, 100)
        self.env["taj.transport.trip"].create({"sale_line_id": row.sale_line_id.id})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            row.write({"product_id": self.product.id})

    def test_data_entry_and_sales_can_read_related_trips_but_cannot_modify_them(self):
        order = self._order()
        actor = self._data_entry_user()
        salesperson = new_test_user(
            self.env,
            login="grouped_file_sales_read",
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        order.user_id = salesperson
        self._confirm(order)
        trips = self.env["taj.transport.trip"].create(
            [
                {"sale_line_id": line.id}
                for line in order.order_line.filtered("product_id")
            ]
        )
        for user in (actor, salesperson):
            file = order.trip_file_ids.with_user(user)
            self.assertEqual(set(file.trip_ids.ids), set(trips.ids))
            self.assertEqual(len(file.trip_ids.read(["name"])), 2)
            self.assertEqual(file.trip_count, 2)
            self.assertEqual(
                file.action_open_related_trips()["domain"],
                [("id", "in", file.trip_ids.ids)],
            )
            from lxml import etree

            view = file.trip_ids.get_view(
                view_id=self.env.ref(
                    "taj_transport_operations.view_file_related_trip_form"
                ).id,
                view_type="form",
            )
            arch = etree.fromstring(view["arch"])
            self.assertEqual(arch.get("edit"), "0")
            self.assertEqual(
                len(
                    file.trip_ids.read(
                        [node.get("name") for node in arch.xpath("//field")]
                    )
                ),
                2,
            )
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                file.trip_ids.write({"notes": "NOT ALLOWED"})

    def test_source_changes_are_live_and_logged_in_file_chatter(self):
        order = self._order()
        self._confirm(order)
        source = order.order_line.filtered("product_id")[0]
        source.write({"container_num": "SOURCE-UPDATED", "bill_of_lading": "SOURCE-BL"})
        row = order.trip_file_ids.line_ids.filtered(
            lambda item: item.sale_line_id == source
        )
        self.assertEqual(row.container_num, "SOURCE-UPDATED")
        self.assertIn("SOURCE-UPDATED", order.trip_file_ids.container_number)
        self.assertIn("SOURCE-BL", order.trip_file_ids.bill_of_lading)
        self.assertTrue(
            self.env["mail.message"].search_count(
                [
                    ("model", "=", "taj.transport.trip.file"),
                    ("res_id", "=", order.trip_file_ids.id),
                    ("body", "ilike", "SOURCE-UPDATED"),
                ]
            )
        )

    def test_context_defaults_cannot_replace_grouped_sources_or_prices(self):
        order = self._order()
        other = self._order()
        foreign_line = other.order_line.filtered("product_id")[0]
        self._confirm(
            order.with_context(
                default_sale_line_id=foreign_line.id,
                default_product_uom_qty=99,
                default_product_id=False,
                default_legacy_sale_line_id=foreign_line.id,
            )
        )
        file = order.trip_file_ids
        self.assertFalse(file.legacy_sale_line_id)
        self.assertEqual(
            set(file.line_ids.sale_line_id.ids),
            set(order.order_line.filtered("product_id").ids),
        )
        self.assertEqual(sorted(file.line_ids.mapped("product_uom_qty")), [1, 5])
        self.assertTrue(
            all(line.price_unit == 100 for line in file.line_ids.sale_line_id)
        )

    def test_optional_columns_row_order_and_chatter(self):
        from lxml import etree

        File = self.env["taj.transport.trip.file"]
        listing = File.get_view(
            view_id=self.env.ref(
                "taj_transport_operations.view_transport_trip_file_list"
            ).id,
            view_type="list",
        )
        for node in etree.fromstring(listing["arch"]).xpath("//list/field"):
            self.assertIn(node.get("optional"), ("show", "hide"), node.get("name"))
        form = File.get_view(
            view_id=self.env.ref(
                "taj_transport_operations.view_transport_trip_file_form"
            ).id,
            view_type="form",
        )
        arch = etree.fromstring(form["arch"])
        self.assertTrue(arch.xpath("//chatter"))
        columns = arch.xpath(
            "//field[@name='line_ids']/list/field[not(@column_invisible)]"
        )
        self.assertEqual(
            [node.get("name") for node in columns],
            [
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
                "trip_id",
            ],
        )
        self.assertTrue(all(node.get("optional") == "show" for node in columns))
        self.assertFalse(
            arch.xpath(
                "//field[@name='price_unit' or @name='discount' or @name='tax_ids']"
            )
        )

    def test_data_entry_cannot_cross_company_boundary(self):
        actor = self._data_entry_user()
        company = self.env["res.company"].create({"name": "Grouped other company"})
        order = self._order().with_company(company)
        order.company_id = company
        self._confirm(order)
        row = (
            order.trip_file_ids.line_ids[:1]
            .with_user(actor)
            .with_context(allowed_company_ids=[self.env.company.id])
        )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            row.write({"container_num": "CROSS-COMPANY"})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            row.file_id.read(["name"])

    def test_legacy_file_migration_preserves_numbers_and_snapshots(self):
        import runpy
        from odoo.tools import file_path

        order = self._order()
        order.write({"state": "sale"})
        sources = order.order_line.filtered("product_id")
        files = self.env["taj.transport.trip.file"].create(
            [
                {"sale_order_id": order.id},
                {"sale_order_id": order.id},
            ]
        )
        names = set(files.mapped("name"))
        self.env.flush_all()
        for file, source in zip(files, sources):
            self.env.cr.execute(
                """UPDATE taj_transport_trip_file
                SET sale_line_id = %s, container_number = 'ORIGINAL-SNAPSHOT', bill_of_lading = 'LEGACY-BILL'
                WHERE id = %s""",
                (source.id, file.id),
            )
        pre = runpy.run_path(
            file_path(
                "taj_transport_operations/migrations/19.0.1.24.0/pre-migration.py"
            )
        )["migrate"]
        post = runpy.run_path(
            file_path(
                "taj_transport_operations/migrations/19.0.1.24.0/post-migration.py"
            )
        )["migrate"]
        pre(self.env.cr, "19.0.1.23.0")
        self.env.invalidate_all()
        post(self.env.cr, "19.0.1.23.0")
        self.env.flush_all()
        self.assertEqual(set(files.mapped("name")), names)
        self.assertEqual(len(files.line_ids), 2)
        self.assertEqual(set(files.line_ids.sale_line_id.ids), set(sources.ids))
        self.assertEqual(
            set(files.mapped("legacy_container_number")), {"ORIGINAL-SNAPSHOT"}
        )
        self.assertEqual(set(sources.mapped("bill_of_lading")), {"LEGACY-BILL"})
        self.assertEqual(set(files.mapped("container_number")), {"CONT-A", "CONT-B"})
        sources.write({"bill_of_lading": "LATER-EDIT"})
        post(self.env.cr, "19.0.1.23.0")
        self.assertEqual(len(files.line_ids), 2)
        self.assertEqual(set(sources.mapped("bill_of_lading")), {"LATER-EDIT"})
