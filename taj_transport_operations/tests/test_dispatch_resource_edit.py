"""Trailer allocation snapshots and Edit Info for unstarted shipments."""

from datetime import date, timedelta
from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import new_test_user

from .phase3_case import TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestAssignTruckTrailerDefaults(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Trailer alloc customer"})
        cls.brand = cls.env["fleet.vehicle.model.brand"].create({"name": "Trailer alloc brand"})
        cls.truck_category = cls.env.ref("taj_transport_operations.fleet_category_truck")
        cls.trailer_category = cls.env.ref("taj_transport_operations.fleet_category_trailer")
        cls.truck_model = cls.env["fleet.vehicle.model"].create({
            "name": "Trailer alloc truck", "brand_id": cls.brand.id,
            "category_id": cls.truck_category.id,
        })
        cls.trailer_model = cls.env["fleet.vehicle.model"].create({
            "name": "Trailer alloc trailer", "brand_id": cls.brand.id,
            "category_id": cls.trailer_category.id,
        })
        cls.truck = cls.env["fleet.vehicle"].create({
            "model_id": cls.truck_model.id, "license_plate": "TRAILER-TRUCK-01",
        })
        cls.trailer = cls.env["fleet.vehicle"].create({
            "model_id": cls.trailer_model.id, "license_plate": "TRAILER-01",
        })
        cls.other_trailer = cls.env["fleet.vehicle"].create({
            "model_id": cls.trailer_model.id, "license_plate": "TRAILER-02",
        })
        cls.driver = cls.env["res.partner"].create({"name": "Trailer alloc driver"})
        cls.truck.driver_id = cls.driver
        cls.truck.transport_default_trailer_id = cls.trailer
        cls.product = cls.env["product.product"].create({
            "name": "Trailer alloc freight", "type": "service", "list_price": 100,
        })
        cls.order = cls.env["sale.order"].create({"partner_id": cls.customer.id})
        cls.line = cls.env["sale.order.line"].create({
            "order_id": cls.order.id, "product_id": cls.product.id,
            "name": "Freight", "product_uom_qty": 1, "price_unit": 100,
            "etol": "2026-11-10",
        })
        cls.order.action_confirm()
        cls.dispatcher = new_test_user(
            cls.env, login="trailer_alloc_dispatcher",
            groups="base.group_user,taj_transport_operations.group_transport_user",
        )

    def _wizard(self, user=None, **values):
        Wizard = self.env["taj.transport.assign.truck.wizard"]
        if user:
            Wizard = Wizard.with_user(user)
        return Wizard.create({
            "sale_line_id": self.line.id,
            "etol": self.line.etol,
            **values,
        })

    def test_trailer_defaults_from_fleet_and_onchange_refresh(self):
        wizard = self.env["taj.transport.assign.truck.wizard"].new({
            "sale_line_id": self.line.id,
            "vehicle_id": self.truck.id,
        })
        self.assertEqual(wizard.trailer_id, self.trailer)
        self.assertEqual(wizard.driver_id, self.driver)
        wizard.trailer_id = False
        self.assertFalse(wizard.trailer_id)
        wizard.vehicle_id = False
        wizard.vehicle_id = self.truck
        self.assertEqual(wizard.trailer_id, self.trailer)

    def test_assign_saves_trailer_snapshot_and_updates_fleet_pairing(self):
        self._wizard(
            self.dispatcher, vehicle_id=self.truck.id, trailer_id=self.other_trailer.id,
        ).action_assign_truck()
        self.assertEqual(self.line.vehicle_id, self.truck)
        self.assertEqual(self.line.transport_allocated_trailer_id, self.other_trailer)
        self.assertTrue(self.line.transport_trailer_choice_set)
        self.assertEqual(self.truck.transport_default_trailer_id, self.other_trailer)
        self.assertEqual(self.other_trailer.transport_assigned_truck_id, self.truck)
        self.assertFalse(self.trailer.transport_assigned_truck_id)

    def test_explicit_empty_trailer_clears_fleet_default_and_trip_snapshot(self):
        self._wizard(
            self.dispatcher, vehicle_id=self.truck.id, trailer_id=False, driver_id=False,
        ).action_assign_truck()
        self.assertTrue(self.line.transport_trailer_choice_set)
        self.assertFalse(self.line.transport_allocated_trailer_id)
        self.assertFalse(self.truck.transport_default_trailer_id)
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line.id})
        self.assertEqual(trip.current_assignment_id.vehicle_id, self.truck)
        self.assertFalse(trip.current_assignment_id.trailer_id)

    def test_cancel_and_onchange_do_not_mutate_fleet(self):
        self.assertEqual(self.truck.transport_default_trailer_id, self.trailer)
        wizard = self._wizard(vehicle_id=self.truck.id, trailer_id=self.other_trailer.id)
        self.assertEqual(wizard.trailer_id, self.other_trailer)
        wizard.unlink()
        self.assertEqual(self.truck.transport_default_trailer_id, self.trailer)
        self.assertFalse(self.line.vehicle_id)

    def test_paired_trailer_is_stolen_for_dialog_choice(self):
        other_truck = self.env["fleet.vehicle"].create({
            "model_id": self.truck_model.id, "license_plate": "TRAILER-TRUCK-02",
        })
        other_truck.transport_default_trailer_id = self.other_trailer
        self._wizard(
            self.dispatcher,
            vehicle_id=self.truck.id, trailer_id=self.other_trailer.id,
        ).action_assign_truck()
        self.assertEqual(self.line.vehicle_id, self.truck)
        self.assertEqual(self.truck.transport_default_trailer_id, self.other_trailer)
        self.assertFalse(other_truck.transport_default_trailer_id)
        self.assertEqual(self.other_trailer.transport_assigned_truck_id, self.truck)

    def test_assign_rejects_foreign_trailer_before_allocation(self):
        foreign = self.env["res.company"].create({"name": "Trailer roll company"})
        foreign_trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id, "license_plate": "TRAILER-ROLL-FOREIGN",
            "company_id": foreign.id,
        })
        original_etol = self.line.etol
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard(
                self.dispatcher,
                vehicle_id=self.truck.id,
                trailer_id=foreign_trailer.id,
                etol=date(2026, 12, 25),
            ).action_assign_truck()
        self.line.invalidate_recordset(["etol", "vehicle_id"])
        self.assertEqual(self.line.etol, original_etol)
        self.assertFalse(self.line.vehicle_id)
        self.assertEqual(self.truck.transport_default_trailer_id, self.trailer)

    def test_foreign_company_and_unauthorized_trailer_paths(self):
        foreign = self.env["res.company"].create({"name": "Trailer foreign company"})
        foreign_trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id, "license_plate": "FOREIGN-TR",
            "company_id": foreign.id,
        })
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._wizard(
                vehicle_id=self.truck.id, trailer_id=foreign_trailer.id,
            ).action_assign_truck()
        salesperson = new_test_user(
            self.env, login="trailer_alloc_sales",
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._wizard(
                salesperson, vehicle_id=self.truck.id, trailer_id=self.trailer.id,
            ).action_assign_truck()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.truck.with_user(self.dispatcher).write({
                "transport_default_trailer_id": self.other_trailer.id,
            })

    def test_deferred_trip_keeps_saved_trailer_after_fleet_default_changes(self):
        self._wizard(
            vehicle_id=self.truck.id, trailer_id=self.trailer.id,
        ).action_assign_truck()
        self.truck.transport_default_trailer_id = self.other_trailer
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line.id})
        self.assertEqual(trip.current_assignment_id.trailer_id, self.trailer)

    def test_existing_draft_trip_gets_selected_trailer(self):
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line.id})
        self.assertFalse(trip.assignment_ids)
        self._wizard(
            vehicle_id=self.truck.id, trailer_id=self.other_trailer.id,
        ).action_assign_truck()
        self.assertEqual(trip.current_assignment_id.trailer_id, self.other_trailer)
        self.assertEqual(self.truck.transport_default_trailer_id, self.other_trailer)

    def test_legacy_line_without_trailer_choice_creates_trip_without_trailer(self):
        self.line.sudo().write({"vehicle_id": self.truck.id})
        self.assertFalse(self.line.transport_trailer_choice_set)
        trip = self.env["taj.transport.trip"].create({"sale_line_id": self.line.id})
        self.assertFalse(trip.current_assignment_id.trailer_id)

    def test_vehicle_availability_wizard_passes_trailer(self):
        self.truck.transport_default_trailer_id = self.trailer
        wizard = self.env["taj.transport.vehicle.sale.allocation.wizard"].with_user(
            self.dispatcher
        ).create({
            "vehicle_id": self.truck.id,
            "sale_order_id": self.order.id,
            "sale_line_id": self.line.id,
            "driver_id": self.driver.id,
            "trailer_id": self.other_trailer.id,
            "etol": self.line.etol,
        })
        wizard.action_allocate()
        self.assertEqual(self.line.transport_allocated_trailer_id, self.other_trailer)
        self.assertEqual(self.truck.transport_default_trailer_id, self.other_trailer)

    def test_assign_truck_form_exposes_trailer(self):
        root = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_assign_truck_wizard_form"
            ).get_combined_arch()
        )
        self.assertEqual(len(root.xpath("//field[@name='trailer_id']")), 1)


