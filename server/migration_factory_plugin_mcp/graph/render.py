"""graph.ids.json — the path -> uuid sidecar. It consists entirely of tokens
a model cannot use, so it is kept out of context; graph.values.yaml is what a
model reads, this is what apply_graph and find_records resolve addresses
against.
"""
from __future__ import annotations

import json
import os

from .paths import PathEntry, PathIndex
from .tree import Tree

IDS_FILE_NAME = "graph.ids.json"

_SEP = " > "


def render_ids(tree: Tree) -> bytes:
    paths = {tree.nodes[nid].path: nid for nid in tree.order}
    doc = {"layer": tree.layer_id, "sep": _SEP, "paths": paths}
    # Python's json module never HTML-escapes "<"/">"/"&" the way Go's
    # encoding/json does by default, and a plain dict already preserves
    # insertion (walk) order — no hand-serialization needed here.
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def parse_ids(data: bytes) -> "tuple[str, PathIndex]":
    doc = json.loads(data)
    sep = doc.get("sep")
    if sep is not None and sep != _SEP:
        raise ValueError(f"graph.ids.json: unexpected separator {sep!r}, expected {_SEP!r}")
    paths = doc.get("paths") or {}
    if not paths:
        raise ValueError("graph.ids.json: no paths — nothing to resolve addresses against")
    entries = [PathEntry(path=p, id=i) for p, i in paths.items()]
    return doc.get("layer", ""), PathIndex.build(entries)


def load_ids(dir_path: str) -> "tuple[str, PathIndex, bool]":
    """found=False on ENOENT — not an error."""
    path = os.path.join(dir_path, IDS_FILE_NAME)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return "", None, False
    layer_id, idx = parse_ids(data)
    return layer_id, idx, True
