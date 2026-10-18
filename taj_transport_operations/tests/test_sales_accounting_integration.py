"""Sales, purchase and Accounting integration for Phase 2 transport trips.

These are real Odoo ORM tests, not mocked _prepare_invoice_line assertions.
Run on an isolated Odoo.sh development/staging test database only.
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestTransportSalesAccountingBoundary(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Odoo 19's accounting test common (BaseCommon) switches the test
        # environment to an independent internal user, which has neither
        # Sales nor Fleet rights. This case covers the Sales/Accounting
        # integration boundary, not permissions, so it keeps the environment
        # used by the other Phase 2 cases.
        cls.env = cls.env(su=True)
        cls.customer = cls.env["res.partner"].create({
            "name": "Transport integration customer",
        })
        cls.driver = cls.env["res.partner"].create({
            "name": "Transport integration primary driver",
        })
        cls.payment_term = cls.env["account.payment.term"].create({
            "name": "Transport integration 30 days",
            "company_id": cls.env.company.id,
            "line_ids": [Command.create({
                "value": "percent", "value_amount": 100.0, "nb_days": 30,
            })],
        })
        cls.brand = cls.env["fleet.vehicle.model.brand"].create({
            "name": "Transport integration brand",
        })
        cls.truck_model = cls.env["fleet.vehicle.model"].create({
            "name": "Integration test Truck",
            "brand_id": cls.brand.id,
            "category_id": cls.env.ref(
                "taj_transport_operations.fleet_category_truck"
            ).id,
        })
        cls.truck = cls.env["fleet.vehicle"].create({
            "model_id": cls.truck_model.id,
            "license_plate": "INTEGRATION-TRUCK-A",
        })
        cls.replacement_truck = cls.env["fleet.vehicle"].create({
            "model_id": cls.truck_model.id,
            "license_plate": "INTEGRATION-TRUCK-B",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Integration Freight Service",
            "type": "service",
            "invoice_policy": "order",
            "list_price": 1250.0,
            "property_account_income_id": cls.company_data[
                "default_account_revenue"
            ].id,
            "taxes_id": [Command.clear()],
        })

    def _sale(self, second_line=False):
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "payment_term_id": self.payment_term.id,
            "client_order_ref": "TRIP-INTEGRATION-REF",
            "origin": "TRIP-INTEGRATION-SOURCE",
            "order_line": [
                Command.create({
                    "product_id": self.product.id,
                    "name": "TAJ transport invoice boundary",
                    "product_uom_qty": 2.0,
                    "price_unit": 1250.0,
                    "vehicle_id": self.truck.id,
                    "container_num": "INTEGRATION-CONTAINER-A",
                    "srn": "INTEGRATION-SRN-A",
                    "consignee": "Integration consignee",
                    "weight": "2000 KG",
                    "size": "40 FT",
                    "file_name": "INTEGRATION-FILE",
                }),
                *([Command.create({
                    "product_id": self.product.id,
                    "name": "Second transport line",
                    "product_uom_qty": 1.0,
                    "price_unit": 450.0,
                    "vehicle_id": self.replacement_truck.id,
                    "container_num": "INTEGRATION-CONTAINER-B",
                    "srn": "INTEGRATION-SRN-B",
                })] if second_line else []),
            ],
        })
        order.action_confirm()
        lines = order.order_line.filtered(lambda line: not line.display_type)
        return order, lines

    def _trip(self, line):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": line.id,
            "origin_location": "Dar es Salaam",
            "destination_location": "Lusaka",
            "planned_departure_at": fields.Datetime.now() - timedelta(hours=2),
            "planned_arrival_at": fields.Datetime.now() + timedelta(days=1),
        })
        self.env["taj.transport.assignment"].create({
            "trip_id": trip.id,
            "vehicle_id": self.truck.id,
            "driver_id": self.driver.id,
            "effective_from": fields.Datetime.now() - timedelta(hours=4),
        })
        return trip

    def _replace_truck(self, trip):
        action = trip.action_open_replace_assignment_wizard()
        return self.env[
            "taj.transport.replace.assignment.wizard"
        ].with_context(**action["context"]).create({
            "trip_id": trip.id,
            "expected_assignment_id": trip.current_assignment_id.id,
            "vehicle_id": self.replacement_truck.id,
            "effective_from": fields.Datetime.now() - timedelta(hours=1),
            "reason": "Replacement truck for operations",
        }).action_replace_resources()

    def _finish(self, trip):
        trip.action_mark_ready()
        trip.write({
            # Must be later than the most recent replacement (now - 1h).
            "actual_departure_at": fields.Datetime.now() - timedelta(minutes=45),
        })
        trip.action_start_trip()
        trip.action_complete_trip()
        self.assertEqual(trip.state, "completed")
        self.assertFalse(trip.current_assignment_id)

    def _invoice(self, order):
        invoices = order._create_invoices()
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.move_type, "out_invoice")
        self.assertEqual(invoices.state, "draft")
        return invoices

    def _assert_original_sales_invoice_line(self, invoice_line, sale_line):
        self.assertEqual(invoice_line.vehicle_id, sale_line.vehicle_id)
        self.assertEqual(invoice_line.route_id, sale_line)
        self.assertEqual(invoice_line.order_id, sale_line.order_id)
        self.assertEqual(invoice_line.container_num, sale_line.container_num)
        self.assertEqual(invoice_line.srn, sale_line.srn)
        self.assertEqual(invoice_line.file_name, sale_line.file_name)
        self.assertEqual(invoice_line.consignee, sale_line.consignee)
        self.assertEqual(invoice_line.size, sale_line.size)
        self.assertEqual(invoice_line.weight, sale_line.weight)
        self.assertEqual(invoice_line.price_unit, sale_line.price_unit)
        self.assertEqual(invoice_line.quantity, sale_line.product_uom_qty)

    def test_existing_customer_invoice_stays_unchanged_through_trip_lifecycle(self):
        order, lines = self._sale()
        invoice = self._invoice(order)
        invoice_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(len(invoice_line), 1)
        before = (
            invoice.amount_total,
            invoice.amount_tax,
            invoice.invoice_payment_term_id.id,
            invoice_line.vehicle_id.id,
            invoice_line.route_id.id,
            invoice_line.price_unit,
            invoice_line.quantity,
            invoice_line.container_num,
            invoice_line.srn,
            tuple(invoice_line.tax_ids.ids),
            order.amount_total,
            order.payment_term_id.id,
        )
        trip = self._trip(lines)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self._replace_truck(trip)
        self._finish(trip)
        invoice.invalidate_recordset()
        invoice_line.invalidate_recordset()
        after = (
            invoice.amount_total,
            invoice.amount_tax,
            invoice.invoice_payment_term_id.id,
            invoice_line.vehicle_id.id,
            invoice_line.route_id.id,
            invoice_line.price_unit,
            invoice_line.quantity,
            invoice_line.container_num,
            invoice_line.srn,
            tuple(invoice_line.tax_ids.ids),
            order.amount_total,
            order.payment_term_id.id,
        )
        self.assertEqual(after, before)
        self._assert_original_sales_invoice_line(invoice_line, lines)
        self.assertEqual(invoice.invoice_payment_term_id, self.payment_term)
        self.assertEqual(trip.last_vehicle_id, self.replacement_truck)
        self.assertEqual(trip.initial_vehicle_id, self.truck)
        self.assertEqual(lines.vehicle_id, self.truck)

    def test_invoicing_after_trip_completion_uses_original_sales_line(self):
        order, lines = self._sale()
        before_sales_totals = (order.amount_total, order.amount_tax)
        trip = self._trip(lines)
        self._replace_truck(trip)
        self._finish(trip)
        self.assertEqual(trip.last_vehicle_id, self.replacement_truck)
        invoice = self._invoice(order)
        invoice_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(len(invoice_line), 1)
        self._assert_original_sales_invoice_line(invoice_line, lines)
        self.assertEqual(invoice_line.vehicle_id, self.truck)
        self.assertEqual(invoice.invoice_payment_term_id, self.payment_term)
        self.assertEqual(invoice.invoice_origin, order.name)
        self.assertEqual(
            (order.amount_total, order.amount_tax), before_sales_totals
        )

    def test_cancelled_trip_does_not_cancel_sales_or_customer_invoice(self):
        order, lines = self._sale()
        invoice = self._invoice(order)
        original_amount = invoice.amount_total
        trip = self._trip(lines)
        trip.action_mark_ready()
        wizard = self.env["taj.transport.cancel.trip.wizard"].create({
            "trip_id": trip.id,
            "reason": "Operations cancelled; Sales/Accounting unaffected",
        })
        wizard.action_confirm_cancellation()
        self.assertEqual(trip.state, "cancelled")
        self.assertEqual(order.state, "sale")
        self.assertEqual(invoice.state, "draft")
        self.assertEqual(invoice.amount_total, original_amount)
        self.assertEqual(invoice.invoice_payment_term_id, self.payment_term)
        self.assertEqual(trip.last_vehicle_id, self.truck)
        self.assertFalse(trip.current_assignment_id)
        self.assertEqual(lines.vehicle_id, self.truck)

    def test_multiple_sales_lines_produce_one_invoice_line_each(self):
        order, lines = self._sale(second_line=True)
        trips = self.env["taj.transport.trip"].create([
            {"sale_line_id": line.id} for line in lines
        ])
        self.assertEqual(len(trips), 2)
        self.assertEqual(order.transport_trip_count, 2)
        invoice = self._invoice(order)
        invoice_lines = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(len(invoice_lines), 2)
        for sale_line in lines:
            invoice_line = invoice_lines.filtered(
                lambda line: line.route_id == sale_line
            )
            self.assertEqual(len(invoice_line), 1)
            self.assertEqual(invoice_line.vehicle_id, sale_line.vehicle_id)
            self.assertEqual(invoice_line.container_num, sale_line.container_num)
        self.assertEqual(order.amount_total, 2950.0)
        self.assertEqual(invoice.amount_total, order.amount_total)

    def test_explicit_vat_invoice_remains_unchanged_by_resource_swap(self):
        order, lines = self._sale()
        tax_country = order._get_vat_tax_country()
        tax_domain = [
            ("company_id", "=", self.env.company.id),
            ("active", "=", True),
            ("type_tax_use", "=", "sale"),
            ("amount_type", "=", "percent"),
            ("price_include", "=", False),
            ("amount", ">", 0),
            ("amount", "<=", 100),
        ]
        if tax_country:
            tax_domain.append(("country_id", "=", tax_country.id))
        tax = self.env["account.tax"].search(tax_domain, limit=1)
        if not tax:
            self.skipTest("No eligible configured Sales VAT tax on this test company.")
        order.write({"vat_tax_id": tax.id})
        self.assertTrue(order.vat_percentage_active)
        self.assertEqual(lines.tax_ids, tax)
        invoice = self._invoice(order)
        self.assertTrue(invoice.is_vat_percentage_invoice)
        self.assertEqual(invoice.vat_percentage, tax.amount)
        invoice_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        totals = (
            order.amount_total,
            order.amount_tax,
            invoice.amount_total,
            invoice.amount_tax,
            invoice.vat_percentage,
            invoice.is_vat_percentage_invoice,
            tuple(invoice_line.tax_ids.ids),
        )
        trip = self._trip(lines)
        self._replace_truck(trip)
        self._finish(trip)
        self.assertEqual((
            order.amount_total,
            order.amount_tax,
            invoice.amount_total,
            invoice.amount_tax,
            invoice.vat_percentage,
            invoice.is_vat_percentage_invoice,
            tuple(invoice_line.tax_ids.ids),
        ), totals)
        self.assertEqual(invoice_line.vehicle_id, self.truck)
        self.assertEqual(trip.last_vehicle_id, self.replacement_truck)
        # The existing VAT rate is locked after invoicing. Trip operations
        # must not bypass the Sales/Accounting protection.
        different_valid_rate = tax.amount - 1 if tax.amount >= 100 else tax.amount + 1
        with self.assertRaises(UserError):
            order.write({"vat_percentage": different_valid_rate})
        self.assertEqual(invoice.vat_percentage, tax.amount)
