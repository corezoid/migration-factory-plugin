"""The entity endpoints the graph tools call: read an actor by id or by
external ref, list the actors of a form, create one, write one back, and read
the form behind it.

The listing is the odd one out: it reads a form's records rather than a
layer's nodes, which is the only way to answer whether an actor already
exists when nothing put it on a graph.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

from .errors import SimulatorError, is_not_found
from .types import Actor, Form, decode_item, decode_list

if TYPE_CHECKING:
    from .client import _ClientCore

# ActorListFilter is the projection for a listing: what identifies an actor
# and what it holds, and none of its form schema. An unfiltered listing
# returns the form template once per row.
ACTOR_LIST_FILTER = "id,title,ref,status,data,formId,updatedAt"

# ActorSummaryFilter is a sane projection for reading one actor: everything a
# caller usually wants and nothing of the form schema. Without a filter the
# gateway returns the actor's whole form template twice plus the full access
# list — tens of thousands of tokens.
ACTOR_SUMMARY_FILTER = "id,title,description,status,data,formId,formTitle,ref,ownerId,createdAt,updatedAt"

# FormWithFieldsFilter is the projection to use when the fields are wanted:
# the sections live under the top-level `form` key, so a filter naming
# `sections` silently drops them.
FORM_WITH_FIELDS_FILTER = "id,title,description,status,type,color,form"

# The backend caps a page at 200 and silently clamps anything larger, so
# asking for more in one request buys nothing.
ACTOR_PAGE_LIMIT = 200

actor_uuid_re = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def validate_actor_id(actor_id: str) -> None:
    """Rejects a malformed or shortened actor id before the request goes out.
    The backend answers 403 Access Denied for a non-UUID id, which reads as a
    permissions problem and is not one."""
    if not actor_id:
        raise ValueError("simulator: actor id is required")
    if not actor_uuid_re.match(actor_id):
        raise ValueError(
            f"simulator: actor id {actor_id!r} is not a full UUID (8-4-4-4-12) — "
            "the backend would answer a misleading 403 for it"
        )


def _filter_query(filter: str) -> Optional[dict]:
    if not filter:
        return None
    return {"filter": filter}


class ActorsMixin:
    def create_actor(
        self: "_ClientCore",
        form_id: int,
        data: Optional[dict],
        *,
        title: str = "",
        description: str = "",
        ref: str = "",
        picture: str = "",
    ) -> Actor:
        """POST /actors/actor/{form_id}.

        data is keyed by the form's field ids ("item_<digits>" — not their
        titles), and each value's shape follows that field's class; read the
        form with get_form first.

        In a form-tree (UAT) workspace form_id must be the ROOT form:
        creating under a form that has a parent fails with 400
        "Form <id> is not UAT".
        """
        if form_id <= 0:
            raise ValueError("simulator: CreateActor needs a form id")
        body: dict[str, Any] = {"data": data or {}}
        if title:
            body["title"] = title
        if description:
            body["description"] = description
        if ref:
            body["ref"] = ref
        if picture:
            body["picture"] = picture
        payload = self._call("POST", f"/actors/actor/{form_id}", body=body)
        return Actor.from_json(decode_item(payload))

    def get_actor_by_ref(self: "_ClientCore", form_id: int, ref: str, filter: str = "") -> Optional[Actor]:
        """GET /actors/ref/{form_id}/{ref} — the lookup by external business
        key instead of UUID.

        A missing actor comes back as a 404, told apart here from a real
        failure and turned into None: that distinction is what lets a create
        op ask "does this record exist yet" without a journal.
        """
        if form_id <= 0 or not ref:
            raise ValueError("simulator: GetActorByRef needs a form id and a ref")
        try:
            payload = self._get(f"/actors/ref/{form_id}/{self._seg(ref)}", _filter_query(filter))
        except SimulatorError as e:
            if is_not_found(e):
                return None
            raise
        return Actor.from_json(decode_item(payload))

    def get_actor(self: "_ClientCore", actor_id: str, filter: str = "") -> Actor:
        """GET /actors/{actor_id}.

        filter is the server-side projection: a comma-separated field list,
        empty for everything. Read one actor with ACTOR_SUMMARY_FILTER unless
        the form schema is genuinely wanted — an unfiltered read returns the
        whole template twice over.
        """
        validate_actor_id(actor_id)
        payload = self._get(f"/actors/{self._seg(actor_id)}", _filter_query(filter))
        return Actor.from_json(decode_item(payload))

    def patch_actor(
        self: "_ClientCore",
        form_id: int,
        actor_id: str,
        *,
        data: Optional[dict] = None,
        title: str = "",
        description: str = "",
        ref: str = "",
        picture: str = "",
        hole: Optional[bool] = None,
    ) -> Actor:
        """PUT /actors/actor/{form_id}/{actor_id} with replaceEmpty=false: the
        keys named in data are written and every other stored value is left
        alone — merge, not replace.

        hole is tri-state: only included in the payload if not None (a nil
        pointer in the Go original leaves the flag alone).
        """
        if form_id <= 0:
            raise ValueError("simulator: PatchActor needs the actor's form id")
        validate_actor_id(actor_id)
        body: dict[str, Any] = {}
        if data:
            body["data"] = data
        if title:
            body["title"] = title
        if description:
            body["description"] = description
        if ref:
            body["ref"] = ref
        if picture:
            body["picture"] = picture
        if hole is not None:
            body["hole"] = hole
        payload = self._call(
            "PUT",
            f"/actors/actor/{form_id}/{self._seg(actor_id)}",
            query={"replaceEmpty": "false"},
            body=body,
        )
        return Actor.from_json(decode_item(payload))

    def get_form(self: "_ClientCore", form_id: int, filter: str = "") -> Form:
        """GET /forms/{form_id} — the field dictionary a node's data is keyed
        by. Pass FORM_WITH_FIELDS_FILTER to keep the sections."""
        if form_id <= 0:
            raise ValueError("simulator: GetForm needs a form id")
        payload = self._get(f"/forms/{form_id}", _filter_query(filter))
        return Form.from_json(decode_item(payload))

    def get_actor_summary(self: "_ClientCore", actor_id: str) -> Actor:
        """Reads an actor with ACTOR_SUMMARY_FILTER — everything about the
        actor itself and none of its form schema."""
        return self.get_actor(actor_id, ACTOR_SUMMARY_FILTER)

    def list_actors(
        self: "_ClientCore",
        form_id: int,
        *,
        workspace_id: str = "",
        filter: str = "",
        search: str = "",
        query: str = "",
        limit: int = 0,
    ) -> list[Actor]:
        """GET /actors_filters/{form_id} — the actors of one form, filtered,
        ordered and paged.

        This is the records side of a form, as opposed to the layer side: it
        answers with every actor of the form, whether or not anything placed
        it on a graph. Reads exactly one page, no pagination loop.
        """
        if form_id <= 0:
            raise ValueError("simulator: ListActors needs a form id")

        q: dict[str, str] = {}
        if filter.strip():
            q["filter"] = filter.strip()
        if search.strip():
            q["search"] = search.strip()
        if query.strip():
            q["q"] = query.strip()
        if workspace_id.strip():
            q["accId"] = workspace_id.strip()
        if limit > 0:
            q["limit"] = str(min(limit, ACTOR_PAGE_LIMIT))

        payload = self._get(f"/actors_filters/{form_id}", q or None)
        return [Actor.from_json(item) for item in decode_list(payload)]
