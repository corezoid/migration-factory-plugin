import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import create as create_mod  # noqa: E402
from migration_factory_plugin_mcp.graph import plan as plan_mod  # noqa: E402
from migration_factory_plugin_mcp.graph.ops import Op  # noqa: E402
from migration_factory_plugin_mcp.graph.paths import PathIndex  # noqa: E402
from migration_factory_plugin_mcp.graph.schema import FieldSpec, Type, TypeSet  # noqa: E402
from migration_factory_plugin_mcp.simulator.errors import SimulatorError  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Actor  # noqa: E402


def _idx(types=None):
    return plan_mod.Index(
        layer_id="layer-1",
        paths=PathIndex.build([]),
        types=types or TypeSet(types=[]),
        paths_from="x",
        from_export=False,
        dir="",
        types_from="y",
    )


def _employee_type():
    return Type(
        slug="hrs_employee",
        form_id=701,
        fields=[
            FieldSpec(name="full_name", id="full_name", type="string"),
            FieldSpec(name="edrpou", id="edrpou", type="string"),
        ],
    )


# --------------------------------------------------------- resolve_record_op


def test_resolve_record_op_rejects_at_and_type_together():
    idx = _idx(TypeSet(types=[_employee_type()]))
    op = Op(at="ACME > Finance", type="hrs_employee", ref="X", set={"full_name": "a"})
    with pytest.raises(ValueError, match="both `at:` and `type:`"):
        create_mod.resolve_record_op(idx, op)


def test_resolve_record_op_requires_ref():
    idx = _idx(TypeSet(types=[_employee_type()]))
    op = Op(type="hrs_employee", ref="", set={"full_name": "a"})
    with pytest.raises(ValueError, match="needs a `ref:`"):
        create_mod.resolve_record_op(idx, op)


def test_resolve_record_op_rejects_op_that_does_nothing():
    idx = _idx(TypeSet(types=[_employee_type()]))
    op = Op(type="hrs_employee", ref="X")
    with pytest.raises(ValueError, match="op does nothing"):
        create_mod.resolve_record_op(idx, op)


def test_resolve_record_op_rejects_unknown_slug():
    idx = _idx(TypeSet(types=[]))
    op = Op(type="nope", ref="X", set={"a": "b"})
    with pytest.raises(ValueError, match="no type"):
        create_mod.resolve_record_op(idx, op)


def test_resolve_record_op_rejects_type_with_no_fields():
    idx = _idx(TypeSet(types=[Type(slug="hrs_employee", form_id=701, fields=[])]))
    op = Op(type="hrs_employee", ref="X", set={"a": "b"})
    with pytest.raises(ValueError, match="no field schema"):
        create_mod.resolve_record_op(idx, op)


def test_resolve_record_op_success():
    t = _employee_type()
    idx = _idx(TypeSet(types=[t]))
    op = Op(type="hrs_employee", ref="X", set={"full_name": "a"})
    result = create_mod.resolve_record_op(idx, op)
    assert result.slug == "hrs_employee"


# ------------------------------------------------------------------- find_record


class _StubSim:
    def __init__(self, by_id=None, by_ref=None, id_error=None):
        self.by_id = by_id or {}
        self.by_ref = by_ref or {}
        self.id_error = id_error
        self.get_actor_calls: list = []
        self.get_actor_by_ref_calls: list = []

    def get_actor(self, actor_id, filter=""):
        self.get_actor_calls.append(actor_id)
        if self.id_error is not None:
            raise self.id_error
        return self.by_id[actor_id]

    def get_actor_by_ref(self, form_id, ref, filter=""):
        self.get_actor_by_ref_calls.append((form_id, ref))
        return self.by_ref.get((form_id, ref))


def test_find_record_by_id_returns_actor():
    actor = Actor(id="emp-1", title="Ivan")
    sim = _StubSim(by_id={"emp-1": actor})
    op = Op(id="emp-1", type="hrs_employee", ref="X", set={"full_name": "a"})
    result = create_mod.find_record(sim, _employee_type(), op)
    assert result is actor
    assert sim.get_actor_calls == ["emp-1"]
    assert sim.get_actor_by_ref_calls == []


def test_find_record_by_id_gone_is_a_hard_error():
    sim = _StubSim(id_error=SimulatorError(status_code=404))
    op = Op(id="emp-1", type="hrs_employee", ref="X", set={"full_name": "a"})
    with pytest.raises(ValueError, match="is gone from Simulator"):
        create_mod.find_record(sim, _employee_type(), op)


def test_find_record_by_id_other_error_propagates_unwrapped():
    sim = _StubSim(id_error=SimulatorError(status_code=500, message="boom"))
    op = Op(id="emp-1", type="hrs_employee", ref="X", set={"full_name": "a"})
    with pytest.raises(SimulatorError):
        create_mod.find_record(sim, _employee_type(), op)


def test_find_record_by_ref_found():
    actor = Actor(id="emp-2", title="Petro")
    sim = _StubSim(by_ref={(701, "UA-1"): actor})
    op = Op(type="hrs_employee", ref="UA-1", set={"full_name": "a"})
    result = create_mod.find_record(sim, _employee_type(), op)
    assert result is actor
    assert sim.get_actor_by_ref_calls == [(701, "UA-1")]


def test_find_record_by_ref_not_found_returns_none():
    sim = _StubSim()
    op = Op(type="hrs_employee", ref="UA-nope", set={"full_name": "a"})
    result = create_mod.find_record(sim, _employee_type(), op)
    assert result is None


# ------------------------------------------------------------------- plan_create


def _pc():
    return plan_mod.PlanContext(sim=None, idx=None)


def test_plan_create_every_set_field_is_a_change_against_the_synthetic_empty_actor():
    t = _employee_type()
    op = Op(type="hrs_employee", ref="UA-1", rename="New Counterparty LLC",
            set={"full_name": "New Counterparty LLC", "edrpou": "12345678"})
    action = create_mod.plan_create(_pc(), op, 1, t)
    assert action.create is True
    assert action.path == "ref:UA-1"
    assert action.form_id == 701
    assert action.type == "hrs_employee"
    assert action.ref == "UA-1"
    assert action.rename == "New Counterparty LLC"
    assert {c.field for c in action.changes} == {"full_name", "edrpou"}
    for c in action.changes:
        assert c.old == "—" or c.old == ""  # formatted "empty" old value


def test_plan_create_without_rename_leaves_action_rename_empty():
    # plan_create itself does not synthesize the "no title" warning — that is
    # plan_ops's job, mirroring the Go original where the warning is
    # appended by the caller, not by planCreate.
    t = _employee_type()
    op = Op(type="hrs_employee", ref="UA-1", set={"full_name": "New Counterparty LLC"})
    action = create_mod.plan_create(_pc(), op, 1, t)
    assert action.rename == ""


def test_plan_create_bad_field_value_raises():
    t = Type(slug="hrs_employee", form_id=701, fields=[FieldSpec(name="age", id="age", type="int")])
    op = Op(type="hrs_employee", ref="UA-1", rename="x", set={"age": "not-a-number"})
    with pytest.raises(ValueError, match="not an int"):
        create_mod.plan_create(_pc(), op, 1, t)


# ---------------------------------------------------------- record_path/key


def test_record_path_and_key():
    assert create_mod.record_path("UA-1") == "ref:UA-1"
    assert create_mod.record_key(701, "UA-1") == (701, "UA-1")
