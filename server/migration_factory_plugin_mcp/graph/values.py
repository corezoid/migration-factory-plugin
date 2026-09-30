"""The field values behind a layer's nodes — ported from the Go
`internal/graph/values.go`.

They do not come with the layer: the paginated layer route serves a trimmed
projection of `data` (one key per node, all null), so the values behind a
node need the actor route. That is one read per node — the reason an export
is concurrent at all.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..simulator.client import SimulatorClient

# actor uuid -> field id -> value. Empty values are dropped, so a node
# missing from the map simply has nothing filled in.
Values = dict

DEFAULT_CONCURRENCY = 8


def is_empty_value(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, list):
        return len(v) == 0
    if isinstance(v, dict):
        return len(v) == 0
    return False


def clean_values(data: dict) -> dict:
    """Drops the empty entries and normalises the keys: a multiform actor
    stores another form's fields under "__form__<formId>:<fieldId>", and the
    field id is what the schema names."""
    out: dict = {}
    for key, value in (data or {}).items():
        if is_empty_value(value):
            continue
        if key.startswith("__form__"):
            rest = key[len("__form__"):]
            i = rest.find(":")
            if i > 0:
                key = rest[i + 1:]
        out[key] = value
    return out


def fetch_values(
    sim: "SimulatorClient", actor_ids: "list[str]", concurrency: int = DEFAULT_CONCURRENCY
) -> "tuple[Values, list[str]]":
    """Reads the values of every actor in the list, concurrently."""
    results: list = []
    if actor_ids:
        def _fetch(actor_id: str):
            try:
                return sim.get_actor_summary(actor_id), None
            except Exception as e:  # noqa: BLE001 - kept as a per-actor warning, not a hard failure
                return None, e

        workers = concurrency if concurrency and concurrency > 0 else DEFAULT_CONCURRENCY
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_fetch, actor_ids))

    warnings: list[str] = []
    values: Values = {}
    for actor_id, (actor, err) in zip(actor_ids, results):
        if err is not None:
            warnings.append(f"actor {actor_id}: {err} — node rendered without values")
            continue
        vals = clean_values(actor.data)
        if vals:
            values[actor_id] = vals
    return values, warnings
