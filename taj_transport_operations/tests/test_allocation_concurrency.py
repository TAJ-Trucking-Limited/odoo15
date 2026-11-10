"""Two allocation requests must serialize before changing the first truck."""
from psycopg2.errors import SerializationFailure

from odoo import api, Command, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAllocationConcurrency(TransactionCase):
    def test_stale_request_retries_then_rejects_a_second_truck(self):
        context = {"tracking_disable": True, "mail_create_nosubscribe": True}
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, context)
            customer = env["res.partner"].create({"name": "Allocation concurrency customer"})
            product = env["product.product"].create({"name": "Allocation concurrency freight", "type": "service"})
            brand = env["fleet.vehicle.model.brand"].create({"name": "Allocation concurrency brand"})
            model = env["fleet.vehicle.model"].create({
                "name": "Allocation concurrency model", "brand_id": brand.id,
                "category_id": env.ref("taj_transport_operations.fleet_category_truck").id,
            })
            trucks = env["fleet.vehicle"].create([
                {"model_id": model.id, "license_plate": "ALLOCATION-CONCURRENT-A"},
                {"model_id": model.id, "license_plate": "ALLOCATION-CONCURRENT-B"},
            ])
            order = env["sale.order"].create({
                "partner_id": customer.id, "order_line": [Command.create({"product_id": product.id}) for unused in range(2)],
            })
            order.action_confirm()
            line_id, race_line_id = order.order_line.ids
            order_id = order.id
            customer_id, product_id, model_id, brand_id, vehicle_ids = customer.id, product.id, model.id, brand.id, trucks.ids
            cr.commit()
        try:
            with self.registry.cursor() as owner, self.registry.cursor() as contender:
                owner.execute("SET LOCAL lock_timeout = '5s'")
                contender.execute("SET LOCAL lock_timeout = '5s'")
                first = api.Environment(owner, SUPERUSER_ID, context)
                second = api.Environment(contender, SUPERUSER_ID, context)
                a = first["taj.transport.assign.truck.wizard"].create({"sale_line_id": line_id, "vehicle_id": vehicle_ids[0]})
                b = second["taj.transport.assign.truck.wizard"].create({"sale_line_id": line_id, "vehicle_id": vehicle_ids[1]})
                self.assertFalse(b.sale_line_id.vehicle_id)
                a.action_assign_truck()
                first.flush_all()
                owner.commit()
                with self.assertRaises(SerializationFailure):
                    b.action_assign_truck()
                contender.rollback()
            with self.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, context)
                retry = env["taj.transport.assign.truck.wizard"].create({"sale_line_id": line_id, "vehicle_id": vehicle_ids[1]})
                with self.assertRaises(UserError):
                    retry.action_assign_truck()
                self.assertEqual(env["sale.order.line"].browse(line_id).vehicle_id.id, vehicle_ids[0])
            # A trip can be created after an allocation request's RR snapshot.
            # On retry it must see that trip and create its resource assignment.
            with self.registry.cursor() as creator, self.registry.cursor() as allocator:
                creator.execute("SET LOCAL lock_timeout = '5s'")
                allocator.execute("SET LOCAL lock_timeout = '5s'")
                creating = api.Environment(creator, SUPERUSER_ID, context)
                allocating = api.Environment(allocator, SUPERUSER_ID, context)
                pending = allocating["taj.transport.assign.truck.wizard"].create({"sale_line_id": race_line_id, "vehicle_id": vehicle_ids[1]})
                self.assertFalse(pending.sale_line_id.transport_trip_ids)
                creating["taj.transport.trip"].create({"sale_line_id": race_line_id})
                creating.flush_all()
                creator.commit()
                with self.assertRaises(SerializationFailure):
                    pending.action_assign_truck()
                allocator.rollback()
            with self.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, context)
                env["taj.transport.assign.truck.wizard"].create({"sale_line_id": race_line_id, "vehicle_id": vehicle_ids[1]}).action_assign_truck()
                trip = env["taj.transport.trip"].search([("sale_line_id", "=", race_line_id)])
                self.assertEqual(trip.current_vehicle_id.id, vehicle_ids[1])
                cr.rollback()
        finally:
            with self.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, context)
                env["taj.transport.assign.truck.wizard"].search([("sale_line_id", "in", [line_id, race_line_id])]).unlink()
                env["taj.transport.trip.file"].search([("sale_order_id", "=", order_id)]).unlink()
                cr.execute("DELETE FROM taj_transport_assignment WHERE trip_id IN (SELECT id FROM taj_transport_trip WHERE sale_line_id = %s)", (race_line_id,))
                env["taj.transport.trip"].search([("sale_line_id", "=", race_line_id)]).unlink()
                order = env["sale.order"].browse(order_id)
                order.write({"state": "cancel"})
                order.unlink()
                env["product.product"].browse(product_id).unlink()
                env["fleet.vehicle.assignation.log"].search([("vehicle_id", "in", vehicle_ids)]).unlink()
                env["fleet.vehicle"].browse(vehicle_ids).unlink()
                env["fleet.vehicle.model"].browse(model_id).unlink()
                env["fleet.vehicle.model.brand"].browse(brand_id).unlink()
                env["res.partner"].browse(customer_id).unlink()
                cr.commit()

    def test_two_transactions_cannot_pair_one_trailer(self):
        from psycopg2.errors import UniqueViolation

        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            brand = env['fleet.vehicle.model.brand'].create({'name': 'Pair race'})
            truck_model = env['fleet.vehicle.model'].create({
                'name': 'Pair race truck', 'brand_id': brand.id,
                'category_id': env.ref('taj_transport_operations.fleet_category_truck').id,
            })
            trailer_model = env['fleet.vehicle.model'].create({
                'name': 'Pair race trailer', 'brand_id': brand.id,
                'category_id': env.ref('taj_transport_operations.fleet_category_trailer').id,
            })
            vehicles = env['fleet.vehicle'].create([
                {'model_id': truck_model.id}, {'model_id': truck_model.id},
                {'model_id': trailer_model.id},
            ])
            ids, model_ids, brand_id = vehicles.ids, (truck_model | trailer_model).ids, brand.id
            cr.commit()
        try:
            with self.registry.cursor() as first, self.registry.cursor() as second:
                second.execute('SELECT transport_default_trailer_id FROM fleet_vehicle WHERE id = %s', (ids[1],))
                first.execute('UPDATE fleet_vehicle SET transport_default_trailer_id = %s WHERE id = %s', (ids[2], ids[0]))
                first.commit()
                with self.assertRaises(UniqueViolation):
                    second.execute('UPDATE fleet_vehicle SET transport_default_trailer_id = %s WHERE id = %s', (ids[2], ids[1]))
                second.rollback()
        finally:
            with self.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['fleet.vehicle'].browse(ids).write({'transport_default_trailer_id': False})
                env['fleet.vehicle.assignation.log'].search([('vehicle_id', 'in', ids)]).unlink()
                env['fleet.vehicle'].browse(ids).unlink()
                env['fleet.vehicle.model'].browse(model_ids).unlink()
                env['fleet.vehicle.model.brand'].browse(brand_id).unlink()
                cr.commit()
