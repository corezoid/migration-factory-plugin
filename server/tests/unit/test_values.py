import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.graph import values as values_mod  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Actor  # noqa: E402


# ---------------------------------------------------------------------------
# is_empty_value
# ---------------------------------------------------------------------------


def test_is_empty_value_cases():
    assert values_mod.is_empty_value(None) is True
    assert values_mod.is_empty_value("") is True
    assert values_mod.is_empty_value("   ") is True
    assert values_mod.is_empty_value([]) is True
    assert values_mod.is_empty_value({}) is True

    assert values_mod.is_empty_value("x") is False
    assert values_mod.is_empty_value(0) is False
    assert values_mod.is_empty_value(False) is False
    assert values_mod.is_empty_value(["a"]) is False
    assert values_mod.is_empty_value({"a": 1}) is False


# ---------------------------------------------------------------------------
# clean_values
# ---------------------------------------------------------------------------


def test_clean_values_drops_empty_and_rewrites_multiform_keys():
    data = {
        "plain": "value",
        "blank": "   ",
        "nullish": None,
        "empty_list": [],
        "empty_dict": {},
        "keep_zero": 0,
        "keep_false": False,
        "__form__408962:view": "leaf value",
    }
    cleaned = values_mod.clean_values(data)
    assert cleaned == {
        "plain": "value",
        "keep_zero": 0,
        "keep_false": False,
        "view": "leaf value",
    }


def test_clean_values_empty_input_returns_empty_dict():
    assert values_mod.clean_values({}) == {}
    assert values_mod.clean_values(None) == {}


# ---------------------------------------------------------------------------
# fetch_values
# ---------------------------------------------------------------------------


class _StubSim:
    def __init__(self, actors: dict, raising: "set[str]" = frozenset()):
        self.actors = actors
        self.raising = set(raising)
        self.calls: list = []

    def get_actor_summary(self, actor_id: str):
        self.calls.append(actor_id)
        if actor_id in self.raising:
            raise RuntimeError(f"boom {actor_id}")
        return self.actors[actor_id]


def test_fetch_values_included_empty_and_error_cases():
    sim = _StubSim(
        actors={
            "with-data": Actor(id="with-data", data={"role": "CFO"}),
            "all-empty": Actor(id="all-empty", data={"role": "", "notes": None}),
        },
        raising={"boom-actor"},
    )
    values, warnings = values_mod.fetch_values(sim, ["with-data", "all-empty", "boom-actor"], concurrency=2)

    assert values == {"with-data": {"role": "CFO"}}
    assert "all-empty" not in values
    assert "boom-actor" not in values
    assert len(warnings) == 1
    assert "boom-actor" in warnings[0]
    assert "node rendered without values" in warnings[0]
    assert sorted(sim.calls) == ["all-empty", "boom-actor", "with-data"]


def test_fetch_values_empty_actor_list():
    sim = _StubSim(actors={})
    values, warnings = values_mod.fetch_values(sim, [])
    assert values == {}
    assert warnings == []
