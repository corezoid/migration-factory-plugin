import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.graph import plan as plan_mod  # noqa: E402
from migration_factory_plugin_mcp.graph.ops import MODE_STRICT, Op, OpsFile  # noqa: E402
from migration_factory_plugin_mcp.graph.paths import PathEntry, PathIndex  # noqa: E402
from migration_factory_plugin_mcp.graph.schema import FieldSpec, Type, TypeSet  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Actor  # noqa: E402


class _StubSim:
    def __init__(self, states=None, by_ref=None):
        self.states = states or {}
        self.by_ref = by_ref or {}
        self.get_actor_calls: list = []
        self.get_actor_by_ref_calls: list = []

    def get_actor(self, actor_id, filter=""):
        self.get_actor_calls.append(actor_id)
        return self.states[actor_id]

    def get_actor_by_ref(self, form_id, ref, filter=""):
        self.get_actor_by_ref_calls.append((form_id, ref))
        return self.by_ref.get((form_id, ref))


def _make_idx(paths):
    entries = [PathEntry(path=p, id=i) for p, i in paths]
    types = TypeSet(types=[
        Type(
            slug="hrs_employee",
            form_id=2,
            fields=[
                FieldSpec(name="role", id="role", type="string"),
                FieldSpec(name="full_name", id="full_name", type="string"),
            ],
        )
    ])
    return plan_mod.Index(
        layer_id="layer-1",
        paths=PathIndex.build(entries),
        types=types,
        paths_from="x",
        from_export=False,
        dir="",
        types_from="y",
    )


# ------------------------------------------------------------- ordinary op


def test_ordinary_at_op_diffs_against_state():
    idx = _make_idx([("ACME > Finance > Employee #1", "emp-1")])
    sim = _StubSim(states={"emp-1": Actor(id="emp-1", title="Ivan", data={"role": "Analyst"}, form_id=2)})
    ops = OpsFile(ops=[Op(at="Employee #1", set={"role": "CFO"}, rename="Ivan Petrov")])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert plan.ok(), plan.errors
    assert len(plan.actions) == 1
    a = plan.actions[0]
    assert a.path == "ACME > Finance > Employee #1"
    assert a.rename == "Ivan Petrov"
    assert a.changes[0].field == "role"
    assert a.changes[0].new == "CFO"
    assert plan.resolved[1] == "emp-1"


def test_ambiguous_match_is_a_plan_error():
    idx = _make_idx([("ACME > IT > Widget", "w1"), ("ACME > HR > Widget", "w2")])
    sim = _StubSim()
    ops = OpsFile(ops=[Op(at="Widget", rename="Thing")])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert not plan.ok()
    assert len(plan.errors) == 1
    assert "matches more than one node" in plan.errors[0]


def test_not_found_match_is_a_plan_error():
    idx = _make_idx([("ACME > Finance > Employee #1", "emp-1")])
    sim = _StubSim()
    ops = OpsFile(ops=[Op(at="ACME > Bad Path", rename="X")])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert not plan.ok()
    assert "op #1" in plan.errors[0]
    assert "no node matches" in plan.errors[0]


def test_id_overrides_at_even_when_at_is_present():
    idx = _make_idx([("ACME > Finance > Employee #1", "emp-1")])
    sim = _StubSim(states={"emp-1": Actor(id="emp-1", title="Ivan", data={}, form_id=2)})
    ops = OpsFile(ops=[Op(id="emp-1", at="a path that does not exist at all", rename="Ivan Petrov")])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert plan.ok(), plan.errors
    assert plan.actions[0].path == "ACME > Finance > Employee #1"


def test_rename_retry_resolves_a_node_already_renamed_since_the_export():
    idx = _make_idx([("ACME > Finance > Ivan", "emp-3")])
    sim = _StubSim(states={"emp-3": Actor(id="emp-3", title="Ivan", data={}, form_id=2)})
    # `at:` still names the pre-rename title; the retry against Rename(at, rename)
    # is what finds the node.
    ops = OpsFile(ops=[Op(at="ACME > Finance > Ivan Petrov", rename="Ivan", set={"role": "CFO"})])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert plan.ok(), plan.errors
    assert plan.resolved[1] == "emp-3"
    assert len(plan.actions) == 1
    # The rename itself is already applied (title already "Ivan"); only the
    # `set:` change is left to write.
    assert plan.actions[0].rename == ""
    assert plan.actions[0].changes[0].new == "CFO"


