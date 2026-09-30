import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.graph import pull as pull_mod  # noqa: E402
from migration_factory_plugin_mcp.simulator.layers import LayerActor, LayerEdge, LayerPosition  # noqa: E402


class _StubSim:
    def __init__(self, actors: list, edges: list):
        self._actors = actors
        self._edges = edges
        self.actor_layer_ids: list = []
        self.edge_layer_ids: list = []

    def list_layer_actors(self, layer_id: str):
        self.actor_layer_ids.append(layer_id)
        return self._actors

    def list_layer_edges(self, layer_id: str):
        self.edge_layer_ids.append(layer_id)
        return self._edges


def test_pull_graph_maps_actors_and_edges():
    actors = [
        LayerActor(
            id="a1",
            title="Alpha",
            description="first",
            form_id=100,
            form_title="Alpha Form",
            data={},
            picture="pic.png",
            position=LayerPosition(x=10, y=-20),
        ),
        # A multiform actor: resolved_form_id must prefer the leaf form key
        # over the top-level formId.
        LayerActor(
            id="a2",
            title="Beta",
            form_id=200,
            form_title="Beta Form",
            data={"__form__555:view": "x"},
            position=LayerPosition(x=0, y=0),
        ),
    ]
    edges = [LayerEdge(id="e1", source="a1", target="a2")]

    sim = _StubSim(actors, edges)
    file = pull_mod.pull_graph(sim, "layer-1")

    assert sim.actor_layer_ids == ["layer-1"]
    assert sim.edge_layer_ids == ["layer-1"]
    assert file.layer_id == "layer-1"
    assert len(file.actors) == 2

    a1 = file.actors[0]
    assert a1.id == "a1"
    assert a1.title == "Alpha"
    assert a1.description == "first"
    assert a1.form_id == 100  # unaffected by resolved_form_id, no __form__ key
    assert a1.form_title == "Alpha Form"
    assert a1.picture == "pic.png"
    assert a1.position.x == 10
    assert a1.position.y == -20

    a2 = file.actors[1]
    assert a2.form_id == 555  # resolved_form_id() picked the leaf form key
    assert a2.form_title == "Beta Form"

    assert len(file.edges) == 1
    assert file.edges[0].source == "a1"
    assert file.edges[0].target == "a2"
