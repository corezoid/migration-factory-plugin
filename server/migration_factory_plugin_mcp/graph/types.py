"""types.schema.yaml's type dictionary, derived from the layer's forms —
ported from the Go `internal/graph/types.go`.

Nothing here is hand-maintained: ResolveTypes reads one Simulator form per
distinct formId on the layer (concurrently) and turns it into a Type with a
derived slug and a FieldSpec per field. A field id comes from the form API,
which is what keeps a write from being refused for a made-up name.

This module is deliberately a line-for-line port of the Go algorithm rather
than a simplification of it: the slugs and types it derives are addressed by
a downstream diff/plan engine, so two different-but-equivalent derivations
would silently break every plan built against a prior export.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from .schema import (
    TYPE_BOOL,
    TYPE_DATE,
    TYPE_INT,
    TYPE_NUMBER,
    TYPE_STRING,
    TYPE_TEXT,
    FieldSpec,
    Type,
    TypeSet,
)

if TYPE_CHECKING:
    from ..simulator.client import SimulatorClient
    from ..simulator.types import Field
    from .tree import Actor

# Caps the parallel form reads an export makes. Eight keeps a 150-node layer
# to a couple of seconds without hammering the gateway.
DEFAULT_CONCURRENCY = 8

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_types(
    sim: "SimulatorClient", actors: "list[Actor]", concurrency: int = DEFAULT_CONCURRENCY
) -> "tuple[TypeSet, list[str]]":
    """Builds the layer's type dictionary: one Simulator form read per
    distinct formId among ``actors``, run concurrently, turned into slugs and
    field specs."""
    # Distinct forms, with the form title the layer read already served (the
    # actor's own form_title, not its instance title) as the fallback name for
    # a form that cannot be read.
    fallback_title: dict[int, str] = {}
    form_ids: list[int] = []
    for a in actors:
        if a.form_id == 0:
            continue
        if a.form_id not in fallback_title:
            form_ids.append(a.form_id)
            fallback_title[a.form_id] = a.form_title
        elif not fallback_title[a.form_id]:
            fallback_title[a.form_id] = a.form_title
    form_ids.sort()

    results: list = []
    if form_ids:
        from ..simulator.actors import FORM_WITH_FIELDS_FILTER

        def _fetch(form_id: int):
            try:
                return sim.get_form(form_id, FORM_WITH_FIELDS_FILTER), None
            except Exception as e:  # noqa: BLE001 - kept as a per-form warning, not a hard failure
                return None, e

        workers = concurrency if concurrency and concurrency > 0 else DEFAULT_CONCURRENCY
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_fetch, form_ids))

    warnings: list[str] = []
    types_list: list = []
    for form_id, (form, err) in zip(form_ids, results):
        t = Type(slug="", form_id=form_id, form_title=fallback_title.get(form_id, ""))
        if err is not None:
            warnings.append(
                f"form {form_id} ({fallback_title.get(form_id, '')}): {err} — type has no field schema"
            )
        else:
            t.form_title = form.title or fallback_title.get(form_id, "")
            t.description = (form.description or "").strip()
            for f in form.fields():
                if not f.id:
                    continue
                t.fields.append(
                    FieldSpec(
                        name=_field_slug(f),
                        id=f.id,
                        type=_field_type(f),
                        title=(f.title or "").strip(),
                        writable=f.visibility != "disabled",
                    )
                )
        _dedupe_field_names(t.fields)
        types_list.append(t)

    _assign_slugs(types_list)
    ts = TypeSet(types=types_list)
    _resolve_refs(ts)
    return ts, warnings


# ---- slug assignment ----


def _assign_slugs(types: "list[Type]") -> None:
    """Names every type from its form title, stripping the prefix the whole
    workspace shares ("DEV_DTO_HRS_EMPLOYEE" -> "hrs_employee") so the slugs
    read as types rather than as installation names."""
    prefix = _common_title_prefix(types)
    taken: set = set()
    for t in types:
        title = t.form_title
        trimmed = title[len(prefix):] if prefix and title.startswith(prefix) else title
        if trimmed:
            title = trimmed
        base = _slugify(title)
        if not base:
            base = f"form_{t.form_id}"
        slug = base
        if slug in taken:
            # Two forms named the same: the id is the only thing that
            # separates them, and it keeps the slug stable across exports.
            slug = f"{base}_{t.form_id}"
        taken.add(slug)
        t.slug = slug


def _common_title_prefix(types: "list[Type]") -> str:
    """The longest "_"-delimited prefix that most of the form titles share.

    It is a majority rather than a universal prefix on purpose: one oddly
    named form is enough to make the prefix shared by *all* titles empty, and
    then every slug carries the installation name it should have dropped.
    """
    if len(types) < 2:
        return ""

    counts: dict[str, int] = {}
    for t in types:
        title = t.form_title
        for i, ch in enumerate(title):
            if ch == "_":
                prefix = title[: i + 1]
                counts[prefix] = counts.get(prefix, 0) + 1

    # Three fifths of the forms, and never fewer than two: a prefix two forms
    # out of fifty happen to share is a coincidence, not a namespace. (Integer
    # division, matching the Go original's len(types)*3/5.)
    threshold = max(2, len(types) * 3 // 5)
    best = ""
    for prefix, n in counts.items():
        if n < threshold:
            continue
        if len(prefix) > len(best) or (len(prefix) == len(best) and prefix < best):
            best = prefix
    return best


# ---- field naming ----


def _field_slug(f: "Field") -> str:
    """The name a write addresses a field by: the field id when it is
    readable, and the title turned into a slug when the id is an opaque
    "item_<digits>" key."""
    if not f.id.startswith("item_") and _IDENTIFIER_RE.match(f.id):
        return f.id
    if s := _slugify(_first_words(f.title, 4)):
        return s
    return _slugify(f.id)


def _first_words(s: str, n: int) -> str:
    words = s.split()
    return " ".join(words[:n])


def _dedupe_field_names(fields: "list[FieldSpec]") -> None:
    """Keeps field slugs unique within a type — two fields whose titles
    slugify the same would otherwise shadow each other."""
    taken: set = set()
    for i, f in enumerate(fields):
        original = f.name
        name = original if original else f"field_{i}"
        n = 2
        while name in taken:
            name = f"{original}_{n}"
            n += 1
        taken.add(name)
        f.name = name


# ---- field typing ----


def _field_type(f: "Field") -> str:
    """Maps a Simulator field onto the graph type vocabulary. An unknown
    widget degrades to string: a write is then validated as text rather than
    refused, which is the right trade for a field nobody has modelled yet."""
    cls = (f.cls or "").lower()
    if cls in ("check", "checkbox", "switch", "toggle"):
        return TYPE_BOOL
    if cls in ("calendar", "date", "datetime", "datepicker"):
        return TYPE_DATE
    if cls in ("select", "radio", "multiselect", "dropdown"):
        opts = _option_values(f)
        if opts:
            return "enum[" + ", ".join(opts) + "]"
        return TYPE_STRING
    if cls in ("actor", "actorlink", "link", "reference"):
        # Filled in by _resolve_refs once every type is known.
        form_id = _extra_form_id(f)
        if form_id > 0:
            return f"ref:{form_id}"
        return TYPE_STRING

    ftype = (f.type or "").lower()
    if ftype in ("int", "integer"):
        return TYPE_INT
    if ftype in ("float", "double", "number", "decimal"):
        return TYPE_NUMBER
    if _is_multiline(f):
        return TYPE_TEXT
    return TYPE_STRING


def _resolve_refs(ts: TypeSet) -> None:
    """Turns the "ref:<formId>" placeholders _field_type leaves into
    ref(<slug>) now that every slug is known. A reference to a form outside
    the layer has no slug to name, so it stays a plain string."""
    for t in ts.types:
        for f in t.fields:
            if not f.type.startswith("ref:"):
                continue
            rest = f.type[len("ref:"):]
            try:
                fid = int(rest)
            except ValueError:
                fid = 0
            target = ts.type_by_form(fid)
            if target is not None:
                f.type = f"ref({target.slug})"
            else:
                f.type = TYPE_STRING


def _go_sprint(v) -> str:
    """A close-enough stand-in for Go's fmt.Sprint(v), for the handful of
    value shapes an option's value takes."""
    if v is None:
        return "<nil>"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return repr(v)
    return str(v)


def _option_values(f: "Field") -> "list[str]":
    out: list = []
    for o in f.options:
        v = _go_sprint(o.value).strip()
        if not v:
            v = (o.title or "").strip()
        if v and "," not in v and "]" not in v:
            out.append(v)
    return out


def _extra_form_id(f: "Field") -> int:
    v = f.extra.get("formId")
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return 0
    return 0


def _is_multiline(f: "Field") -> bool:
    return f.extra.get("multiline") is True


# ---- shared slugify ----


def _slugify(s: str) -> str:
    """Lower-cases and turns any run of non-alphanumeric characters into a
    single "_", trimming a leading/trailing one."""
    out: list = []
    last_underscore = True  # also trims a leading "_"
    for ch in s.strip():
        if ch.isalnum():
            out.append(ch.lower())
            last_underscore = False
        elif not last_underscore:
            out.append("_")
            last_underscore = True
    return "".join(out).strip("_")
