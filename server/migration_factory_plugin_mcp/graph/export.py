"""Projects a Simulator layer into the three files the graph tooling reads:

    graph.values.yaml   the layer: the tree, with every field value under its node
    graph.ids.json       path -> uuid, the only place uuids survive
    types.schema.yaml    the type dictionary the slugs in the tree resolve to

Ported from the Go `internal/graph/export.go`. The split is about what
belongs in front of a reader: the raw layer is mostly uuids, canvas
coordinates and colors, so graph.values.yaml keeps the hierarchy and every
value while the uuids stay in the sidecar and the Simulator field ids in the
schema, where the write path resolves them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import render, schema, tree, valuesfile
from .pull import pull_graph
from .types import DEFAULT_CONCURRENCY, resolve_types
from .values import fetch_values

if TYPE_CHECKING:
    from ..simulator.client import SimulatorClient


@dataclass
class ExportOptions:
    layer_id: str
    # Where the files land, created when it does not exist yet.
    dir: str = "."
    # Caps the parallel form and actor reads. Zero uses DEFAULT_CONCURRENCY.
    concurrency: int = DEFAULT_CONCURRENCY


@dataclass
class ExportResult:
    layer_id: str
    values_path: str
    ids_path: str
    types_path: str

    nodes: int
    edges: int
    types: int
    nodes_with_values: int
    # The anomalies the export resolved rather than failed on: a layer that
    # is not a tree, a form that could not be read, an actor whose values are
    # missing. An export with warnings is still usable — the warnings say
    # which parts of it to distrust.
    warnings: list = field(default_factory=list)


def export_layer(sim: "SimulatorClient", opts: ExportOptions) -> ExportResult:
    file = pull_graph(sim, opts.layer_id)

    built_tree, tree_warnings = tree.build_tree(file)
    if len(built_tree.nodes) == 0:
        # An export of nothing is not harmless: it writes `paths: {}` and
        # `types: {}`, and every apply and listing in that directory then
        # fails blaming the files. The usual cause is a wrong layer id or a
        # key for the wrong workspace, and that is what the reader needs to
        # hear.
        raise ValueError(
            f"layer {opts.layer_id} has no nodes — nothing to export; check the layer id "
            "and that the API key belongs to its workspace"
        )

    types, types_warnings = resolve_types(sim, file.actors, opts.concurrency)

    actor_ids = list(built_tree.order)
    values, values_warnings = fetch_values(sim, actor_ids, opts.concurrency)

    ids_bytes = render.render_ids(built_tree)
    types_bytes = schema.render_types_schema(built_tree.layer_id, types)
    values_bytes = valuesfile.render_values(built_tree, types, values)

    dir_path = opts.dir or "."
    os.makedirs(dir_path, mode=0o750, exist_ok=True)

    values_path = os.path.join(dir_path, valuesfile.VALUES_FILE_NAME)
    ids_path = os.path.join(dir_path, render.IDS_FILE_NAME)
    types_path = os.path.join(dir_path, schema.TYPES_FILE_NAME)

    _write_file(values_path, values_bytes)
    _write_file(ids_path, ids_bytes)
    _write_file(types_path, types_bytes)

    warnings = tree_warnings + types_warnings + values_warnings

    return ExportResult(
        layer_id=built_tree.layer_id,
        values_path=values_path,
        ids_path=ids_path,
        types_path=types_path,
        nodes=len(built_tree.nodes),
        edges=len(file.edges),
        types=len(types.types),
        nodes_with_values=len(values),
        warnings=warnings,
    )


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)
