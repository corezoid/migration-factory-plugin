import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import plan as plan_mod  # noqa: E402
from migration_factory_plugin_mcp.graph.paths import PathEntry, PathIndex  # noqa: E402
from migration_factory_plugin_mcp.graph.schema import FieldSpec, Type, TypeSet  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Actor  # noqa: E402


class _StubSim:
    def __init__(self, actors: dict):
        self.actors = actors
        self.calls: list = []

    def get_actor(self, actor_id: str, filter: str = ""):
        self.calls.append(actor_id)
        return self.actors[actor_id]


def _pc(idx=None, sim=None):
    return plan_mod.PlanContext(sim=sim, idx=idx)


def _field(name="f", ftype="string", writable=True, fid=None):
    return FieldSpec(name=name, id=fid or name, type=ftype, writable=writable)


# --------------------------------------------------------------- clearing


@pytest.mark.parametrize("raw", [None, "", "   ", [], {}])
def test_coerce_value_clearing_is_a_hard_error(raw):
    pc = _pc()
    with pytest.raises(ValueError, match="clearing a field is not supported"):
        plan_mod.coerce_value(pc, _field(ftype="string"), raw)


# --------------------------------------------------------------------- int


def test_coerce_int_accepts_plain_int():
    assert plan_mod.coerce_value(_pc(), _field(ftype="int"), 5) == 5


def test_coerce_int_accepts_whole_float():
    assert plan_mod.coerce_value(_pc(), _field(ftype="int"), 5.0) == 5


def test_coerce_int_rejects_fractional_float():
    with pytest.raises(ValueError, match="not a whole number"):
        plan_mod.coerce_value(_pc(), _field(ftype="int"), 5.5)


def test_coerce_int_accepts_numeric_string():
    assert plan_mod.coerce_value(_pc(), _field(ftype="int"), " 42 ") == 42


def test_coerce_int_rejects_unparseable_string():
    with pytest.raises(ValueError, match="not an int"):
        plan_mod.coerce_value(_pc(), _field(ftype="int"), "abc")


def test_coerce_int_rejects_bool():
    with pytest.raises(ValueError, match="not an int"):
        plan_mod.coerce_value(_pc(), _field(ftype="int"), True)


# ------------------------------------------------------------------ number


def test_coerce_number_accepts_int_float_and_string():
    assert plan_mod.coerce_value(_pc(), _field(ftype="number"), 3) == 3.0
    assert plan_mod.coerce_value(_pc(), _field(ftype="number"), 3.5) == 3.5
    assert plan_mod.coerce_value(_pc(), _field(ftype="number"), "3.5") == 3.5


def test_coerce_number_rejects_unparseable_string():
    with pytest.raises(ValueError, match="not a number"):
        plan_mod.coerce_value(_pc(), _field(ftype="number"), "abc")


# -------------------------------------------------------------------- bool


def test_coerce_bool_accepts_bool_and_lenient_strings():
    assert plan_mod.coerce_value(_pc(), _field(ftype="bool"), True) is True
    assert plan_mod.coerce_value(_pc(), _field(ftype="bool"), "true") is True
    assert plan_mod.coerce_value(_pc(), _field(ftype="bool"), "FALSE") is False


def test_coerce_bool_rejects_other_strings():
    with pytest.raises(ValueError, match="not a bool"):
        plan_mod.coerce_value(_pc(), _field(ftype="bool"), "maybe")


# -------------------------------------------------------------------- date


def test_coerce_date_accepts_date_object():
    d = datetime.date(2024, 3, 1)
    assert plan_mod.coerce_value(_pc(), _field(ftype="date"), d) == "2024-03-01"


def test_coerce_date_accepts_plain_string():
    assert plan_mod.coerce_value(_pc(), _field(ftype="date"), "2024-03-01") == "2024-03-01"


def test_coerce_date_accepts_rfc3339_string():
    assert plan_mod.coerce_value(_pc(), _field(ftype="date"), "2024-03-01T10:15:00Z") == "2024-03-01"
    assert plan_mod.coerce_value(_pc(), _field(ftype="date"), "2024-03-01T10:15:00") == "2024-03-01"


def test_coerce_date_rejects_garbage():
    with pytest.raises(ValueError, match="is not a date"):
        plan_mod.coerce_value(_pc(), _field(ftype="date"), "not a date")


# -------------------------------------------------------------------- enum


