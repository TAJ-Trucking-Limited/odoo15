"""Phase 3 acceptance tests: route templates and checkpoint generation policy.

Covers acceptance scenarios A (template setup and permissions), B (a trip
receives its own ordered snapshot of the checkpoints), C (template changes and
regeneration never destroy recorded checkpoint history), F (company isolation)
and the Phase 2 upgrade path: existing trips without a route template stay
valid and are never backfilled automatically.

Section D holds the independent review regressions: clearing a template,
Draft-only plan changes, archived templates refused by the back end, and the
atomicity of a refused generation. Real database concurrency is covered by
``test_milestone_concurrency.py``.
"""

from datetime import timedelta

from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .phase3_case import DISPATCHER_GROUP, MANAGER_GROUP, TajTransportPhase3Case


@tagged("post_install", "-at_install")
class TestRouteTemplates(TajTransportPhase3Case):
    """Route template maintenance, generation and change policy."""

    user_tag = "phase3_route"

    # ------------------------------------------------------------------
    # A. Template maintenance and permissions
    # ------------------------------------------------------------------
    def test_route_template_keeps_ordered_checkpoints(self):
        route = self._route("Dar es Salaam - Lusaka")
        self.assertEqual(
            route.line_ids.mapped("name"),
            ["Loading", "Kasumbalesa border", "Offloading"],
        )
        self.assertEqual(route.line_count, 3)
        self.assertEqual(route.line_ids[0].milestone_type_id, self.type_loading)
        self.assertTrue(route.line_ids[0].requires_confirmation)
        self.assertFalse(route.line_ids[1].requires_confirmation)
        self.assertEqual(route.line_ids[2].planned_offset_hours, 48.0)
        self.assertEqual(route.line_ids[1].sequence, 20)
        self.assertEqual(route.company_id, self.company)

    def test_route_template_checkpoints_can_be_reordered(self):
        route = self._route(
            "Reorderable route",
            [
                {"name": "First"},
                {"name": "Second"},
                {"name": "Third"},
            ],
        )
        route.line_ids.filtered(lambda line: line.name == "First").write(
            {
                "sequence": 99,
            }
        )
        route.invalidate_recordset(["line_ids"])
        self.assertEqual(
            route.line_ids.mapped("name"),
            ["Second", "Third", "First"],
        )
        route.line_ids[0].write({"name": "Second renamed"})
        route.invalidate_recordset(["line_ids"])
        self.assertEqual(
            route.line_ids.mapped("name"),
            ["Second renamed", "Third", "First"],
        )

    def test_route_template_refuses_duplicate_checkpoint_names(self):
        route = self._route("Duplicate checkpoints", [{"name": "Loading"}])
        with self.assertRaises(ValidationError):
            self.env["taj.transport.route.line"].create(
                {
                    "route_id": route.id,
                    "name": "loading",
                }
            )
        # The same checkpoint name on another template is legitimate.
        other = self._route("Other route", [{"name": "Loading"}])
        self.assertEqual(other.line_ids.mapped("name"), ["Loading"])

    def test_route_template_name_is_unique_per_company(self):
        self._route("Unique route name", [{"name": "U1"}])
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env["taj.transport.route"].create(
                {
                    "name": "Unique route name",
                    "company_id": self.company.id,
                }
            )
        other_company = self.env["res.company"].create(
            {
                "name": "Phase 3 route company two",
            }
        )
        twin = self.env["taj.transport.route"].create(
            {
                "name": "Unique route name",
                "company_id": other_company.id,
            }
        )
        self.assertEqual(twin.name, "Unique route name")

    def test_dispatcher_cannot_maintain_route_templates(self):
        route = self._route("Dispatcher read only", [{"name": "D1"}])
        self.assertEqual(
            route.with_user(self.dispatcher).read(["name"])[0]["name"],
            "Dispatcher read only",
        )
        self.assertEqual(
            route.line_ids.with_user(self.dispatcher).read(["name"])[0]["name"],
            "D1",
        )
        with self.assertRaises(AccessError):
            self.env["taj.transport.route"].with_user(self.dispatcher).create(
                {
                    "name": "Dispatcher route",
                }
            )
        with self.assertRaises(AccessError):
            route.with_user(self.dispatcher).write({"name": "Dispatcher edit"})
        with self.assertRaises(AccessError):
            route.with_user(self.dispatcher).unlink()
        with self.assertRaises(AccessError):
            self.env["taj.transport.route.line"].with_user(self.dispatcher).create(
                {
                    "route_id": route.id,
                    "name": "Dispatcher checkpoint",
                }
            )
        # A Transport Manager maintains the template and its checkpoints.
        manager_route = (
            self.env["taj.transport.route"]
            .with_user(self.manager)
            .create(
                {
                    "name": "Manager route",
                    "company_id": self.company.id,
                }
            )
        )
        self.env["taj.transport.route.line"].with_user(self.manager).create(
            {
                "route_id": manager_route.id,
                "name": "Manager checkpoint",
            }
        )
        self.assertEqual(manager_route.line_count, 1)

    def test_checkpoint_type_catalog_is_manager_maintained(self):
        self.assertEqual(
            self.type_loading.with_user(self.dispatcher).read(["name"])[0]["name"],
            "Loading",
        )
        with self.assertRaises(AccessError):
            self.env["taj.transport.milestone.type"].with_user(self.dispatcher).create(
                {"name": "Dispatcher type"}
            )
        manager_type = (
            self.env["taj.transport.milestone.type"]
            .with_user(self.manager)
            .create({"name": "Weighbridge"})
        )
        self.assertTrue(manager_type.active)
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env["taj.transport.milestone.type"].create(
                {
                    "name": "Weighbridge",
                }
            )

    def test_route_menu_is_manager_scoped_and_views_are_valid(self):
        menu = self.env.ref("taj_transport_operations.menu_transport_routes")
        self.assertIn(self.env.ref(MANAGER_GROUP), menu.group_ids)
        self.assertNotIn(self.env.ref(DISPATCHER_GROUP), menu.group_ids)
        form = etree.fromstring(
            self.env.ref(
                "taj_transport_operations.view_transport_route_form"
            ).get_combined_arch()
        )
        self.assertEqual(len(form.xpath("//field[@name='line_ids']")), 1)
        for field_name in (
            "sequence",
            "name",
            "milestone_type_id",
            "requires_confirmation",
            "planned_offset_hours",
        ):
            self.assertEqual(
                len(
                    form.xpath(
                        "//field[@name='line_ids']//field[@name='%s']" % field_name
                    )
                ),
                1,
                "Route template form is missing checkpoint field %s" % field_name,
            )
        self.assertEqual(
            len(
                self.env.ref(
                    "taj_transport_operations.menu_transport_milestone_types"
                ).group_ids
            ),
            1,
        )

    # ------------------------------------------------------------------
    # B. Generation: ordered snapshots, independent per trip
    # ------------------------------------------------------------------
    def test_selected_template_generates_ordered_snapshots(self):
        route = self._route(
            "Generation route",
            origin_location="Dar es Salaam",
            destination_location="Lusaka",
        )
        now = fields.Datetime.now()
        trip = self._trip(route)
        trip.write(
            {
                "planned_departure_at": now + timedelta(hours=3),
                "planned_arrival_at": now + timedelta(days=2),
            }
        )
        self.assertEqual(
            trip.milestone_ids.mapped("name"),
            ["Loading", "Kasumbalesa border", "Offloading"],
        )
        self.assertEqual(trip.route_id, route)
        self.assertEqual(trip.generated_route_id, route)
        first = trip.milestone_ids[0]
        self.assertEqual(first.state, "pending")
        self.assertEqual(first.milestone_type_id, self.type_loading)
        self.assertEqual(first.type_name, "Loading")
        self.assertTrue(first.requires_confirmation)
        self.assertEqual(first.template_line_id.milestone_type_id, self.type_loading)
        self.assertEqual(first.company_id, trip.company_id)
        # The planned checkpoint time follows this trip's own planning.
        self.assertAlmostEqual(
            first.planned_arrival_at,
            trip.planned_departure_at,
            delta=timedelta(seconds=1),
        )
        last = trip.milestone_ids[2]
        self.assertAlmostEqual(
            (last.planned_arrival_at - trip.planned_departure_at).total_seconds(),
            48 * 3600,
            delta=2,
        )
        self.assertEqual(trip.milestone_count, 3)
        self.assertEqual(trip.milestone_done_count, 0)
        self.assertEqual(trip.milestone_open_count, 3)
        self.assertEqual(trip.milestone_progress, 0.0)
        # Checkpoint generation is traceable for operations.
        self.assertTrue(trip.message_ids)

    def test_two_trips_from_one_template_are_independent_copies(self):
        route = self._route("Shared template")
        first = self._trip(route, tag="COPY1")
        second = self._trip(route, tag="COPY2")
        self.assertEqual(len(first.milestone_ids), 3)
        self.assertEqual(len(second.milestone_ids), 3)
        self.assertFalse(set(first.milestone_ids.ids) & set(second.milestone_ids.ids))
        # Editing the template must not reach either trip.
        route.line_ids[0].write(
            {
                "name": "Loading renamed",
                "requires_confirmation": False,
                "planned_offset_hours": 5.0,
            }
        )
        route.line_ids[1].unlink()
        self.env["taj.transport.route.line"].create(
            {
                "route_id": route.id,
                "name": "New checkpoint",
                "sequence": 40,
            }
        )
        for trip in (first, second):
            trip.invalidate_recordset()
            self.assertEqual(
                trip.milestone_ids.mapped("name"),
                ["Loading", "Kasumbalesa border", "Offloading"],
            )
            self.assertTrue(trip.milestone_ids[0].requires_confirmation)
            self.assertEqual(trip.milestone_ids[0].planned_offset_hours, 0.0)

    def test_generation_picks_up_new_template_checkpoints(self):
        route = self._route("Growing template")
        trip = self._trip(route)
        self.env["taj.transport.route.line"].create(
            {
                "route_id": route.id,
                "name": "Extra customs stop",
                "sequence": 25,
                "milestone_type_id": self.type_border.id,
            }
        )
        trip.action_generate_milestones()
        self.assertEqual(
            trip.milestone_ids.mapped("name"),
            [
                "Loading",
                "Kasumbalesa border",
                "Extra customs stop",
                "Offloading",
            ],
        )
        # Clicking again is refused instead of duplicating checkpoints.
        with self.assertRaises(UserError):
            trip.action_generate_milestones()
        self.assertEqual(len(trip.milestone_ids), 4)

    def test_repeated_generation_and_saving_create_no_duplicates(self):
        route = self._route("Idempotent template")
        trip = self._trip(route)
        self.assertEqual(len(trip.milestone_ids), 3)
        trip.write({"route_id": route.id})
        trip.write({"origin_location": "Dar es Salaam"})
        self.assertEqual(len(trip.milestone_ids), 3)
        self.assertFalse(trip._generate_milestones_from_route(route))
        self.assertEqual(len(trip.milestone_ids), 3)
        trip.invalidate_recordset()
        self.assertEqual(len(trip.milestone_ids), 3)

    def test_generated_checkpoint_cannot_be_duplicated_by_concurrency(self):
        route = self._route("Indexed template")
        trip = self._trip(route)
        template_line = trip.milestone_ids[0].template_line_id
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env["taj.transport.milestone"].sudo().create(
                {
                    "trip_id": trip.id,
                    "route_id": route.id,
                    "template_line_id": template_line.id,
                    "name": "Concurrent duplicate",
                    "sequence": 99,
                }
            )
        self.assertEqual(len(trip.milestone_ids), 3)

    def test_trip_refuses_duplicate_checkpoint_names(self):
        route = self._route("Name clash template")
        trip = self._trip(route)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["taj.transport.milestone"].sudo().create(
                {
                    "trip_id": trip.id,
                    "name": "loading",
                    "sequence": 99,
                }
            )

    def test_create_trips_wizard_applies_the_selected_template(self):
        route = self._route(
            "Wizard route",
            [
                {"name": "W1"},
                {"name": "W2"},
            ],
        )
        line = self._sale_line("WIZARD")
        wizard = self.env["taj.transport.trip.create.wizard"].create(
            {
                "sale_order_id": line.order_id.id,
                "line_ids": [Command.set(line.ids)],
                "route_id": route.id,
            }
        )
        wizard.action_create_trips()
        trip = line.transport_trip_ids
        self.assertEqual(len(trip), 1)
        self.assertEqual(trip.route_id, route)
        self.assertEqual(trip.milestone_ids.mapped("name"), ["W1", "W2"])

    # ------------------------------------------------------------------
    # C. Template change and regeneration policy
    # ------------------------------------------------------------------
    def test_switching_template_before_operations_replaces_unrecorded_copies(self):
        route_a = self._route("Policy route A", [{"name": "A1"}, {"name": "A2"}])
        route_b = self._route(
            "Policy route B",
            [
                {"name": "B1"},
                {"name": "B2"},
                {"name": "B3"},
            ],
        )
        trip = self._trip(route_a, tag="SWITCH")
        manual = trip._add_manual_milestone({"name": "Manual variance stop"})
        trip.write({"route_id": route_b.id})
        self.assertEqual(trip.route_id, route_b)
        self.assertEqual(trip.generated_route_id, route_b)
        names = trip.milestone_ids.mapped("name")
        self.assertEqual(set(names), {"B1", "B2", "B3", "Manual variance stop"})
        self.assertEqual(
            [name for name in names if name != "Manual variance stop"],
            ["B1", "B2", "B3"],
        )
        self.assertIn(manual, trip.milestone_ids)
        self.assertFalse(manual.template_line_id)
        # The unrecorded generated copies of the first template are gone.
        self.assertFalse(
            trip.milestone_ids.filtered(
                lambda milestone: milestone.name.startswith("A")
            )
        )

    def test_switching_template_after_recording_is_refused(self):
        route_a = self._route("Recorded route A", [{"name": "A1"}, {"name": "A2"}])
        route_b = self._route("Recorded route B", [{"name": "B1"}])
        trip = self._ready_trip(route_a, tag="FROZEN")
        self._record(
            self._milestone(trip, "A1"),
            "arrival",
            event_at=fields.Datetime.now() - timedelta(minutes=30),
        )
        with self.assertRaises(UserError), self.env.cr.savepoint():
            trip.write({"route_id": route_b.id})
        self.assertEqual(trip.route_id, route_a)
        self.assertEqual(trip.milestone_ids.mapped("name"), ["A1", "A2"])
        self.assertTrue(self._milestone(trip, "A1").actual_arrival_at)

    def test_regeneration_wizard_reports_scope_and_keeps_manual_checkpoints(self):
        route = self._route("Regeneration route", [{"name": "R1"}, {"name": "R2"}])
        trip = self._planned_trip(route, tag="REGEN")
        manual = trip._add_manual_milestone({"name": "Weighbridge stop"})
        wizard = self.env["taj.transport.regenerate.milestones.wizard"].create(
            {
                "trip_id": trip.id,
            }
        )
        self.assertEqual(wizard.generated_count, 2)
        self.assertEqual(wizard.recorded_count, 0)
        self.assertTrue(wizard.note)
        route.line_ids[1].write({"name": "R2 renamed"})
        wizard.action_regenerate()
        self.assertEqual(
            trip.milestone_ids.mapped("name"),
            ["R1", "R2 renamed", "Weighbridge stop"],
        )
        self.assertIn(manual, trip.milestone_ids)

    def test_regeneration_is_refused_once_an_event_is_recorded(self):
        route = self._route(
            "Recorded regeneration route",
            [
                {"name": "R1"},
            ],
        )
        trip = self._ready_trip(route, tag="REGEN2")
        self._record(
            self._milestone(trip, "R1"),
            "arrival",
            event_at=fields.Datetime.now() - timedelta(minutes=20),
        )
        wizard = self.env["taj.transport.regenerate.milestones.wizard"].create(
            {
                "trip_id": trip.id,
            }
        )
        self.assertEqual(wizard.recorded_count, 1)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            wizard.action_regenerate()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            trip._regenerate_milestones_from_route()
        self.assertTrue(self._milestone(trip, "R1").actual_arrival_at)

    def test_regeneration_is_refused_on_terminal_trips(self):
        route = self._route("Terminal regeneration route", [{"name": "T1"}])
        trip = self._ready_trip(route, tag="TERMINAL")
        self._cancel_trip(trip)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            trip._regenerate_milestones_from_route()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            trip._generate_milestones_from_route(route, replace_pending=True)
        self.assertEqual(len(trip.milestone_ids), 1)

    def test_archived_template_keeps_trip_history_and_leaves_new_trips_alone(self):
        route = self._route("Archivable route", [{"name": "AR1"}])
        trip = self._ready_trip(route, tag="ARCHIVE")
        self._record(
            self._milestone(trip, "AR1"),
            "arrival",
            event_at=fields.Datetime.now() - timedelta(minutes=10),
        )
        route.write({"active": False})
        self.assertEqual(trip.milestone_ids.mapped("name"), ["AR1"])
        self.assertEqual(trip.generated_route_id, route)
        self.assertEqual(trip.route_id, route)
        self.assertNotIn(
            route.id,
            [
                route_id
                for route_id, _label in self.env["taj.transport.route"].name_search(
                    "Archivable route"
                )
            ],
            "An archived template must not be offered for new trips.",
        )
        self.assertEqual(
            set(trip.milestone_ids.mapped("name")),
            {"AR1"},
            "Archiving a template never rewrites generated checkpoints.",
        )

    def test_route_selection_only_suggests_missing_locations(self):
        route = self._route(
            "Suggestion route",
            [{"name": "S1"}],
            origin_location="Dar es Salaam",
            destination_location="Lusaka",
        )
        empty_trip = self.env["taj.transport.trip"].new(
            {
                "sale_line_id": self._sale_line("SUGGEST1").id,
                "route_id": route.id,
            }
        )
        empty_trip._onchange_route_id_suggest_locations()
        self.assertEqual(empty_trip.origin_location, "Dar es Salaam")
        self.assertEqual(empty_trip.destination_location, "Lusaka")
        filled_trip = self.env["taj.transport.trip"].new(
            {
                "sale_line_id": self._sale_line("SUGGEST2").id,
                "route_id": route.id,
                "origin_location": "Mwanza",
            }
        )
        filled_trip._onchange_route_id_suggest_locations()
        self.assertEqual(filled_trip.origin_location, "Mwanza")
        self.assertEqual(filled_trip.destination_location, "Lusaka")

    # ------------------------------------------------------------------
    # Phase 2 upgrade path and company isolation
    # ------------------------------------------------------------------
    def test_trip_without_template_stays_valid_and_is_never_backfilled(self):
        trip = self._ready_trip(tag="NOROUTE")
        self.assertFalse(trip.route_id)
        self.assertFalse(trip.generated_route_id)
        self.assertFalse(trip.milestone_ids)
        self.assertEqual(trip.milestone_count, 0)
        self.assertEqual(trip.milestone_progress, 0.0)
        with self.assertRaises(UserError):
            trip.action_generate_milestones()
        # The Phase 2 lifecycle is unaffected by an empty checkpoint list.
        trip.write(
            {
                "actual_departure_at": fields.Datetime.now() - timedelta(hours=1),
            }
        )
        trip.action_start_trip()
        trip.action_complete_trip()
        self.assertEqual(trip.state, "completed")
        self.assertFalse(trip.milestone_ids)
        self.assertEqual(trip.milestone_open_count, 0)

    def test_only_a_draft_trip_can_adopt_a_template_later(self):
        route = self._route("Late template", [{"name": "L1"}])
        draft = self._planned_trip(tag="LATEDRAFT")
        self.assertFalse(draft.route_id)
        draft.write({"route_id": route.id})
        self.assertEqual(draft.milestone_ids.mapped("name"), ["L1"])
        self.assertEqual(draft.generated_route_id, route)
        cases = (
            ("ready", self._ready_trip),
            ("in_progress", self._started_trip),
        )
        for state, factory in cases:
            trip = factory(tag="LATE%s" % state.upper())
            self.assertFalse(trip.route_id)
            with self.assertRaises(UserError) as error, self.env.cr.savepoint():
                trip.write({"route_id": route.id})
            self.assertIn("Draft", str(error.exception))
            trip.invalidate_recordset()
            self.assertFalse(trip.route_id)
            self.assertFalse(trip.generated_route_id)
            self.assertFalse(trip.milestone_ids)
            # The sanctioned alternative stays available: the variance is
            # added by hand, and a trip without a plan keeps working.
            manual = trip._add_manual_milestone({"name": "Variance %s" % state})
            self.assertEqual(manual.state, "pending")
            self.assertEqual(trip.state, state)

    # ------------------------------------------------------------------
    # D. Independent review regressions: clearing a template, frozen plans,
    #    archived templates and atomicity
    # ------------------------------------------------------------------
    def test_clearing_the_template_removes_untouched_generated_checkpoints(self):
        route = self._route("Cleared template", [{"name": "C1"}, {"name": "C2"}])
        trip = self._planned_trip(route, tag="CLEAR")
        self.assertEqual(trip.milestone_ids.mapped("name"), ["C1", "C2"])
        trip.write({"route_id": False})
        self.assertFalse(trip.route_id)
        self.assertFalse(trip.generated_route_id)
        self.assertFalse(trip.milestone_ids)
        self.assertEqual(trip.milestone_count, 0)
        self.assertEqual(trip.milestone_open_count, 0)
        # Repeated removal is a harmless no-op, even once the trip was
        # released: no checkpoint and no marker are left to reconcile.
        trip.action_mark_ready()
        self.assertEqual(trip.state, "ready")
        trip.write({"route_id": False})
        self.assertFalse(trip.generated_route_id)
        self.assertFalse(trip.milestone_ids)

    def test_clearing_the_template_keeps_manually_added_checkpoints(self):
        route = self._route("Manual keeps template", [{"name": "M1"}])
        trip = self._trip(route, tag="CLEARM")
        manual = trip._add_manual_milestone({"name": "Weighbridge stop"})
        trip.write({"route_id": False})
        self.assertFalse(trip.route_id)
        self.assertFalse(trip.generated_route_id)
        self.assertEqual(trip.milestone_ids, manual)
        self.assertFalse(manual.template_line_id)
        self.assertFalse(manual.route_id)
        # Re-selecting a template adds its own checkpoints only.
        trip.write({"route_id": route.id})
        self.assertEqual(
            trip.milestone_ids.mapped("name"),
            ["M1", "Weighbridge stop"],
        )
        self.assertEqual(trip.generated_route_id, route)

    def test_clearing_the_template_is_refused_once_the_trip_left_draft(self):
        route = self._route("Frozen clear template", [{"name": "F1"}])
        cases = (
            ("ready", self._ready_trip),
            ("in_progress", self._started_trip),
        )
        for state, factory in cases:
            trip = factory(route, tag="CLEARFROZEN%s" % state.upper())
            self.assertEqual(trip.state, state)
            with self.assertRaises(UserError) as error, self.env.cr.savepoint():
                trip.write({"route_id": False})
            self.assertIn("Draft", str(error.exception))
            trip.invalidate_recordset()
            self.assertEqual(trip.route_id, route)
            self.assertEqual(trip.generated_route_id, route)
            self.assertEqual(trip.milestone_ids.mapped("name"), ["F1"])

    def test_template_switch_and_clear_are_refused_in_every_later_state(self):
        route_a = self._route("Lifecycle route A", [{"name": "LA1"}])
        route_b = self._route("Lifecycle route B", [{"name": "LB1"}])
        cases = [
            ("ready", self._ready_trip(route_a, tag="LIFECYCLE0")),
            ("in_progress", self._started_trip(route_a, tag="LIFECYCLE1")),
            ("completed", self._completed_trip(route_a, tag="LIFECYCLE2")),
            (
                "cancelled",
                self._cancel_trip(self._ready_trip(route_a, tag="LIFECYCLE3")),
            ),
        ]
        for state, trip in cases:
            self.assertEqual(trip.state, state)
            # A forged context must not reopen the planning window either.
            forged = trip.with_context(
                {
                    "active_test": False,
                    "taj_allow_route_change": True,
                    "allow_planning": True,
                    "sudo": True,
                }
            )
            with self.assertRaises(UserError) as error, self.env.cr.savepoint():
                forged.write({"route_id": route_b.id})
            self.assertIn("Draft", str(error.exception), state)
            with self.assertRaises(UserError) as error, self.env.cr.savepoint():
                forged.write({"route_id": False})
            self.assertIn("Draft", str(error.exception), state)
            trip.invalidate_recordset()
            self.assertEqual(trip.route_id, route_a)
            self.assertEqual(trip.generated_route_id, route_a)
            self.assertEqual(trip.milestone_ids.mapped("name"), ["LA1"])

    def test_generation_and_regeneration_are_draft_only(self):
        route = self._route("Draft only template", [{"name": "D1"}])
        cases = (
            ("ready", self._ready_trip),
            ("in_progress", self._started_trip),
        )
        for state, factory in cases:
            trip = factory(route, tag="DRAFTONLY%s" % state.upper())
            calls = (
                (
                    "generate action",
                    lambda trip=trip: trip.action_generate_milestones(),
                ),
                (
                    "regenerate action",
                    lambda trip=trip: trip._regenerate_milestones_from_route(),
                ),
                (
                    "regenerate wizard action",
                    lambda trip=trip: trip.action_open_regenerate_milestones_wizard(),
                ),
                (
                    "regenerate wizard confirm",
                    lambda trip=trip: (
                        self.env["taj.transport.regenerate.milestones.wizard"]
                        .create({"trip_id": trip.id})
                        .action_regenerate()
                    ),
                ),
                (
                    "private replacement",
                    lambda trip=trip: trip._generate_milestones_from_route(
                        route,
                        replace_pending=True,
                    ),
                ),
                (
                    "private additive generation",
                    lambda trip=trip: trip._generate_milestones_from_route(route),
                ),
            )
            for label, call in calls:
                context = "%s on a %s trip" % (label, state)
                with self.assertRaises(UserError) as error, self.env.cr.savepoint():
                    call()
                self.assertIn("Draft", str(error.exception), context)
                trip.invalidate_recordset()
                self.assertEqual(
                    trip.milestone_ids.mapped("name"),
                    ["D1"],
                    context,
                )

    def test_archived_template_is_refused_on_assignment_and_creation(self):
        route = self._route("Retired template", [{"name": "R1"}])
        trip = self._trip(tag="ARCHIVED1")
        route.write({"active": False})
        with self.assertRaises(ValidationError) as error, self.env.cr.savepoint():
            trip.write({"route_id": route.id})
        self.assertIn("archived", str(error.exception))
        trip.invalidate_recordset()
        self.assertFalse(trip.route_id)
        self.assertFalse(trip.milestone_ids)
        # The same refusal for a dispatcher going through the ordinary client.
        with self.assertRaises(ValidationError) as error, self.env.cr.savepoint():
            trip.with_user(self.dispatcher).write({"route_id": route.id})
        self.assertIn("archived", str(error.exception))
        # And for creation, which must leave no trip behind.
        line = self._sale_line("ARCHIVED2")
        with self.assertRaises(ValidationError) as error, self.env.cr.savepoint():
            self.env["taj.transport.trip"].create(
                {
                    "sale_line_id": line.id,
                    "route_id": route.id,
                }
            )
        self.assertIn("archived", str(error.exception))
        self.assertFalse(
            self.env["taj.transport.trip"].search(
                [
                    ("sale_line_id", "=", line.id),
                ]
            )
        )
        # Direct ORM calls reach the same validation.
        for label, call in (
            ("generation", lambda: trip._generate_milestones_from_route(route)),
            (
                "regeneration",
                lambda: trip._generate_milestones_from_route(
                    route,
                    replace_pending=True,
                ),
            ),
        ):
            with self.assertRaises(ValidationError) as error, self.env.cr.savepoint():
                call()
            self.assertIn("archived", str(error.exception), label)
        trip.invalidate_recordset()
        self.assertFalse(trip.milestone_ids)
        self.assertFalse(trip.generated_route_id)

    def test_archived_template_keeps_snapshots_and_can_still_be_replaced(self):
        route = self._route("Retired in use", [{"name": "A1"}, {"name": "A2"}])
        trip = self._trip(route, tag="ARCHIVED3")
        route.write({"active": False})
        self.assertEqual(trip.milestone_ids.mapped("name"), ["A1", "A2"])
        self.assertEqual(trip.generated_route_id, route)
        # No new checkpoint can be generated from an archived template.
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            trip.action_generate_milestones()
        trip.invalidate_recordset()
        self.assertEqual(trip.milestone_ids.mapped("name"), ["A1", "A2"])
        # A Draft trip is never stuck: it can clear the archived template or
        # select an active one.
        trip.write({"route_id": False})
        self.assertFalse(trip.route_id)
        self.assertFalse(trip.milestone_ids)
        replacement = self._route("Replacement template", [{"name": "B1"}])
        trip.write({"route_id": replacement.id})
        self.assertEqual(trip.milestone_ids.mapped("name"), ["B1"])
        self.assertEqual(trip.generated_route_id, replacement)
        # Re-activating the template makes it usable again.
        route.write({"active": True})
        trip.write({"route_id": route.id})
        self.assertEqual(trip.milestone_ids.mapped("name"), ["A1", "A2"])
        self.assertEqual(trip.generated_route_id, route)

    def test_a_failed_generation_rolls_back_the_template_assignment(self):
        trip = self._trip(tag="ATOMIC")
        manual = trip._add_manual_milestone({"name": "Loading"})
        route = self._route(
            "Clashing template",
            [{"name": "Loading"}, {"name": "Border"}],
        )
        # The generated checkpoint would collide with the manual one, so the
        # whole assignment rolls back: no partial plan, no half-written marker.
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            trip.write({"route_id": route.id})
        trip.invalidate_recordset()
        self.assertFalse(trip.route_id)
        self.assertFalse(trip.generated_route_id)
        self.assertEqual(trip.milestone_ids, manual)
        self.assertEqual(trip.milestone_ids.mapped("name"), ["Loading"])
        self.assertEqual(trip.milestone_count, 1)

    def test_clearing_refuses_history_that_appears_after_the_pre_check(self):
        route = self._route("Backstop template", [{"name": "B1"}])
        trip = self._trip(route, tag="BACKSTOP")
        milestone = self._milestone(trip, "B1")
        # A concurrent writer records an event between the pre-write
        # validation and the removal itself. The removal is the last line of
        # defence and must refuse instead of discarding history.
        self.env["taj.transport.milestone.event"].sudo().create(
            {
                "milestone_id": milestone.id,
                "event_type": "arrival",
                "occurred_at": fields.Datetime.now() - timedelta(hours=1),
            }
        )
        with self.assertRaises(UserError) as error, self.env.cr.savepoint():
            trip._clear_generated_milestones()
        # The removal layer re-validates recorded history right before the
        # removal, so an event that appeared after any earlier pre-check is
        # still refused there.
        self.assertIn("checkpoint history", str(error.exception))
        trip.invalidate_recordset()
        self.assertEqual(trip.route_id, route)
        self.assertEqual(trip.generated_route_id, route)
        self.assertEqual(trip.milestone_ids.mapped("name"), ["B1"])
        # Even if a caller reached the actual unlink past that guard, the
        # unlink backstop refuses on its own.
        with self.assertRaises(UserError) as error, self.env.cr.savepoint():
            trip._discard_generated_pending_milestones()
        self.assertIn("cannot be discarded", str(error.exception))
        trip.invalidate_recordset()
        self.assertEqual(trip.route_id, route)
        # The ordinary client path refuses it one step earlier, with the
        # checkpoint-history message.
        with self.assertRaises(UserError) as error, self.env.cr.savepoint():
            trip.write({"route_id": False})
        self.assertIn("history", str(error.exception))
        trip.invalidate_recordset()
        self.assertEqual(trip.route_id, route)
        self.assertEqual(trip.milestone_ids.mapped("name"), ["B1"])

    def test_generation_marker_is_system_managed(self):
        route = self._route("Marker route", [{"name": "M1"}])
        other_route = self._route("Marker route two", [{"name": "M2"}])
        trip = self._ready_trip(route, tag="MARKER")
        self.assertEqual(trip.generated_route_id, route)
        with self.assertRaises(UserError):
            trip.write({"generated_route_id": other_route.id})
        with self.assertRaises(UserError):
            self.env["taj.transport.trip"].create(
                {
                    "sale_line_id": self._sale_line("MARKER").id,
                    "generated_route_id": other_route.id,
                }
            )
        self.assertEqual(trip.generated_route_id, route)

    def test_trip_refuses_another_company_template(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Phase 3 route company three",
            }
        )
        foreign_route = self.env["taj.transport.route"].create(
            {
                "name": "Foreign route",
                "company_id": other_company.id,
            }
        )
        line = self._sale_line("FOREIGN")
        with (
            self.assert_raises_any(UserError, ValidationError),
            self.env.cr.savepoint(),
        ):
            self.env["taj.transport.trip"].create(
                {
                    "sale_line_id": line.id,
                    "route_id": foreign_route.id,
                }
            )
        self.assertFalse(
            self.env["taj.transport.trip"].search(
                [
                    ("sale_line_id", "=", line.id),
                ]
            )
        )

    def test_templates_and_checkpoints_are_company_isolated(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Phase 3 route company four",
            }
        )
        own_route = self._route("Own company route", [{"name": "OC1"}])
        own_trip = self._trip(own_route, tag="ISOLATION")
        own_milestone = own_trip.milestone_ids
        other_user = new_test_user(
            self.env,
            login="phase3_route_other_dispatcher",
            company_id=other_company.id,
            groups="base.group_user,%s" % DISPATCHER_GROUP,
        )
        # Restrict the test user to the second company only.
        other_user.write({"company_ids": [Command.set(other_company.ids)]})
        # The other company's dispatcher sees neither template nor checkpoints.
        self.assertFalse(
            self.env["taj.transport.route"]
            .with_user(other_user)
            .search([("id", "=", own_route.id)])
        )
        self.assertFalse(
            self.env["taj.transport.milestone"]
            .with_user(other_user)
            .search([("id", "in", own_milestone.ids)])
        )
        with self.assertRaises(AccessError):
            own_route.with_user(other_user).read(["name"])
        with self.assertRaises(AccessError):
            own_milestone.with_user(other_user).read(["name"])
        # The owning company's dispatcher keeps full operational visibility.
        self.assertTrue(
            self.env["taj.transport.route"]
            .with_user(self.dispatcher)
            .search([("id", "=", own_route.id)])
        )
        self.assertTrue(
            self.env["taj.transport.milestone"]
            .with_user(self.dispatcher)
            .search([("id", "in", own_milestone.ids)])
        )
