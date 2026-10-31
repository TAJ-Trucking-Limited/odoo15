"""Real two-session lock test for Trip Email Updates.

Unlike a TransactionCase, these fixtures are committed deliberately so an
independent PostgreSQL cursor can see them. The second session is refused
while the first one holds the trip row lock; after a rollback/retry, the
automatic dispatcher sees the moved schedule and does not queue again.
"""

from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields
from odoo.modules.registry import Registry
from odoo.tests import tagged
from odoo.tests.common import BaseCase, get_db_name


@tagged("post_install", "-at_install")
class TestTripEmailConcurrency(BaseCase):
    """Exercise a manual/automatic race with two real database sessions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = Registry(get_db_name())
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            customer = env["res.partner"].create({
                "name": "Email concurrency customer",
                "email": "race@example.test",
            })
            driver = env["res.partner"].create({
                "name": "Email concurrency driver",
            })
            product = env["product.product"].create({
                "name": "Email concurrency freight",
                "type": "service",
                "list_price": 100,
            })
            brand = env["fleet.vehicle.model.brand"].create({
                "name": "Email concurrency brand",
            })
            truck_model = env["fleet.vehicle.model"].create({
                "name": "Email concurrency truck model",
                "brand_id": brand.id,
                "category_id": env.ref(
                    "taj_transport_operations.fleet_category_truck"
                ).id,
            })
            vehicle = env["fleet.vehicle"].create({
                "model_id": truck_model.id,
                "license_plate": "EMAIL-RACE-01",
                "driver_id": driver.id,
            })
            order = env["sale.order"].create({
                "partner_id": customer.id,
            })
            line = env["sale.order.line"].create({
                "order_id": order.id,
                "product_id": product.id,
                "name": "Email race freight",
                "product_uom_qty": 1,
                "price_unit": 100,
                "vehicle_id": vehicle.id,
            })
            order.action_confirm()
            now = fields.Datetime.now()
            trip = env["taj.transport.trip"].create({
                "sale_line_id": line.id,
                "origin_location": "Port",
                "destination_location": "Warehouse",
                "planned_departure_at": now - timedelta(hours=1),
                "planned_arrival_at": now + timedelta(days=1),
            })
            trip.action_mark_ready()
            trip.action_start_trip()
            trip.write({
                "email_partner_ids": [(4, customer.id)],
                "email_notification_enabled": True,
                "email_interval_hours": 4.0,
            })
            trip._write_email_schedule({
                "email_next_send_at": now - timedelta(minutes=1),
            })
            ids = {
                "trip": trip.id,
                "line": line.id,
                "order": order.id,
                "vehicle": vehicle.id,
                "truck_model": truck_model.id,
                "brand": brand.id,
                "product": product.id,
                "customer": customer.id,
                "driver": driver.id,
            }
        cls.ids = ids
        cls.addClassCleanup(cls._remove_committed_fixtures, ids)

    @classmethod
    def _remove_committed_fixtures(cls, ids):
        """Only remove records created by this dedicated isolated test."""
        with cls.registry.cursor() as cr:
            trip_id = ids["trip"]
            cr.execute(
                "DELETE FROM taj_transport_trip_email_dispatch_mail_rel "
                "WHERE dispatch_id IN ("
                " SELECT id FROM taj_transport_trip_email_dispatch "
                " WHERE trip_id = %s)",
                (trip_id,),
            )
            cr.execute(
                "DELETE FROM taj_transport_trip_email_dispatch "
                "WHERE trip_id = %s",
                (trip_id,),
            )
            env = api.Environment(cr, SUPERUSER_ID, {})
            env["mail.mail"].sudo().search([
                ("model", "=", "taj.transport.trip"),
                ("res_id", "=", trip_id),
            ]).unlink()
            cr.execute(
                "DELETE FROM mail_message "
                "WHERE model = 'taj.transport.trip' AND res_id = %s",
                (trip_id,),
            )
            cr.execute(
                "DELETE FROM taj_transport_trip_file WHERE sale_line_id = %s",
                (ids["line"],),
            )
            cr.execute(
                "DELETE FROM taj_transport_milestone_event WHERE trip_id = %s",
                (trip_id,),
            )
            cr.execute(
                "DELETE FROM taj_transport_milestone WHERE trip_id = %s",
                (trip_id,),
            )
            cr.execute(
                "DELETE FROM taj_transport_assignment WHERE trip_id = %s",
                (trip_id,),
            )
            cr.execute("DELETE FROM taj_transport_trip WHERE id = %s", (trip_id,))
            cr.execute(
                "DELETE FROM sale_order_line WHERE id = %s", (ids["line"],),
            )
            cr.execute(
                "DELETE FROM sale_order WHERE id = %s", (ids["order"],),
            )
            cr.execute(
                "DELETE FROM fleet_vehicle WHERE id = %s", (ids["vehicle"],),
            )
            cr.execute(
                "DELETE FROM fleet_vehicle_model WHERE id = %s",
                (ids["truck_model"],),
            )
            cr.execute(
                "DELETE FROM fleet_vehicle_model_brand WHERE id = %s",
                (ids["brand"],),
            )
            cr.execute(
                "DELETE FROM product_product WHERE id = %s", (ids["product"],),
            )
            cr.execute(
                "DELETE FROM res_partner WHERE id IN %s",
                ((ids["customer"], ids["driver"]),),
            )

    def test_manual_dispatch_holds_lock_and_cron_retry_skips(self):
        """An automatic worker cannot dispatch while manual send holds the row."""
        owner = self.registry.cursor()
        contender = self.registry.cursor()
        try:
            owner_env = api.Environment(owner, SUPERUSER_ID, {})
            contender_env = api.Environment(contender, SUPERUSER_ID, {})
            trip_owner = owner_env["taj.transport.trip"].browse(
                self.ids["trip"]
            )
            trip_contender = contender_env["taj.transport.trip"].browse(
                self.ids["trip"]
            )

            # Pin the contender's pre-dispatch view of the due trip.
            self.assertEqual(trip_contender.state, "in_progress")
            self.assertTrue(trip_contender.email_next_send_at)
            contender.execute("SET LOCAL lock_timeout = %s", ("1s",))
            contender.execute("SET LOCAL statement_timeout = %s", ("3s",))

            # The owner queues a manual update, but does not commit yet.
            trip_owner.action_send_email_update_manual()
            self.assertEqual(len(trip_owner.email_dispatch_ids), 1)

            # A separate database transaction must be refused on row lock.
            try:
                trip_contender._queue_trip_email_update(
                    dispatch_type="automatic"
                )
            except Exception as error:
                self.assertIn("lock timeout", str(error).lower())
            else:
                self.fail("Concurrent worker bypassed the email row lock")
            contender.rollback()
            owner.commit()

            # On a new transaction, the automatic worker sees the moved due
            # time and safely skips without creating a duplicate mail batch.
            retry_env = api.Environment(contender, SUPERUSER_ID, {})
            trip_retry = retry_env["taj.transport.trip"].browse(self.ids["trip"])
            self.assertFalse(
                trip_retry._queue_trip_email_update(
                    dispatch_type="automatic"
                )
            )
            self.assertEqual(len(trip_retry.email_dispatch_ids), 1)
            self.assertEqual(len(trip_retry.email_dispatch_ids.mail_ids), 1)
        finally:
            owner.rollback()
            contender.rollback()
            owner.close()
            contender.close()
