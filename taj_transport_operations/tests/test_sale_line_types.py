"""Manage trip classifications and default clearing service from File Type."""

from odoo.exceptions import AccessError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestSaleLineTypes(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Line types customer"})
        cls.product = cls.env["product.product"].create({"name": "Line types service", "type": "service"})
        cls.order = cls.env["sale.order"].create({"partner_id": cls.customer.id})

    def _type(self, name, clearing):
        return self.env["taj.transport.file.type"].create({"name": name, "clearing_service": clearing})

    def _line(self, **values):
        return self.env["sale.order.line"].create({
            "order_id": self.order.id, "product_id": self.product.id, **values,
        })

    def test_trip_type_catalog_permissions_and_line(self):
        self.assertTrue("taj.transport.trip.type" in self.env.registry.models, "Trip Type catalog must exist")
        manager = new_test_user(self.env, login="line_type_manager", groups="taj_transport_operations.group_transport_manager")
        sales = new_test_user(self.env, login="line_type_sales", groups="sales_team.group_sale_salesman")
        trip_type = self.env["taj.transport.trip.type"].with_user(manager).create({"name": "Export trip"})
        line = self._line(trip_type_id=trip_type.id)
        self.assertEqual(line.with_user(sales).trip_type_id.name, "Export trip")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            trip_type.with_user(sales).write({"name": "Unauthorized"})
        trip_type.write({"active": False})
        self.assertEqual(line.trip_type_id, trip_type)
        self.assertNotIn(trip_type, self.env["taj.transport.trip.type"].search([]))

    def test_form_selection_autofills_but_allows_manual_override(self):
        included = self._type("Included form type", "included")
        excluded = self._type("Excluded form type", "not_included")
        with Form(self.order) as form:
            with form.order_line.new() as line:
                line.product_id = self.product
                line.file_type_id = included
                self.assertEqual(line.clearing_service, "included")
                line.file_type_id = excluded
                self.assertEqual(line.clearing_service, "not_included")
                line.clearing_service = "included"
        self.assertEqual(self.order.order_line.clearing_service, "included")

    def test_create_defaults_batch_context_and_explicit_values(self):
        included = self._type("Included create type", "included")
        excluded = self._type("Excluded create type", "not_included")
        base = {"order_id": self.order.id, "product_id": self.product.id}
        lines = self.env["sale.order.line"].create([
            dict(base, file_type_id=included.id), dict(base, file_type_id=excluded.id),
            dict(base, file_type_id=included.id, clearing_service="not_included"),
        ])
        self.assertEqual(lines.mapped("clearing_service"), ["included", "not_included", "not_included"])
        line = self.env["sale.order.line"].with_context(default_file_type_id=included.id).create(base)
        self.assertEqual(line.clearing_service, "included")
        line = self.env["sale.order.line"].with_context(
            default_file_type_id=included.id, default_clearing_service="not_included",
        ).create(base)
        self.assertEqual(line.clearing_service, "not_included")

    def test_route_suggested_trip_type_does_not_select_file_type_or_clearing(self):
        trip_type = self.env["taj.transport.trip.type"].create({"name": "Suggested trip type"})
        self._type(trip_type.name, "included")
        self.env["taj.transport.route"].create({
            "name": "Line types route", "product_tmpl_id": self.product.product_tmpl_id.id,
            "company_id": self.env.company.id, "trip_type_id": trip_type.id,
        })
        line = self._line()
        self.assertEqual(line.trip_type_id, trip_type)
        self.assertFalse(line.file_type_id)
        self.assertFalse(line.clearing_service)

    def test_write_selection_defaults_and_clearing_empty_mapping(self):
        included = self._type("Included write type", "included")
        excluded = self._type("Excluded write type", "not_included")
        unmapped = self._type("Unmapped write type", False)
        line = self._line(file_type_id=included.id)
        line.write({"file_type_id": excluded.id})
        self.assertEqual(line.clearing_service, "not_included")
        line.write({"file_type_id": included.id, "clearing_service": "not_included"})
        line.write({"price_unit": 125})
        self.assertEqual(line.clearing_service, "not_included")
        line.write({"file_type_id": unmapped.id})
        self.assertFalse(line.clearing_service)
        line.write({"file_type_id": included.id})
        line.write({"file_type_id": False})
        self.assertFalse(line.clearing_service)

    def test_catalog_change_does_not_rewrite_existing_lines(self):
        file_type = self._type("Historical clearing type", "included")
        line = self._line(file_type_id=file_type.id)
        file_type.clearing_service = "not_included"
        line.write({"price_unit": 150})
        self.assertEqual(line.clearing_service, "included")
        self.assertEqual(self._line(file_type_id=file_type.id).clearing_service, "not_included")
