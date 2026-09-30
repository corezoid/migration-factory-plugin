"""graph.ops.yaml — the file a run writes to describe what should land on a
layer. Every op is an overwrite, so an unchanged file replays as
"already applied" once planned against the live layer (see plan.py).

Unknown keys are a hard parse error, deliberately: a misspelled `sett:` must
not silently vanish.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ruamel.yaml import YAML

OPS_FILE_NAME = "graph.ops.yaml"

MODE_UPSERT = "upsert"
MODE_STRICT = "strict"
_VALID_MODES = (MODE_UPSERT, MODE_STRICT)

_OP_KEYS = {"id", "at", "set", "rename", "describe", "picture", "type", "ref", "under", "create", "append"}
_UNROUTED_KEYS = {"text", "source", "guess", "reason"}
_FILE_KEYS = {"layer", "source_doc", "mode", "ops", "unrouted"}


@dataclass
class Op:
    id: str = ""
    at: str = ""
    set: dict = field(default_factory=dict)
    rename: str = ""
    describe: str = ""
    picture: str = ""
    type: str = ""
    ref: str = ""
    # Not supported by this implementation (mirrors the Go original, which
    # keeps these fields only so the planner can explain why an op using them
    # was rejected, rather than failing on an "unknown field").
    under: str = ""
    create: str = ""
    append: dict = field(default_factory=dict)


@dataclass
class Unrouted:
    text: str = ""
    source: str = ""
    guess: str = ""
    reason: str = ""


@dataclass
class OpsFile:
    layer: str = ""
    source_doc: str = ""
    mode: str = MODE_UPSERT
    ops: list = field(default_factory=list)  # list[Op]
    unrouted: list = field(default_factory=list)  # list[Unrouted]


def _reject_unknown_keys(d: dict, known: set, where: str) -> None:
    unknown = set(d.keys()) - known
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {sorted(unknown)!r} — check for a typo")


def parse_ops(data: bytes) -> OpsFile:
    y = YAML(typ="safe", pure=True)
    doc = y.load(data) or {}
    if not isinstance(doc, dict):
        raise ValueError("graph.ops.yaml must be a mapping at the top level")
    _reject_unknown_keys(doc, _FILE_KEYS, "graph.ops.yaml")

    mode = doc.get("mode") or MODE_UPSERT
    if mode not in _VALID_MODES:
        raise ValueError(f"mode {mode!r} is not one of {_VALID_MODES!r}")

    ops: list = []
    for i, raw_op in enumerate(doc.get("ops") or [], start=1):
        raw_op = raw_op or {}
        if not isinstance(raw_op, dict):
            raise ValueError(f"op #{i}: must be a mapping")
        _reject_unknown_keys(raw_op, _OP_KEYS, f"op #{i}")
        ops.append(
            Op(
                id=str(raw_op.get("id", "") or ""),
                at=str(raw_op.get("at", "") or ""),
                set=dict(raw_op.get("set") or {}),
                rename=str(raw_op.get("rename", "") or ""),
                describe=str(raw_op.get("describe", "") or ""),
                picture=str(raw_op.get("picture", "") or ""),
                type=str(raw_op.get("type", "") or ""),
                ref=str(raw_op.get("ref", "") or ""),
                under=str(raw_op.get("under", "") or ""),
                create=str(raw_op.get("create", "") or ""),
                append=dict(raw_op.get("append") or {}),
            )
        )

    unrouted: list = []
    for i, raw_u in enumerate(doc.get("unrouted") or [], start=1):
        raw_u = raw_u or {}
        if not isinstance(raw_u, dict):
            raise ValueError(f"unrouted #{i}: must be a mapping")
        _reject_unknown_keys(raw_u, _UNROUTED_KEYS, f"unrouted #{i}")
        unrouted.append(
            Unrouted(
                text=str(raw_u.get("text", "") or ""),
                source=str(raw_u.get("source", "") or ""),
                guess=str(raw_u.get("guess", "") or ""),
                reason=str(raw_u.get("reason", "") or ""),
            )
        )

    return OpsFile(
        layer=str(doc.get("layer", "") or ""),
        source_doc=str(doc.get("source_doc", "") or ""),
        mode=mode,
        ops=ops,
        unrouted=unrouted,
    )


def load_ops(path: str) -> OpsFile:
    with open(path, "rb") as f:
        return parse_ops(f.read())
