"""Regression tests for product-linked routes and sales cargo snapshots."""
from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSalesRouteAndCargoDefaults(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Route cargo customer"})
        cls.product_a = cls.env["product.product"].create({
            "name": "City A - City B (40ft)",
            "type": "service",
            "list_price": 100,
        })
        cls.product_b = cls.env["product.product"].create({
            "name": "City C - City D (20ft)",
            "type": "service",
            "list_price": 120,
        })
        cls.cargo = cls.env.ref("taj_transport_operations.cargo_type_general")
        cls.fragile = cls.env.ref("taj_transport_operations.cargo_type_fragile")
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.customer.id,
        })
        cls.line_a = cls.env["sale.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.product_a.id,
            "product_uom_qty": 1,
            "price_unit": 100,
            "cargo_type_id": cls.cargo.id,
            "cargo_description": "Palletized machinery",
            "special_handling_instructions": "Keep upright",
        })
        cls.line_b = cls.env["sale.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.product_b.id,
            "product_uom_qty": 1,
            "price_unit": 120,
            "cargo_type_id": cls.fragile.id,
            "cargo_description": "Glass panels",
            "special_handling_instructions": "Do not stack",
        })
        cls.order.action_confirm()
        cls.route_a = cls.env["taj.transport.route"].create({
            "name": "Linked A",
            "company_id": cls.env.company.id,
            "product_tmpl_id": cls.product_a.product_tmpl_id.id,
            "origin_location": "Template Origin A",
            "destination_location": "Template Dest A",
        })
        cls.env["taj.transport.route.line"].create({
            "route_id": cls.route_a.id,
            "name": "A1",
            "sequence": 10,
        })
        cls.route_b1 = cls.env["taj.transport.route"].create({
            "name": "Linked B1",
            "company_id": cls.env.company.id,
            "product_tmpl_id": cls.product_b.product_tmpl_id.id,
        })
        cls.route_b2 = cls.env["taj.transport.route"].create({
            "name": "Linked B2",
            "company_id": cls.env.company.id,
            "product_tmpl_id": cls.product_b.product_tmpl_id.id,
        })

    def test_orm_create_without_template_still_valid_and_snapshots_cargo(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_a.id,
        })
        self.assertFalse(trip.route_id)
        self.assertEqual(trip.cargo_type_id, self.cargo)
        self.assertEqual(trip.cargo_type, self.cargo.name)
        self.assertEqual(trip.cargo_description, "Palletized machinery")
        self.assertEqual(trip.special_handling_instructions, "Keep upright")
        self.line_a.write({
            "cargo_description": "Boxed spare parts",
            "special_handling_instructions": "Changed later",
            "cargo_type_id": False,
        })
        self.assertEqual(trip.special_handling_instructions, "Keep upright")
        self.assertEqual(trip.cargo_type_id, self.cargo)
        self.assertEqual(trip.cargo_description, "Palletized machinery")
        later_trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_b.id,
        })
        self.assertEqual(later_trip.cargo_description, "Glass panels")
        self.assertEqual(later_trip.cargo_type_id, self.fragile)
        self.assertEqual(later_trip.special_handling_instructions, "Do not stack")

    def test_explicit_cargo_vals_win_over_sales_line_defaults(self):
        other = self.env.ref("taj_transport_operations.cargo_type_fragile")
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_a.id,
            "cargo_type_id": other.id,
            "cargo_description": "Trip-specific goods",
            "special_handling_instructions": "Caller wins",
        })
        self.assertEqual(trip.cargo_type_id, other)
        self.assertEqual(trip.cargo_description, "Trip-specific goods")
        self.assertEqual(trip.special_handling_instructions, "Caller wins")

    def test_explicit_blank_cargo_values_are_not_replaced(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_a.id,
            "cargo_description": False,
            "cargo_type_id": False,
            "special_handling_instructions": False,
        })
        self.assertFalse(trip.cargo_description)
        self.assertFalse(trip.cargo_type_id)
        self.assertFalse(trip.special_handling_instructions)

    def test_blank_line_cargo_does_not_inherit_from_another_line(self):
        self.line_b.write({
            "cargo_type_id": False,
            "cargo_description": False,
            "special_handling_instructions": False,
        })
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_b.id,
        })
        self.assertFalse(trip.cargo_type_id)
        self.assertFalse(trip.cargo_description)
        self.assertFalse(trip.special_handling_instructions)

    def test_same_product_lines_keep_separate_cargo(self):
        other_line = self.env["sale.order.line"].create({
            "order_id": self.order.id,
            "product_id": self.product_a.id,
            "product_uom_qty": 1,
            "price_unit": 100,
            "cargo_type_id": self.fragile.id,
            "cargo_description": "Glass panels",
            "special_handling_instructions": "Do not stack",
        })
        for line in self.line_a | other_line:
            line.action_open_or_create_transport_trip()
        self.assertEqual(self.line_a.transport_trip_ids.cargo_type_id, self.cargo)
        self.assertEqual(other_line.transport_trip_ids.cargo_type_id, self.fragile)
        self.assertEqual(
            other_line.transport_trip_ids.cargo_description, "Glass panels"
        )
        self.assertEqual(
            other_line.transport_trip_ids.special_handling_instructions,
            "Do not stack",
        )

    def test_archived_sales_cargo_blocks_batch_before_any_create(self):
        # An invalid later line must not leave the valid first trip behind.
        self.fragile.active = False
        with self.assertRaises(ValidationError):
            self.env["taj.transport.trip"].create([
                {"sale_line_id": self.line_a.id},
                {"sale_line_id": self.line_b.id},
            ])
        self.assertFalse(self.line_a.transport_trip_ids)
        self.assertFalse(self.line_b.transport_trip_ids)
        self.fragile.active = True

    def test_template_locations_win_on_create_blank_falls_back_to_product(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_a.id,
            "route_id": self.route_a.id,
            "origin_location": "Caller Origin",
            "destination_location": "Caller Dest",
        })
        self.assertEqual(trip.origin_location, "Template Origin A")
        self.assertEqual(trip.destination_location, "Template Dest A")

        blank = self.env["taj.transport.route"].create({
            "name": "Blank locations A2",
            "company_id": self.env.company.id,
            "product_tmpl_id": self.product_a.product_tmpl_id.id,
        })
        line = self.env["sale.order.line"].create({
            "order_id": self.order.id,
            "product_id": self.product_a.id,
            "product_uom_qty": 1,
            "price_unit": 100,
        })
        trip2 = self.env["taj.transport.trip"].create({
            "sale_line_id": line.id,
            "route_id": blank.id,
        })
        self.assertEqual(trip2.origin_location, "City A")
        self.assertEqual(trip2.destination_location, "City B")

    def test_draft_route_change_overwrites_only_nonempty_template_fields(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_a.id,
            "origin_location": "Keep me",
            "destination_location": "Old dest",
        })
        other = self.env["taj.transport.route"].create({
            "name": "Overwrite dest only",
            "company_id": self.env.company.id,
            "destination_location": "New dest",
        })
        trip.write({"route_id": other.id})
        self.assertEqual(trip.origin_location, "Keep me")
        self.assertEqual(trip.destination_location, "New dest")
        trip.write({"route_id": False})
        self.assertEqual(trip.origin_location, "Keep me")
        self.assertEqual(trip.destination_location, "New dest")

    def test_fill_empty_route_assignment_overwrites_locations(self):
        trip = self.env["taj.transport.trip"].create({
            "sale_line_id": self.line_a.id,
            "origin_location": "Filled origin",
            "destination_location": "Filled dest",
            "cargo_description": "Already set",
        })
        defaults = self.env["taj.transport.fill.empty.fields.wizard"].with_context(
            default_trip_id=trip.id,
        ).default_get([
            "trip_id", "empty_field_names", "route_id",
            "origin_location", "destination_location", "cargo_description",
        ])
        self.assertIn("route_id", defaults["empty_field_names"])
        self.assertNotIn("origin_location", defaults["empty_field_names"])
        self.assertNotIn("cargo_description", defaults["empty_field_names"])
        defaults["route_id"] = self.route_a.id
        wizard = self.env["taj.transport.fill.empty.fields.wizard"].create(defaults)
        wizard.action_save()
        self.assertEqual(trip.route_id, self.route_a)
        self.assertEqual(trip.origin_location, "Template Origin A")
        self.assertEqual(trip.destination_location, "Template Dest A")
        self.assertEqual(trip.cargo_description, "Already set")
        self.assertTrue(trip.milestone_ids)

    def test_wizard_rejects_duplicate_sale_lines_before_create(self):
        with self.assertRaises(UserError):
            self.env["taj.transport.trip.create.wizard"].create({
                "sale_order_id": self.order.id,
                "selection_ids": [
                    Command.create({
                        "sale_line_id": self.line_a.id,
                        "route_id": self.route_a.id,
                    }),
                    Command.create({
                        "sale_line_id": self.line_a.id,
                        "route_id": self.route_a.id,
                    }),
                ],
            }).action_create_trips()
        self.assertFalse(self.line_a.transport_trip_ids)

    def test_wizard_requires_per_line_matching_templates(self):
        wizard = self.env["taj.transport.trip.create.wizard"].create({
            "sale_order_id": self.order.id,
            "selection_ids": [
                Command.create({
                    "sale_line_id": self.line_a.id,
                    "route_id": self.route_a.id,
                }),
                Command.create({
                    "sale_line_id": self.line_b.id,
                    "route_id": self.route_b1.id,
                }),
            ],
        })
        wizard.action_create_trips()
        self.assertEqual(self.line_a.transport_trip_ids.route_id, self.route_a)
        self.assertEqual(self.line_b.transport_trip_ids.route_id, self.route_b1)
        for line in self.line_a | self.line_b:
            trip = line.transport_trip_ids
            self.assertEqual(trip.cargo_description, line.cargo_description)
            self.assertEqual(trip.cargo_type_id, line.cargo_type_id)
            self.assertEqual(
                trip.special_handling_instructions,
                line.special_handling_instructions,
            )

    def test_wizard_refuses_missing_and_forged_templates_before_create(self):
        with self.assertRaises(UserError):
            self.env["taj.transport.trip.create.wizard"].create({
                "sale_order_id": self.order.id,
                "selection_ids": [Command.create({
                    "sale_line_id": self.line_b.id,
                })],
            }).action_create_trips()
        self.assertFalse(self.line_b.transport_trip_ids)

        with self.assertRaises(UserError):
            self.env["taj.transport.trip.create.wizard"].create({
                "sale_order_id": self.order.id,
                "selection_ids": [Command.create({
                    "sale_line_id": self.line_b.id,
                    "route_id": self.route_a.id,
                })],
            }).action_create_trips()
        self.assertFalse(self.line_b.transport_trip_ids)

    def test_line_button_unique_ambiguous_and_missing(self):
        action = self.line_a.action_open_or_create_transport_trip()
        self.assertEqual(action["res_model"], "taj.transport.trip")
        self.assertEqual(
            self.line_a.transport_trip_ids.cargo_description, "Palletized machinery"
        )
        self.assertEqual(
            self.line_a.transport_trip_ids.route_id, self.route_a
        )

        ambiguous = self.line_b.action_open_or_create_transport_trip()
        self.assertEqual(
            ambiguous["res_model"],
            "taj.transport.trip.route.chooser.wizard",
        )
        self.assertFalse(self.line_b.transport_trip_ids)

        orphan = self.env["product.product"].create({
            "name": "Orphan - Route (40ft)",
            "type": "service",
            "list_price": 50,
        })
        line = self.env["sale.order.line"].create({
            "order_id": self.order.id,
            "product_id": orphan.id,
            "product_uom_qty": 1,
            "price_unit": 50,
        })
        with self.assertRaises(UserError):
            line.action_open_or_create_transport_trip()
