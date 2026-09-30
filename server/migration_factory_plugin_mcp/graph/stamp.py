"""Stamps a resolved node's uuid into graph.ops.yaml as an `id:` line, so a
replayed op is addressed by that id from then on and survives a rename that
moved the paths it was originally written against.

This is a byte-position LINE SPLICE against the ORIGINAL file text, not a
parse-and-re-dump: re-serializing the YAML would keep comments but silently
drop blank lines between ops, and this file is meant to be read side-by-side
with the plan diff. ruamel's round-trip loader is used only to find line/
column positions; the actual edit is a plain text-line insertion.
"""
from __future__ import annotations

import os

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import FoldedScalarString, LiteralScalarString

# Anchor key preference: prefer `at:` (ordinary node op); a record op
# (type:+ref: addressing, no `at:`) tries `type:` then `ref:`.
_ANCHOR_KEYS = ("at", "type", "ref")


def _yaml_rt() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    return y


def _quote_closes_on_line(line: str, start: int, quote: str) -> bool:
    """start points at the opening quote character. For a double quote,
    a backslash escapes the next character (including another `"`); for a
    single quote, a doubled `''` is an escaped literal quote, not a close."""
    i = start + 1
    n = len(line)
    while i < n:
        c = line[i]
        if quote == '"' and c == "\\":
            i += 2
            continue
        if c == quote:
            if quote == "'" and i + 1 < n and line[i + 1] == "'":
                i += 2
                continue
            return True
        i += 1
    return False


def _value_start_col(line: str, key: str, key_col: int) -> int:
    """Column right after `key:` (skipping the colon and following spaces),
    on the raw line — where the value's own text begins."""
    after_key = key_col + len(key)
    colon = line.find(":", after_key)
    if colon == -1:
        return len(line)
    j = colon + 1
    while j < len(line) and line[j] == " ":
        j += 1
    return j


def _one_line_scalar(value, lines: list, key: str, key_line: int, key_col: int) -> bool:
    if isinstance(value, (LiteralScalarString, FoldedScalarString)):
        return False
    line = lines[key_line] if key_line < len(lines) else ""
    start = _value_start_col(line, key, key_col)
    if start >= len(line):
        # Nothing after the colon on this line -> the value lives on a
        # following line (an explicit block/folded scalar, or an indented
        # plain continuation) — never one-line.
        return False
    ch = line[start]
    if ch in ("|", ">"):
        return False
    if ch in ('"', "'"):
        return _quote_closes_on_line(line, start, ch)
    # Plain/bare scalar: reject if the next non-blank, non-comment line is
    # indented deeper than the key itself, which would mean the scalar wraps
    # onto it.
    for next_line in lines[key_line + 1:]:
        stripped = next_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(next_line) - len(next_line.lstrip(" "))
        return indent <= key_col
    return True


def _stamp_anchor(op_node, lines: list) -> "tuple[int, int] | None":
    """Returns (line, column) to anchor the id: insertion under, or None if
    no candidate key qualifies."""
    lc_data = getattr(op_node, "lc", None)
    if lc_data is None:
        return None
    for key in _ANCHOR_KEYS:
        if key not in op_node:
            continue
        pos = op_node.lc.data.get(key)
        if pos is None:
            continue
        key_line, key_col = pos[0], pos[1]
        value = op_node[key]
        if _one_line_scalar(value, lines, key, key_line, key_col):
            return key_line, key_col
    return None


def stamp_ops(path: str, resolved: dict) -> "tuple[int, list]":
    """resolved: {1-based op index: uuid}. Returns (stamped_count, warnings).
    Raises on I/O/parse failure (missing `ops:` key, unreadable file)."""
    with open(path, "rb") as f:
        raw = f.read()
    mode = os.stat(path).st_mode

    text = raw.decode("utf-8")
    lines = text.split("\n")

    y = _yaml_rt()
    doc = y.load(text)
    ops_seq = doc.get("ops") if doc is not None else None
    if ops_seq is None:
        raise ValueError(f"{path}: no `ops:` key — nothing to stamp")

    warnings: list = []
    inserts: list = []  # (line_to_insert_after, indent, uuid)

    for i, op_node in enumerate(ops_seq):
        n = i + 1
        node_id = resolved.get(n)
        if not node_id:
            continue
        if "id" in op_node:
            continue
        anchor = _stamp_anchor(op_node, lines)
        if anchor is None:
            warnings.append(
                f"op #{n} keeps no `id:` — its `at:` is not a plain one-line address "
                "(block scalar, quote spanning lines, or a wrapped plain scalar); "
                "a replay after a rename will not find the node"
            )
            continue
        line, col = anchor
        inserts.append((line, col, node_id))

    # Bottom-up: descending by line number, so an earlier (higher-line)
    # splice never shifts the line number a later (lower-line) splice was
    # computed against.
    inserts.sort(key=lambda t: t[0], reverse=True)
    for line, col, node_id in inserts:
        new_line = " " * col + "id: " + node_id
        lines.insert(line + 1, new_line)

    if inserts:
        new_text = "\n".join(lines)
        with open(path, "wb") as f:
            f.write(new_text.encode("utf-8"))
        os.chmod(path, mode & 0o777)

    return len(inserts), warnings
