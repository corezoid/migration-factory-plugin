"""The graph-layer read endpoints: the two halves of a layer — its nodes
(actors placed on the canvas) and its edges (the links between them) — both
served by the paginated /graph_layers/paginated/{layer_id} route under a
`type` switch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from .actors import actor_uuid_re
from .types import decode_list, resolved_form_id

if TYPE_CHECKING:
    from .client import _ClientCore

# The route pages, and a layer routinely holds more nodes than one page.
# MAX_LAYER_PAGES caps the walk — ten thousand items — so a route that
# ignores offset and serves its first page forever ends in an error rather
# than a spin.
LAYER_PAGE_LIMIT = 50
MAX_LAYER_PAGES = 200

# Layer listing types — the `type` query parameter of the paginated route.
_LAYER_TYPE_NODES = "nodes"
_LAYER_TYPE_EDGES = "edges"

T = TypeVar("T")


def _validate_layer_id(layer_id: str) -> None:
    """A layer is an actor, so it carries the same full UUID — and the same
    misleading 403 when it is shortened."""
    if not layer_id:
        raise ValueError("simulator: layer id is required")
    if not actor_uuid_re.match(layer_id):
        raise ValueError(f"simulator: layer id {layer_id!r} is not a full UUID (8-4-4-4-12)")


@dataclass
class LayerPosition:
    """A node's coordinate on the layer canvas. The backend stores some
    positions as fractions (e.g. -1543.58) and others as integers, so both
    are accepted and a fraction is rounded to the nearest pixel."""

    x: int = 0
    y: int = 0

    @classmethod
    def from_json(cls, d: dict) -> "LayerPosition":
        return cls(x=round(float(d.get("x", 0) or 0)), y=round(float(d.get("y", 0) or 0)))


@dataclass
class LayerActor:
    """One node of a layer: the actor plus its placement."""

    id: str
    title: str = ""
    description: str = ""
    form_id: int = 0
    # form_title is the name of the node's form, served with the node — the
    # only place a layer read gives one, and enough to name a type without a
    # form lookup.
    form_title: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    # picture is the image the node is drawn with, as a path in the
    # workspace's storage.
    picture: str = ""
    position: LayerPosition = field(default_factory=LayerPosition)

    @classmethod
    def from_json(cls, d: dict) -> "LayerActor":
        return cls(
            id=d.get("id", "") or "",
            title=d.get("title", "") or "",
            description=d.get("description", "") or "",
            form_id=d.get("formId", 0) or 0,
            form_title=d.get("formTitle", "") or "",
            data=d.get("data") or {},
            picture=d.get("picture", "") or "",
            position=LayerPosition.from_json(d.get("position") or {}),
        )

    def resolved_form_id(self) -> int:
        """The form id to use for API calls on this node. In a form-tree
        (UAT) workspace the node's own data is keyed by the leaf form, and
        that leaf id — not the top-level form_id, which names the root — is
        what the actor routes expect."""
        return resolved_form_id(self.data, self.form_id)


@dataclass
class LayerEdge:
    """One link of a layer."""

    id: str
    source: str = ""
    target: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "LayerEdge":
        return cls(id=d.get("id", "") or "", source=d.get("source", "") or "", target=d.get("target", "") or "")


def _list_layer_items(client: "_ClientCore", layer_id: str, item_type: str, from_json: Callable[[dict], T]) -> list[T]:
    """Walks the paginated layer route until a short page ends it."""
    _validate_layer_id(layer_id)
    all_items: list[T] = []
    for n in range(MAX_LAYER_PAGES):
        query = {"type": item_type, "limit": str(LAYER_PAGE_LIMIT), "offset": str(n * LAYER_PAGE_LIMIT)}
        payload = client._get(f"/graph_layers/paginated/{client._seg(layer_id)}", query)
        page = decode_list(payload)
        all_items.extend(from_json(item) for item in page)
        if len(page) < LAYER_PAGE_LIMIT:
            return all_items
    raise RuntimeError(
        f"simulator: layer {layer_id} listing did not end after {MAX_LAYER_PAGES} pages — the route is not paging"
    )


class LayersMixin:
    def list_layer_actors(self: "_ClientCore", layer_id: str) -> list[LayerActor]:
        """Returns every node of a layer, walking the pages of
        GET /graph_layers/paginated/{layer_id}?type=nodes."""
        return _list_layer_items(self, layer_id, _LAYER_TYPE_NODES, LayerActor.from_json)

    def list_layer_edges(self: "_ClientCore", layer_id: str) -> list[LayerEdge]:
        """Returns every edge of a layer, walking the pages of
        GET /graph_layers/paginated/{layer_id}?type=edges."""
        return _list_layer_items(self, layer_id, _LAYER_TYPE_EDGES, LayerEdge.from_json)
