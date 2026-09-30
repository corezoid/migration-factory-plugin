"""Addressing: any unique suffix of a node's path, separator ``" > "``. The
same PathIndex shape is built either from a live tree (paths.py + tree.py) or
from a prior export's graph.ids.json (render.py) — both produce identical
lookup behavior.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PATH_SEP = " > "
DISCRIMINATOR = "@"


class NoMatchError(Exception):
    """No node matches the given address."""


class AmbiguousMatchError(Exception):
    """More than one node matches — never guessed, the caller must
    disambiguate."""


@dataclass
class PathEntry:
    path: str
    id: str


@dataclass
class PathIndex:
    entries: list[PathEntry] = field(default_factory=list)
    by_path: dict = field(default_factory=dict)
    by_id: dict = field(default_factory=dict)

    @staticmethod
    def build(entries: list[PathEntry]) -> "PathIndex":
        idx = PathIndex(entries=list(entries))
        for e in entries:
            idx.by_path[e.path] = e.id
            idx.by_id[e.id] = e.path
        return idx

    def by_id_lookup(self, node_id: str) -> PathEntry:
        """Direct uuid lookup, no fuzziness — either the node is on the layer
        or it's reported gone (deleted, or belongs to another layer)."""
        path = self.by_id.get(node_id)
        if path is None:
            raise NoMatchError(f"no node with id {node_id} — it may have been deleted, or belongs to another layer")
        return PathEntry(path=path, id=node_id)

    def resolve(self, addr: str) -> PathEntry:
        return _match_path(self.entries, addr)


_HEX_TAIL_RE = re.compile(r"^(.*)@([0-9a-fA-F]{4,})$")
_TYPE_SUFFIX_RE = re.compile(r"^(.*) \[[^\[\]]+\]$")


def _is_hex(s: str) -> bool:
    return len(s) >= 4 and all(c in "0123456789abcdefABCDEF" for c in s)


def _strip_discriminators(path: str) -> str:
    """Removes a trailing ``@<hex>`` from each ``PATH_SEP``-delimited
    segment, where the hex run is >=4 chars — a real ``@`` inside a title
    (rare) is left untouched since a shorter/non-hex tail never matches."""
    segments = path.split(PATH_SEP)
    stripped = []
    for seg in segments:
        m = _HEX_TAIL_RE.match(seg)
        if m and _is_hex(m.group(2)):
            stripped.append(m.group(1))
        else:
            stripped.append(seg)
    return PATH_SEP.join(stripped)


def _without_type_suffix(addr: str) -> str:
    m = _TYPE_SUFFIX_RE.match(addr)
    return m.group(1) if m else addr


def _matches(path: str, addr: str) -> bool:
    return path == addr or path.endswith(PATH_SEP + addr)


def _match_path(entries: list[PathEntry], addr: str) -> PathEntry:
    hits = [e for e in entries if _matches(e.path, addr)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise AmbiguousMatchError(
            f"{addr!r} matches more than one node: " + ", ".join(e.path for e in hits)
        )

    # Retry against every path with its @hex discriminators stripped — catches
    # a writer who typed "Employee #1" not knowing a sibling exists.
    stripped_hits = [e for e in entries if _matches(_strip_discriminators(e.path), addr)]
    if len(stripped_hits) == 1:
        return stripped_hits[0]
    if len(stripped_hits) > 1:
        raise AmbiguousMatchError(
            f"{addr!r} matches more than one node: " + ", ".join(e.path for e in stripped_hits)
        )

    without_suffix = _without_type_suffix(addr)
    if without_suffix != addr:
        raise NoMatchError(
            f"no node matches {addr!r} — the [type] in graph.values.yaml names the node's type, "
            f"not part of its path; address it as {without_suffix!r}"
        )
    raise NoMatchError(f"no node matches {addr!r}")


def path_title(path: str) -> str:
    """Last segment of the discriminator-stripped path — used to detect a
    stale export (compared against the live actor's current title)."""
    stripped = _strip_discriminators(path)
    return stripped.rsplit(PATH_SEP, 1)[-1]


def rename(addr: str, title: str) -> str:
    """Replaces only the last PATH_SEP-delimited segment of addr with title."""
    if PATH_SEP in addr:
        prefix, _ = addr.rsplit(PATH_SEP, 1)
        return prefix + PATH_SEP + title
    return title
