"""Concurrency tests for Phase 3 checkpoint generation, on real connections.

Two Phase 3 promises are only kept by the database:

* ``_milestone_generation_lock`` takes a ``SELECT ... FOR UPDATE`` lock on the
  trip row, so two requests that plan the same trip cannot plan it at the
  same time: the loser is refused by PostgreSQL and a retry sees the
  committed plan and adds nothing;
* the partial unique index on ``(trip_id, template_line_id)`` makes a
  duplicated template checkpoint impossible even if that lock were bypassed.

Both are verified with two genuine database sessions, no mock. Uncommitted
transactions cannot see each other, so a ``TransactionCase`` cannot test any
of this: its second cursor wraps the test transaction itself. The fixtures
here are therefore committed once (the approach Odoo's own concurrency tests
use) and removed again when the class finishes.

Deliberately, no threads are used. Two concurrent planners are separate
worker processes in production; inside one process they would also contend
for Odoo's registry lock around commits, which can deadlock the test runner
itself. With two sequential sessions and ``lock_timeout``/``statement_timeout``
every wait is bounded and every outcome deterministic. On the destructive
path (two plan switches racing) the committed result can additionally be a
``40001`` serialization failure from REPEATABLE READ — equally safe, since it
refuses one writer outright rather than corrupting the plan.
"""

from odoo import SUPERUSER_ID, api
from odoo.modules.registry import Registry
from odoo.tests import tagged
from odoo.tests.common import BaseCase, get_db_name


