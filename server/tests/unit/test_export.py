import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import export as export_mod  # noqa: E402
from migration_factory_plugin_mcp.graph.render import IDS_FILE_NAME  # noqa: E402
from migration_factory_plugin_mcp.graph.schema import TYPES_FILE_NAME  # noqa: E402
from migration_factory_plugin_mcp.graph.valuesfile import VALUES_FILE_NAME  # noqa: E402
from migration_factory_plugin_mcp.simulator.layers import LayerActor, LayerEdge, LayerPosition  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Actor, Field, Form, Section  # noqa: E402


class _StubSim:
    def __init__(self, actors, edges, forms, actor_data):
        self._actors = actors
        self._edges = edges
        self._forms = forms
        self._actor_data = actor_data

    def list_layer_actors(self, layer_id):
        return self._actors

    def list_layer_edges(self, layer_id):
        return self._edges

    def get_form(self, form_id, filter=""):
        return self._forms[form_id]

    def get_actor_summary(self, actor_id):
        return Actor(id=actor_id, data=self._actor_data.get(actor_id, {}))


def _populated_sim() -> _StubSim:
    actors = [
        LayerActor(
            id="r1", title="Root", form_id=10, form_title="Root Form",
            position=LayerPosition(x=0, y=0),
        ),
        LayerActor(
            id="c1", title="Child One", form_id=20, form_title="Child Form",
            position=LayerPosition(x=-50, y=100),
        ),
        LayerActor(
            id="c2", title="Child Two", form_id=20, form_title="Child Form",
            position=LayerPosition(x=50, y=100),
        ),
    ]
    edges = [
        LayerEdge(id="e1", source="r1", target="c1"),
        LayerEdge(id="e2", source="r1", target="c2"),
    ]
    forms = {
        10: Form(id=10, title="Root Form", sections=[
            Section(content=[Field(id="name", cls="", type="string", title="Name")])
        ]),
        20: Form(id=20, title="Child Form", sections=[
            Section(content=[Field(id="item_1", cls="", type="int", title="Amount")])
        ]),
    }
    actor_data = {
        "r1": {"name": "Root Co"},
        "c1": {"item_1": 100},
        "c2": {"item_1": ""},  # empty after cleaning -> silently omitted
    }
    return _StubSim(actors, edges, forms, actor_data)


def test_export_layer_writes_all_three_files(tmp_path):
    sim = _populated_sim()
    opts = export_mod.ExportOptions(layer_id="layer-xyz", dir=str(tmp_path), concurrency=2)

    result = export_mod.export_layer(sim, opts)

    values_path = tmp_path / VALUES_FILE_NAME
    ids_path = tmp_path / IDS_FILE_NAME
    types_path = tmp_path / TYPES_FILE_NAME
    assert values_path.exists()
    assert ids_path.exists()
    assert types_path.exists()

    assert result.values_path == str(values_path)
    assert result.ids_path == str(ids_path)
    assert result.types_path == str(types_path)

    assert result.layer_id == "layer-xyz"
    assert result.nodes == 3
    assert result.edges == 2
    assert result.types == 2
    assert result.nodes_with_values == 2  # c2's only field cleaned away to nothing

    values_text = values_path.read_text(encoding="utf-8")
    assert "Root Co" in values_text

    ids_text = ids_path.read_text(encoding="utf-8")
    assert "layer-xyz" in ids_text

    types_text = types_path.read_text(encoding="utf-8")
    assert "root_form" in types_text
    assert "child_form" in types_text


def test_export_layer_empty_layer_is_a_hard_error(tmp_path):
    sim = _StubSim([], [], {}, {})
    opts = export_mod.ExportOptions(layer_id="empty-layer", dir=str(tmp_path))

    with pytest.raises(ValueError, match="empty-layer"):
        export_mod.export_layer(sim, opts)
