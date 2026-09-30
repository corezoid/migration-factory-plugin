"""Reads a Simulator graph layer — its actors and the edges between them —
into the flat `tree.File` shape the export tooling works from. Ported from
the Go `internal/graph/pull.go`; nothing here writes a layer back as a whole.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import tree

if TYPE_CHECKING:
    from ..simulator.client import SimulatorClient


def pull_graph(sim: "SimulatorClient", layer_id: str) -> tree.File:
    """Fetches every actor and edge of a layer."""
    server_actors = sim.list_layer_actors(layer_id)
    server_edges = sim.list_layer_edges(layer_id)

    actors = [
        tree.Actor(
            id=sa.id,
            title=sa.title,
            description=sa.description,
            form_id=sa.resolved_form_id(),
            form_title=sa.form_title,
            picture=sa.picture,
            position=tree.Position(x=sa.position.x, y=sa.position.y),
        )
        for sa in server_actors
    ]
    edges = [tree.Edge(source=se.source, target=se.target) for se in server_edges]
    return tree.File(layer_id=layer_id, actors=actors, edges=edges)
