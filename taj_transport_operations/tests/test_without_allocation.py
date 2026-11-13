"""Sale Orders without Allocation: columns, the Assign Truck dialog and ETOL."""

from datetime import date

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestWithoutAllocation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Allocation customer"})
        cls.brand = cls.env["fleet.vehicle.model.brand"].create(
            {
                "name": "Allocation brand",
            }
        )
        cls.truck_category = cls.env.ref(
            "taj_transport_operations.fleet_category_truck"
        )
        cls.trailer_category = cls.env.ref(
            "taj_transport_operations.fleet_category_trailer"
        )
        cls.truck_model = cls.env["fleet.vehicle.model"].create(
            {
                "name": "Allocation truck model",
                "brand_id": cls.brand.id,
                "category_id": cls.truck_category.id,
            }
        )
        cls.truck = cls.env["fleet.vehicle"].create(
            {
                "model_id": cls.truck_model.id,
                "license_plate": "ALLOC-01",
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Allocation freight",
                "type": "service",
                "list_price": 100,
            }
        )
        cls.order = cls.env["sale.order"].create({"partner_id": cls.customer.id})
        cls.line = cls.env["sale.order.line"].create(
            {
                "order_id": cls.order.id,
                "product_id": cls.product.id,
                "name": "Freight without truck",
                "product_uom_qty": 1,
                "price_unit": 100,
                "etol": "2026-11-10",
            }
        )
        cls.order.action_confirm()
        cls.env.flush_all()
        cls.line.invalidate_recordset(
            [
                "transport_file_id",
                "transport_file_line_ids",
                "transport_trip_ids",
            ]
        )

    def _wizard(self, user=None, **values):
        Wizard = self.env["taj.transport.assign.truck.wizard"]
        if user:
            Wizard = Wizard.with_user(user)
        return Wizard.create(
            {
                "sale_line_id": self.line.id,
                "etol": self.line.etol,
                **values,
            }
        )

    def test_menu_action_and_no_default_grouping(self):
        action = self.env.ref(
            "taj_transport_operations.action_sale_order_line_without_truck"
        )
        menu = self.env.ref(
            "taj_transport_operations.menu_transport_lines_without_truck"
        )
        server_action = self.env.ref(
            "taj_transport_operations.server_action_lines_without_truck"
        )
        self.assertEqual(action.name, "Sale Orders without Allocation")
        self.assertEqual(menu.name, "Sale Orders without Allocation")
        self.assertEqual(server_action.name, "Sale Orders without Allocation")
        self.assertNotIn(
            "search_default_group_by_order",
            action.context or "",
        )
        self.assertEqual(
            action.view_id,
            self.env.ref(
                "taj_transport_operations.view_sale_order_line_without_allocation_list"
            ),
        )

    def test_worklist_columns_order_and_show_hide(self):
        arch = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_sale_order_line_without_allocation_list"
            ).get_combined_arch()
        )
        columns = arch.xpath("//list/field")
        self.assertEqual(
            [node.get("name") for node in columns],
            [
                "transport_file_id",
                "file_type_id",
                "clearing_service",
                "order_id",
                "order_partner_id",
                "consignee",
                "product_id",
                "name",
                "product_uom_qty",
                "vehicle_id",
                "eta",
                "cargo_type_id",
                "size",
                "weight",
                "cargo_description",
                "special_handling_instructions",
                "etol",
            ],
        )
        optional = {node.get("name"): node.get("optional") for node in columns}
        self.assertEqual(optional["product_id"], "hide")
        self.assertEqual(optional["product_uom_qty"], "hide")
        for name, value in optional.items():
            if name not in ("product_id", "product_uom_qty"):
                self.assertEqual(value, "show", name)
        button = arch.xpath("//list/button[@name='action_open_assign_truck_wizard']")
        self.assertEqual(len(button), 1)
        self.assertEqual(button[0].get("string"), "Assign Truck")

    def test_not_started_worklist_displays_allocated_driver(self):
        listing = self.env.ref(
            "taj_transport_operations.view_sale_order_line_dispatch_list"
        )
        arch = etree.fromstring(listing.get_combined_arch())
        columns = [node.get("name") for node in arch.xpath("./field")]
        self.assertEqual(
            columns[columns.index("vehicle_id") + 1],
            "transport_dispatch_driver_id",
        )
        form = etree.fromstring(self.env.ref(
            "taj_transport_operations.view_sale_order_line_dispatch_form"
        ).get_combined_arch())
        self.assertTrue(form.xpath("//field[@name='transport_dispatch_driver_id']"))

        default = self.env["res.partner"].create({"name": "Fleet driver"})
        chosen = self.env["res.partner"].create({"name": "Dispatch driver"})
        self.truck.driver_id = default
        self.line.sudo().write({"vehicle_id": self.truck.id})
        self.line.invalidate_recordset(["transport_dispatch_driver_id"])
        self.assertEqual(self.line.transport_dispatch_driver_id, default)
        self.line.sudo().write({
            "transport_allocated_driver_id": chosen.id,
            "transport_driver_choice_set": True,
        })
        self.line.invalidate_recordset(["transport_dispatch_driver_id"])
        self.assertEqual(self.line.transport_dispatch_driver_id, chosen)
        self.line.sudo().write({
            "transport_allocated_driver_id": False,
            "transport_driver_choice_set": True,
        })
        self.line.invalidate_recordset(["transport_dispatch_driver_id"])
        self.assertFalse(self.line.transport_dispatch_driver_id)

    def test_file_number_is_shown_on_the_sales_line(self):
        self.assertTrue(self.line.transport_file_id)
        self.assertEqual(self.line.transport_file_id, self.order.trip_file_ids)

    def test_assign_truck_form_loads_selected_sale_line_and_company(self):
        action = self.line.action_open_assign_truck_wizard()
        view = self.env.ref(
            "taj_transport_operations.view_assign_truck_wizard_form"
        )
        arch = etree.fromstring(view.get_combined_arch())
        source = arch.xpath("./form/field[@name='sale_line_id']")
        self.assertEqual(len(source), 1)
        self.assertEqual(source[0].get("invisible"), "1")
        Wizard = self.env["taj.transport.assign.truck.wizard"].with_context(
            action["context"]
        )
        defaults = Wizard.default_get(["sale_line_id", "etol"])
        self.assertEqual(defaults["sale_line_id"], self.line.id)
        self.assertEqual(defaults["etol"], self.line.etol)
        wizard = Wizard.new(defaults)
        self.assertEqual(wizard.sale_order_id, self.order)
        self.assertEqual(wizard.product_id, self.product)
        self.assertEqual(wizard.company_id, self.order.company_id)

    def test_assign_truck_accepts_active_uncategorized_fleet_vehicle(self):
        unclassified = self.env["fleet.vehicle"].create({
            "model_id": self.truck_model.id,
            "license_plate": "ALLOC-UNCATEGORIZED",
            "category_id": False,
        })
        self.assertFalse(unclassified.category_id)
        Wizard = self.env["taj.transport.assign.truck.wizard"]
        domain = safe_eval(
            Wizard._fields["vehicle_id"].domain(Wizard),
            {"company_id": self.order.company_id.id},
        )
        candidates = self.env["fleet.vehicle"].search(domain)
        self.assertIn(unclassified, candidates)
        self.assertIn(self.truck, candidates)
        self._wizard(vehicle_id=unclassified.id).action_assign_truck()
        self.assertEqual(self.line.vehicle_id, unclassified)
        self.assertFalse(self.line.transport_trip_ids)

    def test_driver_defaults_from_fleet_and_is_editable_in_wizard(self):
        default_driver = self.env["res.partner"].create({
            "name": "Fleet default driver",
        })
        replacement = self.env["res.partner"].create({
            "name": "Manually selected driver",
        })
        self.truck.driver_id = default_driver
        arch = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_assign_truck_wizard_form"
            ).get_combined_arch()
        )
        self.assertEqual(len(arch.xpath("//field[@name='driver_id']")), 1)
        wizard = self.env["taj.transport.assign.truck.wizard"].new({
            "sale_line_id": self.line.id,
            "vehicle_id": self.truck.id,
        })
        self.assertEqual(wizard.driver_id, default_driver)
        wizard.driver_id = replacement
        self.assertEqual(wizard.driver_id, replacement)

    def test_fleet_default_driver_survives_wizard_save_and_trip_creation(self):
        fleet_driver = self.env["res.partner"].create({
            "name": "Fleet preset driver",
        })
        self.truck.driver_id = fleet_driver
        wizard = self._wizard(vehicle_id=self.truck.id)
        self.assertEqual(wizard.driver_id, fleet_driver)
        wizard.action_assign_truck()
        self.assertEqual(self.line.transport_allocated_driver_id, fleet_driver)
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        self.assertEqual(trip.current_assignment_id.driver_id, fleet_driver)

    def test_manual_driver_is_kept_until_trip_is_created(self):
        fleet_driver = self.env["res.partner"].create({
            "name": "Fleet driver",
        })
        chosen_driver = self.env["res.partner"].create({
            "name": "Selected driver",
        })
        self.truck.driver_id = fleet_driver
        self._wizard(
            vehicle_id=self.truck.id, driver_id=chosen_driver.id,
        ).action_assign_truck()
        self.assertEqual(self.line.vehicle_id, self.truck)
        self.assertEqual(self.line.transport_allocated_driver_id, chosen_driver)
        self.assertTrue(self.line.transport_driver_choice_set)
        self.assertFalse(self.line.transport_trip_ids)
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        self.assertEqual(trip.current_assignment_id.vehicle_id, self.truck)
        self.assertEqual(trip.current_assignment_id.driver_id, chosen_driver)
        self.assertEqual(self.truck.driver_id, fleet_driver)

    def test_empty_manual_driver_stays_empty_when_trip_is_created(self):
        fleet_driver = self.env["res.partner"].create({
            "name": "Fleet driver with manual blank",
        })
        self.truck.driver_id = fleet_driver
        self._wizard(vehicle_id=self.truck.id, driver_id=False).action_assign_truck()
        self.assertTrue(self.line.transport_driver_choice_set)
        self.assertFalse(self.line.transport_allocated_driver_id)
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        self.assertEqual(trip.current_assignment_id.vehicle_id, self.truck)
        self.assertFalse(trip.current_assignment_id.driver_id)

    def test_existing_draft_trip_uses_selected_driver(self):
        original = self.env["res.partner"].create({
            "name": "Original driver",
        })
        chosen = self.env["res.partner"].create({
            "name": "New draft trip driver",
        })
        self.truck.driver_id = original
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        self.assertFalse(trip.assignment_ids)
        self._wizard(
            vehicle_id=self.truck.id, driver_id=chosen.id,
        ).action_assign_truck()
        self.assertEqual(trip.current_assignment_id.driver_id, chosen)
        self.assertEqual(trip.current_assignment_id.vehicle_id, self.truck)
        self.assertEqual(self.line.vehicle_id, self.truck)
        self.assertEqual(self.line.transport_trip_ids, trip)

    def test_assign_truck_replaces_existing_draft_trip_resources(self):
        """Stuck without-allocation rows (trip has Resources, line truck empty)."""
        from datetime import timedelta

        from odoo import fields

        first = self.env["fleet.vehicle"].create({
            "model_id": self.truck_model.id,
            "license_plate": "ALLOC-FIRST",
        })
        replacement = self.env["fleet.vehicle"].create({
            "model_id": self.truck_model.id,
            "license_plate": "ALLOC-NEXT",
        })
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        self.env["taj.transport.assignment"].create({
            "trip_id": trip.id,
            "vehicle_id": first.id,
            "driver_id": self.env["res.partner"].create({"name": "First driver"}).id,
            "effective_from": fields.Datetime.now() - timedelta(hours=1),
            "reason": "Seeded before commercial truck",
        })
        # Simulate the stale worklist row: trip allocated, sale line still empty.
        self.line.sudo().write({"vehicle_id": False})
        self.assertFalse(self.line.vehicle_id)
        self.assertTrue(trip.current_assignment_id)
        chosen = self.env["res.partner"].create({"name": "Replacement driver"})
        self._wizard(
            vehicle_id=replacement.id, driver_id=chosen.id,
        ).action_assign_truck()
        trip.invalidate_recordset()
        self.assertEqual(self.line.vehicle_id, replacement)
        self.assertEqual(trip.current_assignment_id.vehicle_id, replacement)
        self.assertEqual(trip.current_assignment_id.driver_id, chosen)
        self.assertEqual(len(trip.assignment_ids), 2)

    def test_assign_truck_names_blocking_trip_state(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        trip.sudo().write({"state": "cancelled"})
        with self.assertRaisesRegex(UserError, "cancelled"):
            self._wizard(vehicle_id=self.truck.id).action_assign_truck()
        self.assertFalse(self.line.vehicle_id)

    def test_assign_truck_rejects_inactive_or_other_company_driver(self):
        invalid = self.env["res.partner"].create({"name": "Inactive driver"})
        invalid.active = False
        other_company = self.env["res.company"].create({
            "name": "Driver's foreign company",
        })
        foreign = self.env["res.partner"].create({
            "name": "Foreign driver", "company_id": other_company.id,
        })
        for driver in (invalid, foreign):
            with self.subTest(driver=driver.name), self.assertRaises(UserError):
                self._wizard(
                    vehicle_id=self.truck.id, driver_id=driver.id,
                ).action_assign_truck()
            self.assertFalse(self.line.vehicle_id)
            self.assertFalse(self.line.transport_driver_choice_set)

    def test_allocation_requires_etol_and_saves_dispatch_changes(self):
        dispatcher = new_test_user(
            self.env, login="etol_dispatcher",
            company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        with self.assertRaisesRegex(UserError, "ETOL"), self.env.cr.savepoint():
            self._wizard(
                dispatcher, vehicle_id=self.truck.id, etol=False,
            ).action_assign_truck()
        self.assertFalse(self.line.vehicle_id)
        changed = date(2026, 12, 12)
        self._wizard(
            dispatcher, vehicle_id=self.truck.id, etol=changed,
        ).action_assign_truck()
        self.line.invalidate_recordset(["etol"])
        self.assertEqual(self.line.etol, changed)
        self.assertEqual(self.line.transport_file_line_ids.etol, changed)
        self.assertEqual(self.line.vehicle_id, self.truck)
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line.id,
        })
        self.assertEqual(trip.etol, changed)
        self.assertEqual(trip.current_assignment_id.vehicle_id, self.truck)

    def test_allocated_truck_conflict_blocks_trip_instead_of_losing_resources(self):
        other = self.env["sale.order"].create({"partner_id": self.customer.id})
        other_line = self.env["sale.order.line"].create({
            "order_id": other.id,
            "product_id": self.product.id,
            "name": "Existing allocated trip",
            "product_uom_qty": 1,
            "price_unit": 100,
            "vehicle_id": self.truck.id,
        })
        other.action_confirm()
        occupied = self.env["taj.transport.trip"].create({
            "sale_line_id": other_line.id,
        })
        self.assertEqual(occupied.current_vehicle_id, self.truck)
        self._wizard(vehicle_id=self.truck.id).action_assign_truck()
        with self.assertRaisesRegex(UserError, "already assigned"), self.env.cr.savepoint():
            self.env["taj.transport.trip"].create({"sale_line_id": self.line.id})
        self.assertFalse(self.line.transport_trip_ids)
        self.assertEqual(self.line.vehicle_id, self.truck)

    def test_allocation_shows_other_open_sales_order_warning(self):
        other = self.env["sale.order"].create({"partner_id": self.customer.id})
        self.env["sale.order.line"].create({
            "order_id": other.id,
            "product_id": self.product.id,
            "name": "Other draft Sales shipment",
            "product_uom_qty": 1,
            "price_unit": 100,
            "vehicle_id": self.truck.id,
        })
        wizard = self.env["taj.transport.assign.truck.wizard"].new({
            "sale_line_id": self.line.id,
            "vehicle_id": self.truck.id,
        })
        self.assertIn("other Sales Order", wizard.sale_order_conflict_note)
        self.assertFalse(wizard.in_progress_trip_note)

    def test_allocation_warns_about_in_progress_trip(self):
        other = self.env["sale.order"].create({"partner_id": self.customer.id})
        other_line = self.env["sale.order.line"].create({
            "order_id": other.id,
            "product_id": self.product.id,
            "name": "Busy truck shipment",
            "product_uom_qty": 1,
            "price_unit": 100,
            "vehicle_id": self.truck.id,
        })
        other.action_confirm()
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": other_line.id,
        })
        self.assertEqual(trip.current_assignment_id.vehicle_id, self.truck)
        # This test checks only the warning query. Set up the lifecycle
        # status directly without requiring unrelated milestone fixtures.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE taj_transport_trip SET state = %s WHERE id = %s",
            ("in_progress", trip.id),
        )
        trip.invalidate_recordset(["state"])
        wizard = self.env["taj.transport.assign.truck.wizard"].new({
            "sale_line_id": self.line.id,
            "vehicle_id": self.truck.id,
        })
        self.assertIn("In Progress", wizard.in_progress_trip_note)

    def test_allocation_form_shows_etol_and_warning_fields(self):
        root = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_assign_truck_wizard_form"
            ).get_combined_arch()
        )
        self.assertEqual(
            root.xpath("//field[@name='etol']")[0].get("required"), "1"
        )
        self.assertTrue(root.xpath("//field[@name='sale_order_conflict_note']"))
        self.assertTrue(root.xpath("//field[@name='in_progress_trip_note']"))

    def test_assign_truck_writes_the_line_without_a_trip(self):
        action = self.line.action_open_assign_truck_wizard()
        self.assertEqual(action["res_model"], "taj.transport.assign.truck.wizard")
        self.assertEqual(
            action["context"]["default_sale_line_id"],
            self.line.id,
        )
        result = self._wizard(vehicle_id=self.truck.id).action_assign_truck()
        self.assertEqual(result["type"], "ir.actions.act_window_close")
        self.assertEqual(self.line.vehicle_id, self.truck)
        self.assertFalse(self.line.transport_trip_ids)
        self.assertTrue(self.order.locked)
        action = self.env.ref(
            "taj_transport_operations.action_sale_order_line_without_truck"
        )
        self.assertNotIn(
            self.line,
            self.env["sale.order.line"].search(safe_eval(action.domain)),
        )

    def test_dispatcher_can_assign_a_truck(self):
        dispatcher = new_test_user(
            self.env,
            login="allocation_dispatcher",
            company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )
        self._wizard(dispatcher, vehicle_id=self.truck.id).action_assign_truck()
        self.assertEqual(self.line.vehicle_id, self.truck)
        self.assertFalse(self.line.transport_trip_ids)

    def test_assign_truck_rejects_a_non_truck(self):
        trailer_model = self.env["fleet.vehicle.model"].create(
            {
                "name": "Allocation trailer model",
                "brand_id": self.brand.id,
                "category_id": self.trailer_category.id,
            }
        )
        trailer = self.env["fleet.vehicle"].create(
            {
                "model_id": trailer_model.id,
                "license_plate": "ALLOC-TRAILER",
            }
        )
        with self.assertRaises(UserError):
            self._wizard(vehicle_id=trailer.id).action_assign_truck()
        self.assertFalse(self.line.vehicle_id)

    def test_assign_truck_rejects_another_company_truck(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Allocation other company",
            }
        )
        other_truck = self.env["fleet.vehicle"].create(
            {
                "model_id": self.truck_model.id,
                "license_plate": "ALLOC-OTHER",
                "company_id": other_company.id,
            }
        )
        with self.assertRaises(UserError):
            self._wizard(vehicle_id=other_truck.id).action_assign_truck()
        self.assertFalse(self.line.vehicle_id)

    def test_etol_is_available_on_sales_and_file_shipment(self):
        self.line.etol = date(2026, 12, 1)
        row = self.line.transport_file_line_ids
        self.assertEqual(row.etol, date(2026, 12, 1))
        self.assertEqual(self.order.trip_file_ids.etols, "2026-12-01")

    def test_shipment_data_entry_can_change_etol(self):
        actor = new_test_user(
            self.env,
            login="allocation_shipment_entry",
            company_id=self.env.company.id,
            groups=(
                "base.group_user,taj_transport_operations.group_shipment_data_entry"
            ),
        )
        row = self.line.transport_file_line_ids.with_user(actor)
        row.write({"etol": date(2026, 12, 2)})
        self.line.invalidate_recordset(["etol"])
        self.assertEqual(self.line.etol, date(2026, 12, 2))

    def test_dispatcher_can_edit_etol_without_allocation(self):
        dispatcher = new_test_user(self.env, login="etol_inline_dispatcher",
            company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user")
        line = self.line.with_user(dispatcher)
        line.write({"etol": "2026-12-15"})
        self.assertEqual(line.etol, date(2026, 12, 15))
        self.assertFalse(line.vehicle_id or line.transport_trip_ids or line.allocation_date)
        self.assertTrue(self.line.transport_file_id.message_ids.filtered(lambda m: "ETOL" in (m.body or "")))
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            line.write({"etol": "2026-12-16", "price_unit": 1})
        line.write({"etol": False})
        self.assertFalse(line.etol)
        self._wizard(dispatcher, vehicle_id=self.truck.id, etol="2026-12-20").action_assign_truck()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            line.write({"etol": "2026-12-21"})

    def test_inline_etol_rejects_unauthorized_cancelled_and_foreign_company(self):
        dispatcher = new_test_user(self.env, login="etol_security_dispatcher",
            company_id=self.env.company.id,
            groups="base.group_user,taj_transport_operations.group_transport_user")
        salesperson = new_test_user(self.env, login="etol_security_sales",
            company_id=self.env.company.id,
            groups="base.group_user,sales_team.group_sale_salesman_all_leads")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.line.with_user(salesperson).with_context(_file_shipment_write=True).write({'etol': '2026-12-25'})
        company = self.env['res.company'].create({'name': 'Foreign shipment company'})
        foreign_order = self.env['sale.order'].with_company(company).create({
            'partner_id': self.customer.id, 'company_id': company.id})
        foreign_line = self.env['sale.order.line'].with_company(company).create({
            'order_id': foreign_order.id, 'product_id': self.product.id, 'price_unit': 100})
        foreign_order.action_confirm()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            foreign_line.with_user(dispatcher).write({'etol': '2026-12-25'})
        self.order.action_unlock()
        self.order.action_cancel()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.line.with_user(dispatcher).write({'etol': '2026-12-25'})
