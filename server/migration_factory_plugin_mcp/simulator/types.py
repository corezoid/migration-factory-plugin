"""Shared envelope decoding and the entity dataclasses that carry an actor's
form (Form/Section/Field/FieldOption) and the actor itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------- envelopes ----------
#
# The gateway wraps every payload: a single entity comes back as
# {"data": {...}}, a collection as {"data": [...]} plus an optional stats
# object when the caller asked for totals.


def decode_item(payload: dict) -> dict:
    return payload.get("data") or {}


def decode_list(payload: dict) -> list:
    """Accepts the two shapes the gateway returns a collection in: a bare
    array under "data" (the form listings) and an object under "data"
    carrying the rows in "list" (the actor listings)."""
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("list") or []
    return []


# ---------- shared form-id resolution ----------

_FORM_KEY_RE = re.compile(r"^__form__(\d+):")


def resolved_form_id(data: dict, form_id: int) -> int:
    """Reads the leaf form out of an actor's data keys, falling back to the
    form the record names. In a form-tree (UAT) workspace a node's own data is
    keyed by the leaf form ("__form__408962:view"), and that leaf id — not the
    top-level formId, which names the root — is what the actor routes expect.
    """
    for key in data:
        m = _FORM_KEY_RE.match(key)
        if m:
            candidate = int(m.group(1))
            if candidate > 0:
                return candidate
    return form_id


# ---------- actors ----------


@dataclass
class Actor:
    """One graph node: an instance of a form, whose field values live in
    data. Which fields arrive depends on the projection the read asked for —
    an unrequested field is simply zero here."""

    id: str
    acc_id: str = ""
    form_id: int = 0
    form_title: str = ""
    title: str = ""
    description: str = ""
    ref: str = ""
    color: str = ""
    picture: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    hole: bool = False
    created_at: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "Actor":
        return cls(
            id=d.get("id", "") or "",
            acc_id=d.get("accId", "") or "",
            form_id=d.get("formId", 0) or 0,
            form_title=d.get("formTitle", "") or "",
            title=d.get("title", "") or "",
            description=d.get("description", "") or "",
            ref=d.get("ref", "") or "",
            color=d.get("color", "") or "",
            picture=d.get("picture", "") or "",
            data=d.get("data") or {},
            hole=bool(d.get("hole", False)),
            created_at=str(d.get("createdAt", "") or ""),
        )

    def resolved_form_id(self) -> int:
        return resolved_form_id(self.data, self.form_id)


# ---------- forms ----------


@dataclass
class FieldOption:
    """One choice of a radio, select or multiSelect field."""

    title: str = ""
    value: Any = None
    color: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "FieldOption":
        return cls(title=d.get("title", "") or "", value=d.get("value"), color=d.get("color", "") or "")


@dataclass
class Field:
    """One input in a section. id is the stable "item_<digits>" key an
    actor's data uses; cls (json "class") picks the widget and therefore the
    value shape."""

    id: str = ""
    key: str = ""
    cls: str = ""
    type: str = ""
    title: str = ""
    value: Any = None
    options: list[FieldOption] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    visibility: str = ""
    color: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "Field":
        return cls(
            id=d.get("id", "") or "",
            key=d.get("key", "") or "",
            cls=d.get("class", "") or "",
            type=d.get("type", "") or "",
            title=d.get("title", "") or "",
            value=d.get("value"),
            options=[FieldOption.from_json(o) for o in (d.get("options") or [])],
            extra=d.get("extra") or {},
            description=d.get("description", "") or "",
            visibility=d.get("visibility", "") or "",
            color=d.get("color", "") or "",
        )


@dataclass
class Section:
    """One group of fields in a form."""

    id: str = ""
    title: str = ""
    content: list[Field] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict) -> "Section":
        return cls(
            id=d.get("id", "") or "",
            title=d.get("title", "") or "",
            content=[Field.from_json(f) for f in (d.get("content") or [])],
        )


@dataclass
class Form:
    """A form template (the product calls it an Account Template): the field
    structure actors of this form instantiate."""

    id: int
    acc_id: str = ""
    title: str = ""
    description: str = ""
    ref: str = ""
    type: str = ""
    color: str = ""
    picture: str = ""
    sections: list[Section] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict) -> "Form":
        # A form's sections can arrive either at the top level or nested
        # under form.sections; support both transparently.
        sections_raw = d.get("sections") or []
        if not sections_raw:
            nested = d.get("form") or {}
            sections_raw = nested.get("sections") or []
        return cls(
            id=d.get("id", 0) or 0,
            acc_id=d.get("accId", "") or "",
            title=d.get("title", "") or "",
            description=d.get("description", "") or "",
            ref=d.get("ref", "") or "",
            type=d.get("type", "") or "",
            color=d.get("color", "") or "",
            picture=d.get("picture", "") or "",
            sections=[Section.from_json(s) for s in sections_raw],
        )

    def fields(self) -> list[Field]:
        """Flattens all sections' content in order — the dictionary an
        actor's data is keyed by."""
        result: list[Field] = []
        for section in self.sections:
            result.extend(section.content)
        return result