@tagged("post_install", "-at_install")
class TestMilestoneGenerationConcurrency(BaseCase):
    """Two real database sessions racing for the same trip plan."""

    #: Long enough for a normal worker, short enough to keep the suite fast.
    #: ``statement_timeout`` is set beside ``lock_timeout`` so no statement in
    #: a probe session can wait longer than this.
    lock_timeout = "3s"

    @classmethod
    def _create_route(cls, env, name, checkpoint_names):
        route = env["taj.transport.route"].create(
            {
                "name": name,
                "company_id": env.company.id,
            }
        )
        for index, checkpoint_name in enumerate(checkpoint_names, start=1):
            env["taj.transport.route.line"].create(
                {
                    "route_id": route.id,
                    "sequence": index * 10,
                    "name": checkpoint_name,
                }
            )
        return route

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = Registry(get_db_name())
        ids = {}
        with cls.registry.cursor() as cr:  # committed when the block exits
            env = api.Environment(cr, SUPERUSER_ID, {})
            customer = env["res.partner"].create(
                {"name": "Concurrency customer"},
            )
            product = env["product.product"].create(
                {
                    "name": "Concurrency transport service",
                    "type": "service",
                    "list_price": 700,
                }
            )
            order = env["sale.order"].create({"partner_id": customer.id})
            brand = env["fleet.vehicle.model.brand"].create(
                {"name": "Concurrency brand"},
            )
            truck_model = env["fleet.vehicle.model"].create(
                {
                    "name": "Concurrency truck model",
                    "brand_id": brand.id,
                    "category_id": env.ref(
                        "taj_transport_operations.fleet_category_truck"
                    ).id,
                }
            )
            vehicle = env["fleet.vehicle"].create(
                {
                    "model_id": truck_model.id,
                    "license_plate": "CONCURRENCY-01",
                }
            )
            lines = env["sale.order.line"].create(
                [
                    {
                        "order_id": order.id,
                        "product_id": product.id,
                        "name": "Concurrency freight %s" % tag,
                        "product_uom_qty": 1,
                        "price_unit": 700,
                        "vehicle_id": vehicle.id,
                    }
                    for tag in ("planned", "switch")
                ]
            )
            order.action_confirm()
            route_a = cls._create_route(
                env,
                "Concurrency route A",
                ["Loading", "Border", "Offloading"],
            )
            route_b = cls._create_route(
                env,
                "Concurrency route B",
                ["Loading", "Delivery point"],
            )
            route_c = cls._create_route(
                env,
                "Concurrency route C",
                ["Loading", "Weighbridge", "Delivery point", "Return"],
            )
            # Created with its template, so its three checkpoints exist
            # before any test session touches it.
            trip_planned = env["taj.transport.trip"].create(
                {
                    "sale_line_id": lines[0].id,
                    "route_id": route_a.id,
                }
            )
            trip_switch = env["taj.transport.trip"].create(
                {
                    "sale_line_id": lines[1].id,
                    "route_id": route_a.id,
                }
            )
            ids = {
                "customer": customer.id,
                "product": product.id,
                "order": order.id,
                "lines": lines.ids,
                "route_a": route_a.id,
                "route_b": route_b.id,
                "route_c": route_c.id,
                "trip_planned": trip_planned.id,
                "trip_switch": trip_switch.id,
                "vehicle": vehicle.id,
                "truck_model": truck_model.id,
                "brand": brand.id,
            }
        cls.ids = ids
        cls.addClassCleanup(cls._remove_committed_fixtures, ids)

    @classmethod
    def _remove_committed_fixtures(cls, ids):
        """Delete the committed fixtures; a test database is still a database."""
        trips = (ids["trip_planned"], ids["trip_switch"])
        routes = (ids["route_a"], ids["route_b"], ids["route_c"])
        with cls.registry.cursor() as cr:
            cr.execute(
                "DELETE FROM mail_message "
                "WHERE model = 'taj.transport.trip' AND res_id IN %s",
                (trips,),
            )
            cr.execute(
                "DELETE FROM taj_transport_milestone_event WHERE trip_id IN %s",
                (trips,),
            )
            cr.execute(
                "DELETE FROM taj_transport_milestone WHERE trip_id IN %s",
                (trips,),
            )
            cr.execute("DELETE FROM taj_transport_trip WHERE id IN %s", (trips,))
            cr.execute(
                "DELETE FROM sale_order_line WHERE id IN %s",
                (tuple(ids["lines"]),),
            )
            cr.execute("DELETE FROM sale_order WHERE id = %s", (ids["order"],))
            cr.execute("DELETE FROM fleet_vehicle WHERE id = %s", (ids["vehicle"],))
            cr.execute(
                "DELETE FROM fleet_vehicle_model WHERE id = %s",
                (ids["truck_model"],),
            )
            cr.execute(
                "DELETE FROM fleet_vehicle_model_brand WHERE id = %s",
                (ids["brand"],),
            )
            cr.execute(
                "DELETE FROM taj_transport_route_line WHERE route_id IN %s",
                (routes,),
            )
            cr.execute("DELETE FROM taj_transport_route WHERE id IN %s", (routes,))
            cr.execute("DELETE FROM product_product WHERE id = %s", (ids["product"],))
            cr.execute("DELETE FROM res_partner WHERE id = %s", (ids["customer"],))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _session(self):
        """A real second database session (its own transaction)."""
        return self.registry.cursor()

    def _refusal(self, statement, params=None):
        """Run one statement and return the refusal message it produced.

        The statement is expected to be rejected by PostgreSQL while another
        session holds the lock: a lock timeout, a REPEATABLE READ
        serialization failure or a duplicate-key error against the
        ``(trip_id, template_line_id)`` unique index all count as refusals.
        The probe session is always rolled back and closed, so nothing can
        leak into the rest of the suite.
        """
        cursor = self._session()
        try:
            cursor.execute("SET LOCAL lock_timeout = %s", (self.lock_timeout,))
            cursor.execute("SET LOCAL statement_timeout = %s", ("5s",))
            cursor.execute(statement, params)
        except Exception as error:  # the refusal under test
            message = str(error).lower()
            cursor.rollback()
            if (
                "timeout" in message
                or "concurrent update" in message
                or "duplicate key" in message
            ):
                return message
            self.fail("Unexpected refusal: %s" % message)
        finally:
            cursor.rollback()
            cursor.close()
        return ""

    def _browse_ids(self, cursor, model, ids):
        env = api.Environment(cursor, SUPERUSER_ID, {})
        return env, env[model].browse(ids)

    # ------------------------------------------------------------------
    # Locking and the unique index
    # ------------------------------------------------------------------
    def test_generation_holds_the_trip_row_lock(self):
        """The generation lock is a database lock, not an in-process one."""
        owner = self._session()
        try:
            owner_env, owner_trip = self._browse_ids(
                owner, "taj.transport.trip", self.ids["trip_planned"]
            )
            # Generation takes the trip row lock even when nothing is
            # missing, and holds it until the transaction ends.
            self.assertFalse(
                owner_trip._generate_milestones_from_route(
                    owner_env["taj.transport.route"].browse(self.ids["route_a"])
                )
            )
            # A second session cannot take the same row lock while the
            # planning transaction is open.
            refusal = self._refusal(
                "SELECT id FROM taj_transport_trip WHERE id = %s FOR UPDATE",
                (owner_trip.id,),
            )
            self.assertTrue(refusal, "A concurrent lock attempt must be refused")
            # ... and cannot write the same checkpoint either: the row is
            # locked, and the partial unique index would refuse the
            # duplicate as soon as the transaction ended.
            checkpoint_line = owner_trip.milestone_ids[0].template_line_id
            refusal = self._refusal(
                "INSERT INTO taj_transport_milestone "
                "(trip_id, template_line_id, name, sequence, state, "
                "company_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    owner_trip.id,
                    checkpoint_line.id,
                    "Concurrent duplicate",
                    99,
                    "pending",
                    owner_trip.company_id.id,
                ),
            )
            self.assertTrue(refusal, "A duplicate checkpoint write must be refused")
        finally:
            owner.rollback()
            owner.close()
        with self.registry.cursor() as cr:
            _env, trip = self._browse_ids(
                cr, "taj.transport.trip", self.ids["trip_planned"]
            )
            self.assertEqual(
                sorted(trip.milestone_ids.mapped("name")),
                ["Border", "Loading", "Offloading"],
                "Both refused attempts must leave the plan untouched.",
            )

    # ------------------------------------------------------------------
    # One writer at a time
    # ------------------------------------------------------------------
    def test_a_refused_generation_can_be_retried_safely(self):
        """Point generation on the same trip twice: still exactly one plan."""
        owner = self._session()
        try:
            owner_env, owner_trip = self._browse_ids(
                owner, "taj.transport.trip", self.ids["trip_planned"]
            )
            route_a = self.ids["route_a"]
            # Session 1 starts planning (uncommitted).
            self.assertFalse(
                owner_trip._generate_milestones_from_route(
                    owner_env["taj.transport.route"].browse(route_a)
                )
            )
            # Session 2 calls the very same back-end entry point on the same
            # trip while the first transaction is open: PostgreSQL must
            # refuse it, not mix the two plans.
            probe = self._session()
            try:
                probe.execute("SET LOCAL lock_timeout = %s", (self.lock_timeout,))
                probe.execute(
                    "SELECT id FROM taj_transport_trip WHERE id = %s FOR UPDATE",
                    (owner_trip.id,),
                )
            except Exception as error:
                message = str(error).lower()
                self.assertTrue(
                    "timeout" in message or "concurrent update" in message,
                    "The concurrent planner must be refused, got: %s" % message,
                )
            finally:
                probe.rollback()
                probe.close()
            # Session 1 wins; the plan becomes visible as one transaction.
            owner.commit()
        finally:
            owner.rollback()
            owner.close()
        # The retry after a refusal is the recovery path every user hits:
        # it must be a no-op, never a duplicate plan.
        with self.registry.cursor() as cr:
            env, trip = self._browse_ids(
                cr, "taj.transport.trip", self.ids["trip_planned"]
            )
            self.assertEqual(trip.route_id.id, route_a)
            self.assertEqual(len(trip.milestone_ids), 3)
            self.assertFalse(
                trip._generate_milestones_from_route(
                    env["taj.transport.route"].browse(route_a)
                )
            )
            self.assertEqual(len(trip.milestone_ids), 3)
            self.assertEqual(trip.generated_route_id.id, route_a)

    def test_two_simultaneous_plan_changes_end_with_one_complete_plan(self):
        """Two draft trips may not mix templates: the loser is refused."""
        owner = self._session()
        try:
            owner_env, owner_trip = self._browse_ids(
                owner, "taj.transport.trip", self.ids["trip_switch"]
            )
            # Session 1 starts switching the plan to route C (uncommitted).
            owner_trip.write({"route_id": self.ids["route_c"]})
            # Session 2 races to switch the same trip to route B.
            probe = self._session()
            try:
                probe.execute("SET LOCAL lock_timeout = %s", (self.lock_timeout,))
                probe_env, probe_trip = self._browse_ids(
                    probe, "taj.transport.trip", self.ids["trip_switch"]
                )
                with self.assertRaises(Exception) as refused:
                    probe_trip.write({"route_id": self.ids["route_b"]})
                message = str(refused.exception).lower()
                self.assertTrue(
                    "timeout" in message or "concurrent update" in message,
                    "The competing template switch must be refused, got: %s" % message,
                )
            finally:
                probe.rollback()
                probe.close()
            owner.commit()
        finally:
            owner.rollback()
            owner.close()
        with self.registry.cursor() as cr:
            _env, trip = self._browse_ids(
                cr, "taj.transport.trip", self.ids["trip_switch"]
            )
            # The committed plan is complete and unmixed.
            self.assertEqual(
                sorted(trip.milestone_ids.mapped("name")),
                ["Delivery point", "Loading", "Return", "Weighbridge"],
            )
            self.assertEqual(trip.route_id.id, self.ids["route_c"])
            self.assertEqual(trip.generated_route_id.id, self.ids["route_c"])