@tagged("post_install", "-at_install")
class TestEditShipmentInfo(TajTransportPhase3Case):
    user_tag = "edit_info"

    def _truck(self, plate, driver=None, trailer=None):
        truck = self.env["fleet.vehicle"].create({
            "model_id": self.truck_model.id, "license_plate": plate,
        })
        if driver is not None:
            truck.driver_id = driver
        if trailer is not None:
            truck.transport_default_trailer_id = trailer
        return truck

    def _allocate(self, line, truck=None, trailer=None, driver=None, etol="2026-11-10"):
        truck = truck or self.truck
        values = {
            "sale_line_id": line.id,
            "vehicle_id": truck.id,
            "driver_id": (driver or self.driver).id,
            "etol": etol,
        }
        if trailer is not None:
            values["trailer_id"] = trailer.id if trailer else False
        elif truck.transport_default_trailer_id:
            values["trailer_id"] = truck.transport_default_trailer_id.id
        self.env["taj.transport.assign.truck.wizard"].with_user(
            self.dispatcher
        ).create(values).action_assign_truck()
        line.invalidate_recordset(["etol", "vehicle_id"])
        return line

    def _edit_wizard(self, line, user=None, **overrides):
        action = line.with_user(user or self.dispatcher).action_open_edit_info()
        Wizard = self.env["taj.transport.edit.shipment.resources.wizard"].with_user(
            user or self.dispatcher
        ).with_context(action["context"])
        defaults = Wizard.default_get(list(Wizard._fields))
        defaults.update(overrides)
        return Wizard.create(defaults)

    def _assignment_started_earlier(self, trip, hours=1):
        """Fixture helper: open assignment began earlier so Replace accepts now()."""
        assignment = trip.current_assignment_id
        if not assignment:
            return
        self.env.cr.execute(
            "UPDATE taj_transport_assignment SET effective_from = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=hours), assignment.id),
        )
        assignment.invalidate_recordset(["effective_from"])

    def _trailer(self, plate):
        model = self.env["fleet.vehicle.model"].create({
            "name": plate, "brand_id": self.brand.id,
            "category_id": self.env.ref("taj_transport_operations.fleet_category_trailer").id,
        })
        return self.env["fleet.vehicle"].create({"model_id": model.id, "license_plate": plate})

    def test_edit_info_entrypoints_and_single_selection_guard(self):
        line = self._sale_line("EDIT-ENTRY", vehicle_id=False)
        self._allocate(line)
        listing = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_sale_order_line_dispatch_list"
            ).get_combined_arch()
        )
        self.assertEqual(
            listing.xpath("./button[@name='action_open_edit_info']")[0].get("string"),
            "Edit Info",
        )
        self.assertEqual(
            listing.xpath("./header/button[@name='action_edit_selected_info']")[0].get(
                "string"
            ),
            "Edit Info",
        )
        form = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_sale_order_line_dispatch_form"
            ).get_combined_arch()
        )
        self.assertEqual(
            form.xpath("//header/button[@name='action_open_edit_info']")[0].get("string"),
            "Edit Info",
        )
        other = self._sale_line("EDIT-MULTI", vehicle_id=False)
        with self.assertRaisesRegex(UserError, "exactly one"), self.env.cr.savepoint():
            (line | other).with_user(self.dispatcher).action_edit_selected_info()

    def test_edit_info_no_trip_updates_resources_etol_and_fleet(self):
        line = self._sale_line("EDIT-NOTRIP", vehicle_id=False)
        trailer = self._trailer("EDIT-TR-1")
        self.truck.transport_default_trailer_id = trailer
        self._allocate(line, trailer=trailer)
        allocation_date = line.allocation_date
        price = line.price_unit
        replacement = self._trailer("EDIT-TR-2")
        other_driver = self.env["res.partner"].create({"name": "Edit info driver"})
        wizard = self._edit_wizard(
            line, trailer_id=replacement.id, driver_id=other_driver.id,
            etol=date(2026, 12, 1),
        )
        wizard.action_save()
        self.assertEqual(line.vehicle_id, self.truck)
        self.assertEqual(line.transport_allocated_trailer_id, replacement)
        self.assertEqual(line.transport_allocated_driver_id, other_driver)
        self.assertEqual(line.etol, date(2026, 12, 1))
        self.assertEqual(line.allocation_date, allocation_date)
        self.assertEqual(line.price_unit, price)
        self.assertEqual(self.truck.transport_default_trailer_id, replacement)
        self.assertFalse(line.transport_trip_ids)

    def test_edit_info_etol_only_skips_replacement_history(self):
        line = self._sale_line("EDIT-ETOL", vehicle_id=False)
        trailer = self._trailer("EDIT-ETOL-TR")
        self._allocate(line, trailer=trailer)
        trip = self._trip(line=line)
        before = trip.assignment_ids
        self.assertEqual(len(before), 1)
        wizard = self._edit_wizard(line, etol=date(2026, 12, 20))
        wizard.action_save()
        self.assertEqual(line.etol, date(2026, 12, 20))
        self.assertEqual(trip.assignment_ids, before)
        self.assertFalse(before.effective_to)

    def test_edit_info_etol_only_ignores_unrelated_fleet_pairing_change(self):
        line = self._sale_line("EDIT-ETOL-FLEET", vehicle_id=False)
        trailer = self._trailer("EDIT-ETOL-FLEET-TR")
        self._allocate(line, trailer=trailer)
        # Move Fleet pairing elsewhere while the shipment snapshot keeps trailer.
        self.truck.transport_default_trailer_id = False
        other = self._truck("EDIT-ETOL-FLEET-OTHER")
        other.transport_default_trailer_id = trailer
        wizard = self._edit_wizard(line, etol=date(2026, 12, 22))
        wizard.action_save()
        self.assertEqual(line.etol, date(2026, 12, 22))
        self.assertEqual(line.transport_allocated_trailer_id, trailer)
        self.assertEqual(other.transport_default_trailer_id, trailer)
        self.assertFalse(self.truck.transport_default_trailer_id)

    def test_edit_info_driver_only_preserves_later_fleet_pairing_and_trailer(self):
        truck = self._truck("EDIT-DRV-TRUCK")
        trailer_t1 = self._trailer("EDIT-DRV-T1")
        trailer_t2 = self._trailer("EDIT-DRV-T2")
        other = self._truck("EDIT-DRV-OTHER")
        line = self._sale_line("EDIT-DRV", vehicle_id=False)
        self._allocate(line, truck=truck, trailer=trailer_t1)
        trip = self._trip(line=line)
        self._assignment_started_earlier(trip)
        truck.transport_default_trailer_id = trailer_t2
        other.transport_default_trailer_id = trailer_t1
        new_driver = self.env["res.partner"].create({"name": "Driver-only edit"})
        before = trip.current_assignment_id
        self._edit_wizard(line, driver_id=new_driver.id).action_save()
        self.assertEqual(truck.transport_default_trailer_id, trailer_t2)
        self.assertEqual(other.transport_default_trailer_id, trailer_t1)
        self.assertEqual(line.transport_allocated_trailer_id, trailer_t1)
        self.assertEqual(line.transport_allocated_driver_id, new_driver)
        self.assertEqual(line.vehicle_id, truck)
        trip.invalidate_recordset()
        self.assertEqual(len(trip.assignment_ids), 2)
        self.assertTrue(before.effective_to)
        current = trip.current_assignment_id
        self.assertEqual(current.vehicle_id, truck)
        self.assertEqual(current.trailer_id, trailer_t1)
        self.assertEqual(current.driver_id, new_driver)
        self.assertFalse(current.effective_to)

    def test_edit_info_form_keeps_explicit_empty_trailer_after_fleet_default_set(self):
        truck = self._truck("EDIT-EMPTY-TRUCK")
        line = self._sale_line("EDIT-EMPTY", vehicle_id=False)
        self._allocate(line, truck=truck, trailer=False, driver=self.driver)
        self.assertTrue(line.transport_trailer_choice_set)
        self.assertFalse(line.transport_allocated_trailer_id)
        later = self._trailer("EDIT-EMPTY-LATER")
        truck.transport_default_trailer_id = later
        action = line.with_user(self.dispatcher).action_open_edit_info()
        with Form(
            self.env["taj.transport.edit.shipment.resources.wizard"].with_user(
                self.dispatcher
            ).with_context(action["context"])
        ) as form:
            self.assertEqual(form.vehicle_id, truck)
            self.assertFalse(form.trailer_id)
            self.assertEqual(form.driver_id, self.driver)
        self.assertEqual(truck.transport_default_trailer_id, later)
        self.assertFalse(line.transport_allocated_trailer_id)

    def test_edit_info_draft_with_assignment_uses_replace_and_keeps_codriver(self):
        line = self._sale_line("EDIT-REPLACE", vehicle_id=False)
        trailer = self._trailer("EDIT-REP-TR")
        self._allocate(line, trailer=trailer)
        trip = self._trip(line=line)
        co_driver = self.env["res.partner"].create({"name": "Edit co-driver"})
        current = trip.current_assignment_id
        self.env.cr.execute(
            "UPDATE taj_transport_assignment SET co_driver_id = %s WHERE id = %s",
            (co_driver.id, current.id),
        )
        current.invalidate_recordset(["co_driver_id"])
        self._assignment_started_earlier(trip)
        initial_vehicle = trip.initial_vehicle_id
        new_trailer = self._trailer("EDIT-REP-TR2")
        wizard = self._edit_wizard(line, trailer_id=new_trailer.id)
        wizard.action_save()
        trip.invalidate_recordset()
        self.assertEqual(len(trip.assignment_ids), 2)
        self.assertTrue(current.effective_to)
        self.assertEqual(trip.current_assignment_id.trailer_id, new_trailer)
        self.assertEqual(trip.current_assignment_id.co_driver_id, co_driver)
        self.assertEqual(trip.initial_vehicle_id, initial_vehicle)
        self.assertEqual(line.vehicle_id, self.truck)

    def test_edit_info_draft_without_assignment_creates_initial_resources(self):
        line = self._sale_line("EDIT-INIT", vehicle_id=False, etol="2026-11-10")
        trip = self._trip(line=line)
        self.assertFalse(trip.assignment_ids)
        trailer = self._trailer("EDIT-INIT-TR")
        truck = self._truck("EDIT-INIT-TRUCK", trailer=trailer)
        wizard = self._edit_wizard(
            line, vehicle_id=truck.id, trailer_id=trailer.id, driver_id=self.driver.id,
            etol=date(2026, 11, 10),
        )
        wizard.action_save()
        self.assertEqual(trip.current_assignment_id.vehicle_id, truck)
        self.assertEqual(trip.current_assignment_id.trailer_id, trailer)
        self.assertEqual(line.vehicle_id, truck)

    def test_edit_info_ready_trip_and_rejects_started_cancelled_stale(self):
        ready_truck = self._truck("EDIT-READY-TRUCK")
        line = self._sale_line("EDIT-READY", vehicle_id=False)
        trailer = self._trailer("EDIT-READY-TR")
        self._allocate(line, truck=ready_truck, trailer=trailer)
        trip = self._trip(line=line)
        self._plan(trip)
        trip.action_mark_ready()
        self._assignment_started_earlier(trip)
        new_trailer = self._trailer("EDIT-READY-TR2")
        self._edit_wizard(line, trailer_id=new_trailer.id).action_save()
        self.assertEqual(trip.current_assignment_id.trailer_id, new_trailer)

        started_truck = self._truck("EDIT-START-TRUCK")
        started = self._sale_line("EDIT-STARTED", vehicle_id=False)
        self._allocate(started, truck=started_truck, trailer=self._trailer("EDIT-START-TR"))
        started_trip = self._trip(line=started)
        self._plan(started_trip)
        self._assignment_started_earlier(started_trip)
        started_trip.action_mark_ready()
        started_trip.action_start_trip(
            actual_start_at=fields.Datetime.now() - timedelta(minutes=30)
        )
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._edit_wizard(started).action_save()

        cancel_truck = self._truck("EDIT-CANCEL-TRUCK")
        cancelled_order = self._sale_line("EDIT-CANCEL", vehicle_id=False)
        self._allocate(
            cancelled_order, truck=cancel_truck, trailer=self._trailer("EDIT-CANCEL-TR"),
        )
        cancelled_order.order_id.sudo().write({"locked": False, "state": "cancel"})
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._edit_wizard(cancelled_order).action_save()

        stale_truck = self._truck("EDIT-STALE-TRUCK")
        stale_line = self._sale_line("EDIT-STALE", vehicle_id=False)
        self._allocate(stale_line, truck=stale_truck, trailer=self._trailer("EDIT-STALE-TR"))
        stale = self._edit_wizard(stale_line)
        stale_line._write_file_shipment(
            {"etol": date(2026, 12, 31)}, stale_line.transport_file_line_ids
        )
        with self.assertRaisesRegex(UserError, "changed after"), self.env.cr.savepoint():
            stale.action_save()

    def test_edit_info_two_no_trip_dialogs_detect_stale_driver_and_trailer(self):
        line = self._sale_line("EDIT-RACE", vehicle_id=False)
        trailer = self._trailer("EDIT-RACE-TR")
        self._allocate(line, trailer=trailer)
        first = self._edit_wizard(line)
        second = self._edit_wizard(line)
        other_driver = self.env["res.partner"].create({"name": "Race driver"})
        other_trailer = self._trailer("EDIT-RACE-TR2")
        first.driver_id = other_driver
        first.action_save()
        self.assertEqual(line.transport_allocated_driver_id, other_driver)
        second.trailer_id = other_trailer
        with self.assertRaisesRegex(UserError, "changed after"), self.env.cr.savepoint():
            second.action_save()
        self.assertEqual(line.transport_allocated_trailer_id, trailer)

    def test_edit_info_allows_open_when_assignment_truck_differs_from_source(self):
        source_truck = self._truck("EDIT-SRC-TRUCK")
        line = self._sale_line("EDIT-SRC", vehicle_id=False)
        trailer = self._trailer("EDIT-SRC-TR")
        self._allocate(line, truck=source_truck, trailer=trailer)
        trip = self._trip(line=line)
        self._assignment_started_earlier(trip)
        replacement_truck = self._truck("EDIT-OPS-TRUCK", driver=self.driver)
        self.env["taj.transport.replace.assignment.wizard"].with_user(
            self.dispatcher
        ).create({
            "trip_id": trip.id,
            "expected_assignment_id": trip.current_assignment_id.id,
            "vehicle_id": replacement_truck.id,
            "trailer_id": trailer.id,
            "driver_id": self.driver.id,
            "effective_from": fields.Datetime.now(),
            "reason": "Swap operational truck",
        }).action_replace_resources()
        self.assertEqual(line.vehicle_id, source_truck)
        self.assertEqual(trip.current_assignment_id.vehicle_id, replacement_truck)
        wizard = self._edit_wizard(line)
        self.assertEqual(wizard.expected_source_vehicle_id, source_truck)
        self.assertEqual(wizard.expected_vehicle_id, replacement_truck)
        self.assertEqual(wizard.vehicle_id, replacement_truck)
        wizard.etol = date(2026, 12, 18)
        wizard.action_save()
        self.assertEqual(line.etol, date(2026, 12, 18))
        self.assertEqual(line.vehicle_id, source_truck)
        self.assertEqual(trip.current_assignment_id.vehicle_id, replacement_truck)

    def test_edit_info_form_truck_change_a_b_a_refreshes_defaults(self):
        trailer_a = self._trailer("EDIT-AB-A")
        trailer_b = self._trailer("EDIT-AB-B")
        driver_a = self.env["res.partner"].create({"name": "Driver A"})
        driver_b = self.env["res.partner"].create({"name": "Driver B"})
        truck_a = self._truck("EDIT-AB-TRUCK-A", driver=driver_a, trailer=trailer_a)
        truck_b = self._truck("EDIT-AB-TRUCK-B", driver=driver_b, trailer=trailer_b)
        line = self._sale_line("EDIT-AB", vehicle_id=False)
        # Operational prefill uses an explicit non-default trailer/driver.
        manual_trailer = self._trailer("EDIT-AB-MANUAL")
        manual_driver = self.env["res.partner"].create({"name": "Manual driver"})
        self._allocate(
            line, truck=truck_a, trailer=manual_trailer, driver=manual_driver,
        )
        # Allocate updates Fleet pairing; restore catalog defaults so A->B->A
        # asserts truck defaults, distinct from the operational open prefill.
        truck_a.transport_default_trailer_id = trailer_a
        action = line.with_user(self.dispatcher).action_open_edit_info()
        with Form(
            self.env["taj.transport.edit.shipment.resources.wizard"].with_user(
                self.dispatcher
            ).with_context(action["context"])
        ) as form:
            self.assertEqual(form.vehicle_id, truck_a)
            self.assertEqual(form.trailer_id, manual_trailer)
            self.assertEqual(form.driver_id, manual_driver)
            form.vehicle_id = truck_b
            self.assertEqual(form.trailer_id, trailer_b)
            self.assertEqual(form.driver_id, driver_b)
            form.trailer_id = self.env["fleet.vehicle"]
            self.assertFalse(form.trailer_id)
            form.vehicle_id = truck_a
            self.assertEqual(form.trailer_id, trailer_a)
            self.assertEqual(form.driver_id, driver_a)
        # Cancel/discard leaves Fleet and shipment unchanged.
        self.assertEqual(truck_a.transport_default_trailer_id, trailer_a)
        self.assertEqual(line.transport_allocated_trailer_id, manual_trailer)
        self.assertEqual(line.transport_allocated_driver_id, manual_driver)

    def test_edit_info_steals_paired_trailer_for_dialog_choice(self):
        line = self._sale_line("EDIT-ROLL", vehicle_id=False)
        trailer = self._trailer("EDIT-ROLL-TR")
        self._allocate(line, trailer=trailer)
        trip = self._trip(line=line)
        self._assignment_started_earlier(trip)
        paired = self._trailer("EDIT-ROLL-PAIR")
        other = self._truck("EDIT-ROLL-TRUCK2")
        other.transport_default_trailer_id = paired
        self._edit_wizard(line, trailer_id=paired.id).action_save()
        self.assertEqual(line.transport_allocated_trailer_id, paired)
        self.assertEqual(self.truck.transport_default_trailer_id, paired)
        self.assertFalse(other.transport_default_trailer_id)

    def test_edit_info_rollback_keeps_commercial_and_fleet_untouched_on_conflict(self):
        line = self._sale_line("EDIT-ROLL-FOREIGN", vehicle_id=False)
        trailer = self._trailer("EDIT-ROLL-FOREIGN-TR")
        self._allocate(line, trailer=trailer)
        foreign = self.env["res.company"].create({"name": "Edit roll foreign company"})
        foreign_trailer = self.env["fleet.vehicle"].create({
            "model_id": self.trailer_model.id,
            "license_plate": "EDIT-ROLL-FOREIGN-TR2",
            "company_id": foreign.id,
        })
        price = line.price_unit
        before_default = self.truck.transport_default_trailer_id
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._edit_wizard(line, trailer_id=foreign_trailer.id).action_save()
        self.assertEqual(line.price_unit, price)
        self.assertEqual(self.truck.transport_default_trailer_id, before_default)
        self.assertEqual(line.transport_allocated_trailer_id, trailer)

    def test_edit_info_form_round_trip(self):
        line = self._sale_line("EDIT-FORM", vehicle_id=False)
        trailer = self._trailer("EDIT-FORM-TR")
        self.truck.transport_default_trailer_id = trailer
        self._allocate(line, trailer=trailer)
        action = line.with_user(self.dispatcher).action_open_edit_info()
        with Form(
            self.env["taj.transport.edit.shipment.resources.wizard"].with_user(
                self.dispatcher
            ).with_context(action["context"])
        ) as form:
            self.assertEqual(form.vehicle_id, self.truck)
            self.assertEqual(form.trailer_id, trailer)
            form.etol = date(2026, 12, 15)
            wizard = form.save()
        wizard.action_save()
        self.assertEqual(line.etol, date(2026, 12, 15))