def test_coerce_enum_accepts_a_listed_option():
    f = _field(ftype="enum[CFO, CEO, Analyst]")
    assert plan_mod.coerce_value(_pc(), f, "CEO") == "CEO"


def test_coerce_enum_rejects_unlisted_value_and_lists_options():
    f = _field(ftype="enum[CFO, CEO, Analyst]")
    with pytest.raises(ValueError) as exc_info:
        plan_mod.coerce_value(_pc(), f, "Intern")
    msg = str(exc_info.value)
    assert "Intern" in msg
    assert "CFO" in msg and "CEO" in msg and "Analyst" in msg


# --------------------------------------------------------------------- ref


def _ref_setup():
    target_actor = Actor(id="emp-1", form_id=2, title="Ivan Petrov")
    sim = _StubSim({"emp-1": target_actor})
    paths = PathIndex.build([PathEntry(path="ACME > Finance > Ivan Petrov", id="emp-1")])
    types = TypeSet(types=[Type(slug="employee", form_id=2, fields=[])])
    idx = plan_mod.Index(
        layer_id="layer-1", paths=paths, types=types,
        paths_from="x", from_export=False, dir="", types_from="y",
    )
    return sim, idx, target_actor


def test_coerce_ref_resolves_path_to_id_and_title():
    sim, idx, target_actor = _ref_setup()
    pc = _pc(idx=idx, sim=sim)
    f = _field(ftype="ref(employee)")
    value = plan_mod.coerce_value(pc, f, "ACME > Finance > Ivan Petrov")
    assert value == {"id": "emp-1", "title": "Ivan Petrov"}
    assert sim.calls == ["emp-1"]


def test_coerce_ref_requires_a_string():
    sim, idx, _ = _ref_setup()
    pc = _pc(idx=idx, sim=sim)
    f = _field(ftype="ref(employee)")
    with pytest.raises(ValueError, match="write the target's path"):
        plan_mod.coerce_value(pc, f, 123)


def test_coerce_ref_target_of_wrong_type_is_an_error():
    # Point the ref field at a type the resolved target does not belong to.
    target_actor = Actor(id="emp-1", form_id=2, title="Ivan Petrov")
    sim = _StubSim({"emp-1": target_actor})
    paths = PathIndex.build([PathEntry(path="ACME > Finance > Ivan Petrov", id="emp-1")])
    types = TypeSet(types=[
        Type(slug="employee", form_id=2, fields=[]),
        Type(slug="company", form_id=1, fields=[]),
    ])
    idx = plan_mod.Index(
        layer_id="layer-1", paths=paths, types=types,
        paths_from="x", from_export=False, dir="", types_from="y",
    )
    pc = _pc(idx=idx, sim=sim)
    f = _field(ftype="ref(company)")
    with pytest.raises(ValueError) as exc_info:
        plan_mod.coerce_value(pc, f, "ACME > Finance > Ivan Petrov")
    msg = str(exc_info.value)
    assert "ref(company)" in msg
    assert "[employee]" in msg


def test_coerce_ref_caches_state_reads_on_the_plan_context():
    sim, idx, _ = _ref_setup()
    pc = _pc(idx=idx, sim=sim)
    f = _field(ftype="ref(employee)")
    plan_mod.coerce_value(pc, f, "ACME > Finance > Ivan Petrov")
    plan_mod.coerce_value(pc, f, "ACME > Finance > Ivan Petrov")
    # The second lookup is served from pc.states, not a second round trip.
    assert sim.calls == ["emp-1"]


# ------------------------------------------------------------ string/text


def test_coerce_string_stringifies_scalars():
    f = _field(ftype="string")
    assert plan_mod.coerce_value(_pc(), f, "hello") == "hello"
    assert plan_mod.coerce_value(_pc(), f, 7) == "7"
    assert plan_mod.coerce_value(_pc(), f, True) == "true"
    assert plan_mod.coerce_value(_pc(), f, 3.5) == "3.5"


def test_coerce_text_stringifies_scalars_too():
    f = _field(ftype="text")
    assert plan_mod.coerce_value(_pc(), f, "hello") == "hello"


def test_coerce_string_rejects_list_and_dict():
    f = _field(ftype="string")
    with pytest.raises(ValueError, match="cannot be written"):
        plan_mod.coerce_value(_pc(), f, ["a", "b"])
    with pytest.raises(ValueError, match="cannot be written"):
        plan_mod.coerce_value(_pc(), f, {"a": 1})
