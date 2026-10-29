"""Shared fixtures for the Phase 3 route template and checkpoint tests.

Phase 3 is additive, so these cases build trips exactly like Phase 2 does:
one confirmed sales order line, one trip, planned route/times and a resource
assignment. Checkpoints are only recorded once such a trip is Ready, which is
the operational workflow under test.
"""

from contextlib import contextmanager
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase
from odoo.tests.common import new_test_user

MANAGER_GROUP = "taj_transport_operations.group_transport_manager"
DISPATCHER_GROUP = "taj_transport_operations.group_transport_user"


class TajTransportPhase3Case(TransactionCase):
    """Fixtures and helpers shared by the Phase 3 acceptance tests."""

    #: Used to build distinct test logins, one set per test class.
    user_tag = "phase3"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Phase 3 customer",
            }
        )
        cls.driver = cls.env["res.partner"].create(
            {
                "name": "Phase 3 driver",
            }
        )
        cls.brand = cls.env["fleet.vehicle.model.brand"].create(
            {
                "name": "Phase 3 brand",
            }
        )
        cls.truck_model = cls.env["fleet.vehicle.model"].create(
            {
                "name": "Phase 3 truck model",
                "brand_id": cls.brand.id,
                "category_id": cls.env.ref(
                    "taj_transport_operations.fleet_category_truck"
                ).id,
            }
        )
        cls.truck = cls.env["fleet.vehicle"].create(
            {
                "model_id": cls.truck_model.id,
                "license_plate": "PHASE3-TRUCK-01",
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Phase 3 transport service",
                "type": "service",
                "list_price": 900,
            }
        )
        cls.manager = new_test_user(
            cls.env,
            login="%s_manager" % cls.user_tag,
            company_id=cls.company.id,
            groups="base.group_user,%s" % MANAGER_GROUP,
        )
        cls.dispatcher = new_test_user(
            cls.env,
            login="%s_dispatcher" % cls.user_tag,
            company_id=cls.company.id,
            groups="base.group_user,%s" % DISPATCHER_GROUP,
        )
        cls.type_loading = cls.env.ref(
            "taj_transport_operations.milestone_type_loading"
        )
        cls.type_border = cls.env.ref("taj_transport_operations.milestone_type_border")
        cls.type_offloading = cls.env.ref(
            "taj_transport_operations.milestone_type_offloading"
        )

    # ------------------------------------------------------------------
    # Assertion helper
    # ------------------------------------------------------------------
    @contextmanager
    def assert_raises_any(self, *exceptions):
        """``assertRaises`` accepting several exception classes.

        Odoo 19's ``TestCase.assertRaises`` calls ``issubclass(exception,
        AccessError)`` internally and therefore rejects a tuple of classes.
        """
        try:
            yield
        except exceptions:
            return
        self.fail(
            "Expected one of %s to be raised."
            % ", ".join(exception.__name__ for exception in exceptions)
        )

    # ------------------------------------------------------------------
    # Business fixtures
    # ------------------------------------------------------------------
    def _sale_line(self, tag="A", **vals):
        """A confirmed sales order line, ready to become a trip."""
        partner = self.env["res.partner"].create(
            {
                "name": "Phase 3 customer %s" % tag,
            }
        )
        order = self.env["sale.order"].create({"partner_id": partner.id})
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product.id,
                "name": "Phase 3 freight %s" % tag,
                "product_uom_qty": 1,
                "price_unit": 900,
                "vehicle_id": self.truck.id,
                **vals,
            }
        )
        order.action_confirm()
        return line

    def _default_checkpoints(self):
        """Legacy templates include retired confirmation flags for upgrade coverage."""
        return [
            {
                "name": "Loading",
                "milestone_type_id": self.type_loading.id,
                "requires_confirmation": True,
                "planned_offset_hours": 0.0,
            },
            {
                "name": "Kasumbalesa border",
                "milestone_type_id": self.type_border.id,
                "planned_offset_hours": 18.0,
            },
            {
                "name": "Offloading",
                "milestone_type_id": self.type_offloading.id,
                "requires_confirmation": True,
                "planned_offset_hours": 48.0,
            },
        ]

    def _route(self, name, specs=None, **vals):
        route = self.env["taj.transport.route"].create(
            {
                "name": name,
                "company_id": self.company.id,
                **vals,
            }
        )
        checkpoints = self._default_checkpoints() if specs is None else specs
        for index, spec in enumerate(checkpoints):
            self.env["taj.transport.route.line"].create(
                {
                    "route_id": route.id,
                    "sequence": (index + 1) * 10,
                    **spec,
                }
            )
        return route

    def _trip(self, route=None, tag="A", line=None, **vals):
        values = {
            "sale_line_id": (line or self._sale_line(tag)).id,
            **vals,
        }
        if route:
            values["route_id"] = route.id
        return self.env["taj.transport.trip"].create(values)

    def _plan(self, trip):
        now = fields.Datetime.now()
        trip.write(
            {
                "origin_location": "Dar es Salaam",
                "destination_location": "Lusaka",
                "planned_departure_at": now + timedelta(hours=2),
                "planned_arrival_at": now + timedelta(days=2),
            }
        )
        return trip

    def _assign(self, trip):
        self.env["taj.transport.assignment"].create(
            {
                "trip_id": trip.id,
                "vehicle_id": self.truck.id,
                "driver_id": self.driver.id,
                "effective_from": fields.Datetime.now() - timedelta(hours=4),
            }
        )
        return trip

    def _planned_trip(self, route=None, tag="P", **vals):
        """Draft trip with route, locations, planned times and resources."""
        trip = self._trip(route, tag=tag, **vals)
        self._plan(trip)
        return self._assign(trip)

    def _ready_trip(self, route=None, tag="R", **vals):
        trip = self._planned_trip(route, tag=tag, **vals)
        trip.action_mark_ready()
        return trip

    def _started_trip(self, route=None, tag="S", **vals):
        trip = self._ready_trip(route, tag=tag, **vals)
        trip.write(
            {
                "actual_departure_at": fields.Datetime.now() - timedelta(hours=1),
            }
        )
        trip.action_start_trip()
        return trip

    def _completed_trip(self, route=None, tag="C", **vals):
        trip = self._started_trip(route, tag=tag, **vals)
        trip.action_complete_trip()
        return trip

    def _cancel_trip(self, trip, reason="Customer cancelled the load"):
        self.env["taj.transport.cancel.trip.wizard"].create(
            {
                "trip_id": trip.id,
                "reason": reason,
            }
        ).action_confirm_cancellation()
        return trip

    # ------------------------------------------------------------------
    # Checkpoint helpers
    # ------------------------------------------------------------------
    def _milestone(self, trip, name):
        milestone = trip.milestone_ids.filtered(
            lambda row: row.name == name,
        )
        self.assertEqual(len(milestone), 1, "Checkpoint %r not found" % name)
        return milestone

    def _record(self, milestone, action_type, user=None, **vals):
        """Record a checkpoint event through the wizard, as the UI does."""
        wizard_model = self.env["taj.transport.milestone.action.wizard"]
        if user:
            wizard_model = wizard_model.with_user(user)
        wizard = wizard_model.create(
            {
                "milestone_id": milestone.id,
                "action_type": action_type,
                **vals,
            }
        )
        return wizard.action_confirm()

    def _events(self, milestone, event_type=None):
        events = milestone.event_ids
        if event_type:
            events = events.filtered(
                lambda event: event.event_type == event_type,
            )
        return events
