import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import plan as plan_mod  # noqa: E402
from migration_factory_plugin_mcp.graph.schema import FieldSpec, Type  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Actor  # noqa: E402


def _pc():
    return plan_mod.PlanContext(sim=None, idx=None)


def _type():
    # Field order deliberately not alphabetical and not the order a `set:`
    # dict would declare them, so we can prove rendering follows field order.
    return Type(
        slug="employee",
        form_id=2,
        fields=[
            FieldSpec(name="role", id="role", type="string"),
            FieldSpec(name="salary", id="salary", type="number"),
            FieldSpec(name="active", id="active", type="bool"),
        ],
    )


def _state(data=None, **kw):
    return Actor(id="emp-1", data=data or {}, **kw)


# ---------------------------------------------------------------- ordering


def test_plan_set_orders_changes_by_field_order_not_set_order():
    t = _type()
    raw_set = {"active": True, "role": "CFO", "salary": 1000}
    changes = plan_mod.plan_set(_pc(), raw_set, t, _state(), "ACME > Finance > Employee #1")
    assert [c.field for c in changes] == ["role", "salary", "active"]


# ------------------------------------------------------------------ no-op


def test_plan_set_identical_value_produces_no_change():
    t = _type()
    state = _state(data={"role": "CFO"})
    changes = plan_mod.plan_set(_pc(), {"role": "CFO"}, t, state, "p")
    assert changes == []


def test_plan_set_changed_value_produces_a_change():
    t = _type()
    state = _state(data={"role": "Analyst"})
    changes = plan_mod.plan_set(_pc(), {"role": "CFO"}, t, state, "p")
    assert len(changes) == 1
    c = changes[0]
    assert c.field == "role"
    assert c.key == "role"
    assert c.value == "CFO"
    assert c.old == "Analyst"
    assert c.new == "CFO"


# --------------------------------------------------------------- title ban


def test_plan_set_rejects_title_key():
    t = _type()
    with pytest.raises(ValueError, match="do not write `title`"):
        plan_mod.plan_set(_pc(), {"title": "New Name"}, t, _state(), "p")


# --------------------------------------------------------- unknown fields


def test_plan_set_unknown_field_suggests_nearest_by_edit_distance():
    t = _type()
    with pytest.raises(ValueError) as exc_info:
        plan_mod.plan_set(_pc(), {"rle": "CFO"}, t, _state(), "p")
    msg = str(exc_info.value)
    assert "no field" in msg
    assert "rle" in msg
    assert "did you mean" in msg
    assert "role" in msg


def test_plan_set_unknown_field_far_from_any_real_field_gets_no_suggestion():
    t = _type()
    with pytest.raises(ValueError) as exc_info:
        plan_mod.plan_set(_pc(), {"completely_unrelated_name": "x"}, t, _state(), "p")
    msg = str(exc_info.value)
    assert "did you mean" not in msg


# -------------------------------------------------------- multiple problems


def test_plan_set_collects_every_problem_in_one_error():
    t = _type()
    raw_set = {
        "salary": "not-a-number",  # bad value
        "active": "not-a-bool",  # bad value
        "bogus_field": "x",  # unknown field
    }
    with pytest.raises(ValueError) as exc_info:
        plan_mod.plan_set(_pc(), raw_set, t, _state(), "p")
    msg = str(exc_info.value)
    assert "salary" in msg
    assert "active" in msg
    assert "bogus_field" in msg


def test_plan_set_not_writable_field_is_a_problem_too():
    t = Type(slug="employee", form_id=2, fields=[FieldSpec(name="role", id="role", type="string", writable=False)])
    with pytest.raises(ValueError, match="not writable"):
        plan_mod.plan_set(_pc(), {"role": "CFO"}, t, _state(), "p")
