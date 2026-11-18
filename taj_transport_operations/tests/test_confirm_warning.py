"""Confirm warns about empty configured shipment fields and still allows it."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestConfirmWithEmptyShipmentFields(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.transport_required_shipment_fields = []
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.env["res.partner"]
                .create({"name": "Confirm warning customer"})
                .id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Confirm warning service",
                "type": "service",
            }
        )
        cls.order.write(
            {
                "order_line": [(0, 0, {"product_id": cls.product.id})],
            }
        )
        cls.line = cls.order.order_line

    def test_empty_required_field_warns_then_confirms_on_proceed(self):
        self.company.transport_required_shipment_fields = ["consignee"]
        action = self.order.action_confirm()
        self.assertEqual(action["res_model"], "taj.transport.sale.confirm.wizard")
        self.assertEqual(self.order.state, "draft")
        wizard = self.env["taj.transport.sale.confirm.wizard"].browse(action["res_id"])
        self.assertIn("consignee", wizard.warning_message.lower())
        wizard.action_confirm_order()
        self.assertEqual(self.order.state, "sale")
        self.assertFalse(self.line.consignee)

    def test_filled_required_field_confirms_without_warning(self):
        self.company.transport_required_shipment_fields = ["consignee"]
        self.line.write({"consignee": "Receiver"})
        self.order.action_confirm()
        self.assertEqual(self.order.state, "sale")