def test_rename_retry_not_used_when_ambiguous_error_is_not_no_match():
    idx = _make_idx([("ACME > IT > Widget", "w1"), ("ACME > HR > Widget", "w2")])
    sim = _StubSim()
    # Ambiguous, not NoMatch: never retried, surfaces directly.
    ops = OpsFile(ops=[Op(at="Widget", rename="Thing")])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert not plan.ok()
    assert "matches more than one node" in plan.errors[0]


# --------------------------------------------------------------- record ops


def test_record_op_not_found_queues_a_create():
    idx = _make_idx([])
    sim = _StubSim()
    ops = OpsFile(ops=[Op(type="hrs_employee", ref="UA-1", rename="New Co", set={"full_name": "New Co"})])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert plan.ok(), plan.errors
    assert len(plan.actions) == 1
    assert plan.actions[0].create is True
    assert plan.actions[0].path == "ref:UA-1"
    assert 1 not in plan.resolved  # no actor id exists yet


def test_record_op_found_queues_an_update():
    idx = _make_idx([])
    found = Actor(id="rec-1", title="Old Name", data={"full_name": "Old Name"}, form_id=2, ref="UA-2")
    sim = _StubSim(by_ref={(2, "UA-2"): found})
    ops = OpsFile(ops=[Op(type="hrs_employee", ref="UA-2", set={"full_name": "New Name"})])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert plan.ok(), plan.errors
    assert len(plan.actions) == 1
    a = plan.actions[0]
    assert a.create is False
    assert a.actor_id == "rec-1"
    assert a.changes[0].new == "New Name"
    assert plan.resolved[1] == "rec-1"
    # The record's own state (from get_actor_by_ref) is reused; no redundant
    # get_actor round trip for the same id.
    assert sim.get_actor_calls == []


def test_strict_mode_does_not_create():
    idx = _make_idx([])
    sim = _StubSim()
    ops = OpsFile(mode=MODE_STRICT, ops=[Op(type="hrs_employee", ref="UA-3", set={"full_name": "X"})])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert not plan.ok()
    assert "does not exist" in plan.errors[0]
    assert plan.actions == []


def test_ref_reused_twice_in_one_file_is_a_dedup_error():
    idx = _make_idx([])
    sim = _StubSim()
    ops = OpsFile(
        ops=[
            Op(type="hrs_employee", ref="UA-4", set={"full_name": "A"}),
            Op(type="hrs_employee", ref="UA-4", set={"full_name": "B"}),
        ]
    )

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert len(plan.errors) == 1
    assert "op #2" in plan.errors[0]
    assert "already claims ref" in plan.errors[0]
    # The first op is unaffected and still queued as a create.
    assert len(plan.actions) == 1
    assert plan.actions[0].ref == "UA-4"


# ------------------------------------------------------------ satisfied ops


def test_fully_satisfied_op_lands_in_satisfied_not_actions():
    idx = _make_idx([("ACME > Finance > Employee #1", "emp-1")])
    sim = _StubSim(states={"emp-1": Actor(id="emp-1", title="Ivan", data={"role": "CFO"}, form_id=2)})
    ops = OpsFile(ops=[Op(at="Employee #1", set={"role": "CFO"})])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert plan.ok(), plan.errors
    assert plan.actions == []
    assert len(plan.satisfied) == 1
    assert plan.satisfied[0].op_index == 1
    assert plan.satisfied[0].path == "ACME > Finance > Employee #1"


def test_picture_already_present_is_a_warning_not_a_write():
    idx = _make_idx([("ACME > Finance > Employee #1", "emp-1")])
    sim = _StubSim(
        states={
            "emp-1": Actor(id="emp-1", title="Ivan", data={}, form_id=2, picture="existing.png"),
        }
    )
    ops = OpsFile(ops=[Op(at="Employee #1", picture="https://example.com/new.jpg")])

    plan = plan_mod.plan_ops(sim, idx, ops)

    assert plan.ok(), plan.errors
    assert plan.actions == []
    assert len(plan.satisfied) == 1
    assert any("already carries a picture" in w for w in plan.warnings)
