"""Phase 2 record set for the upgrade drill, driven by ``run_verification.sh``.

The script feeds this file to ``odoo shell`` twice, against the same database:

``P2_DRILL_MODE=seed`` (on the frozen 19.0.1.6.4 install)
    Creates one confirmed Sale Order (2100.00), two draft trips and one
    assignment, commits, and prints the snapshot.

``P2_DRILL_MODE=check`` (after the upgrade to 19.0.1.7.0)
    Re-reads the very same records, checks the Phase 2 invariants and the
    absence of any unintended backfill, and prints the same snapshot so the
    caller can diff the two.

Snapshot lines start with ``SNAP ``: the caller diffs the common ones byte for
byte and greps for the tokens it requires. The last line of ``check`` mode is
``SNAPCHECK OK``, printed only when every assertion held - ``odoo shell`` keeps
reading after a traceback, so the caller must not trust its exit code alone.
"""

import os

env = self.env
mode = os.environ.get("P2_DRILL_MODE", "seed")


def emit(label, *values):
    print("SNAP %s" % label, *values)


def installed_version():
    module = env["ir.module.module"].search(
        [("name", "=", "taj_transport_operations")],
        limit=1,
    )
    return module.installed_version or module.latest_version or "unknown"


# The same records are located the same way in both modes, so the snapshot is
# built from one single code path.
if mode == "seed":
    customer = env["res.partner"].create({"name": "Drill customer"})
    product = env["product.product"].create(
        {
            "name": "Drill freight service",
            "type": "service",
            "list_price": 700.0,
        }
    )
    order = env["sale.order"].create({"partner_id": customer.id})
    for qty, tag in ((1, "port"), (2, "yard")):
        env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.id,
                "name": "Drill freight %s" % tag,
                "product_uom_qty": qty,
                "price_unit": 700.0,
            }
        )
    order.action_confirm()
    line_port, line_yard = order.order_line.sorted("id")
    trip_port = env["taj.transport.trip"].create({"sale_line_id": line_port.id})
    trip_yard = env["taj.transport.trip"].create({"sale_line_id": line_yard.id})
    driver = env["res.partner"].create({"name": "Drill driver"})
    assignment = env["taj.transport.assignment"].create(
        {
            "trip_id": trip_port.id,
            "driver_id": driver.id,
            "reason": "Drill assignment",
        }
    )
    # Same order as the check mode below (ascending id).
    trips = (trip_port + trip_yard).sorted("id")
    trip_port, trip_yard = trips
    env.cr.commit()
else:
    customer = env["res.partner"].search([("name", "=", "Drill customer")], limit=1)
    product = env["product.product"].search(
        [("name", "=", "Drill freight service")],
        limit=1,
    )
    driver = env["res.partner"].search([("name", "=", "Drill driver")], limit=1)
    order = env["sale.order"].search([("partner_id", "=", customer.id)], limit=1)
    trips = env["taj.transport.trip"].search(
        [("sale_line_id", "in", order.order_line.ids)],
        order="id",
    )
    assignment = env["taj.transport.assignment"].search(
        [("trip_id", "=", trips[0].id)],
        order="id",
    )
    trip_port, trip_yard = trips[0], trips[1]

    # Phase 2 invariants: the sale, the trips and the assignment are exactly
    # what the frozen release produced.
    assert order.state == "sale", order.state
    assert abs(order.amount_untaxed - 2100.0) < 0.01, order.amount_untaxed
    assert len(order.order_line) == 2, len(order.order_line)
    assert (trip_port.state, trip_yard.state) == ("draft", "draft"), trips.mapped(
        "state"
    )
    assert assignment.trip_id == trip_port, assignment.trip_id
    assert assignment.driver_id == driver, assignment.driver_id
    assert assignment.effective_from, "The assignment history lost its start time"
    # No unintended backfill: a Phase 2 dataset gets no routes, no checkpoints
    # and no milestone events, and the Phase 3 fields stay empty.
    assert not trip_port.route_id and not trip_yard.route_id, trips.mapped("route_id")
    assert not trip_port.milestone_ids and not trip_yard.milestone_ids
    assert trip_port.milestone_count == 0 and trip_port.milestone_done_count == 0
    assert not trip_port.milestone_progress and not trip_yard.milestone_progress
    assert env["taj.transport.route"].search_count([]) == 0
    assert env["taj.transport.milestone.event"].search_count([]) == 0

emit("mode", mode)
emit("version", installed_version())
emit("order", order.name, "%.2f" % order.amount_untaxed, order.state)
emit("lines", len(order.order_line))
emit("trips", len(trips), trip_port.state, trip_yard.state)
emit(
    "ids",
    order.id,
    product.id,
    customer.id,
    trip_port.id,
    trip_yard.id,
    driver.id,
    assignment.id,
)
emit(
    "assignment",
    assignment.trip_id.id,
    assignment.driver_id.id,
    bool(assignment.effective_from),
)
if mode == "check":
    emit("milestones", len(trip_port.milestone_ids), len(trip_yard.milestone_ids))
    emit("progress", trip_port.milestone_progress, trip_yard.milestone_progress)
    emit("routes", env["taj.transport.route"].search_count([]))
    emit("events", env["taj.transport.milestone.event"].search_count([]))
    emit("route_free", bool(trip_port.route_id), bool(trip_yard.route_id))
    print("SNAPCHECK OK")
