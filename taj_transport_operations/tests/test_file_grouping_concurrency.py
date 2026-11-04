"""Two repeatable-read requests must not create competing order files."""
from psycopg2.errors import SerializationFailure

from odoo import api, Command, SUPERUSER_ID
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFileGroupingConcurrency(TransactionCase):
    def test_first_file_generation_retries_a_stale_order_snapshot(self):
        registry = self.registry
        context = {"tracking_disable": True, "mail_create_nosubscribe": True}
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, context)
            customer = env["res.partner"].create({"name": "File grouping concurrency customer"})
            product = env["product.product"].create({"name": "File grouping concurrency service", "type": "service"})
            order = env["sale.order"].create({
                "partner_id": customer.id,
                "order_line": [Command.create({"product_id": product.id}) for unused in range(2)],
            })
            order.write({"state": "sale"})
            order_id, line_ids, customer_id, product_id = order.id, order.order_line.ids, customer.id, product.id
            cr.commit()
        try:
            with registry.cursor() as owner, registry.cursor() as contender:
                owner_env = api.Environment(owner, SUPERUSER_ID, context)
                contender_env = api.Environment(contender, SUPERUSER_ID, context)
                first = owner_env["sale.order.line"].browse(line_ids[0])
                second = contender_env["sale.order.line"].browse(line_ids[1])
                # Both requests read their source before either creates the file.
                first.read(["order_id"])
                second.read(["order_id"])
                owner_env["taj.transport.trip.file"]._ensure_for_sale_lines(first)
                owner_env.flush_all()
                owner.commit()
                with self.assertRaises(SerializationFailure):
                    contender_env["taj.transport.trip.file"]._ensure_for_sale_lines(second)
                contender.rollback()
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, context)
                env["taj.transport.trip.file"]._ensure_for_sale_lines(env["sale.order.line"].browse(line_ids[1]))
                env.flush_all()
                cr.commit()
                files = env["taj.transport.trip.file"].search([("sale_order_id", "=", order_id)])
                self.assertEqual(len(files), 1)
                self.assertEqual(set(files.line_ids.sale_line_id.ids), set(line_ids))
        finally:
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, context)
                env["taj.transport.trip.file"].search([("sale_order_id", "=", order_id)]).unlink()
                order = env["sale.order"].browse(order_id)
                order.write({"state": "cancel"})
                order.unlink()
                env["product.product"].browse(product_id).unlink()
                env["res.partner"].browse(customer_id).unlink()
                cr.commit()
