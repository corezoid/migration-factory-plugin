"""Answers, for each value about to be written, whether a record of a type
already carries that identity — the check to run BEFORE writing a record,
every time. A layer's canvas only shows the nodes somebody placed on it,
usually one empty placeholder per type per branch; the form behind the type
holds every record, including the ones earlier runs created off the canvas.
Skipping this check is how the same counterparty ends up in the register
three times under three different refs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..simulator.actors import ACTOR_LIST_FILTER
from .schema import FieldSpec, Type, TypeSet
from .values import clean_values

MAX_PROBE_VALUES = 64
PROBE_PAGE_LIMIT = 10

_IDENTITY_MARKER = "identity key"
_TITLE_PROBE = "title"


def is_identity_field(f: FieldSpec) -> bool:
    return _IDENTITY_MARKER in f.title.lower()


@dataclass
class FoundRecord:
    id: str
    ref: str = ""
    title: str = ""
    fields: dict = field(default_factory=dict)


@dataclass
class ValueCheck:
    value: str
    matched_by: str = ""
    records: list = field(default_factory=list)  # list[FoundRecord]
    similar: list = field(default_factory=list)  # list[FoundRecord]
    err: Optional[Exception] = None

    def found(self) -> bool:
        return len(self.records) > 0


@dataclass
class FindResult:
    type_slug: str
    form_id: int
    form_title: str
    probes: list  # list[str] — field names probed, "title" included
    checks: list  # list[ValueCheck]
    warnings: list = field(default_factory=list)

    def tally(self) -> "tuple[int, int, int]":
        found = missing = failed = 0
        for c in self.checks:
            if c.err is not None:
                failed += 1
            elif c.found():
                found += 1
            else:
                missing += 1
        return found, missing, failed


@dataclass
class FindRecordsOptions:
    type: str
    values: list
    fields: list = field(default_factory=list)
    dir: str = "."


def _probe_fields(t: Type, named: list) -> "tuple[list, list]":
    """Returns (probes, warnings), where each probe is a FieldSpec or the
    literal string "title"."""
    warnings: list = []
    if named:
        probes: list = []
        with_title = False
        for name in named:
            if name == _TITLE_PROBE:
                probes.append(_TITLE_PROBE)
                with_title = True
                continue
            match = next((f for f in t.fields if f.name == name), None)
            if match is None:
                raise ValueError(f"field {name!r} not found on type {t.slug!r}")
            if not is_identity_field(match):
                warnings.append(
                    f"field {name!r} is not marked as an identity key — "
                    "two different records may legitimately carry this value"
                )
            probes.append(match)
        if not with_title:
            # Naming any field drops the title probe unless "title" is
            # explicitly among them — but a shadowed same-named field warns.
            shadowed = next((f for f in t.fields if f.name == _TITLE_PROBE), None)
            if shadowed is not None:
                warnings.append(
                    "this type has a field literally named 'title', which is unreachable by this probe "
                    "unless \"title\" is named explicitly among `fields`"
                )
        return probes, warnings

    identity_fields = [f for f in t.fields if is_identity_field(f)]
    if not identity_fields:
        warnings.append(f"type {t.slug!r} marks no field as an identity key — probing by title alone")
    return identity_fields + [_TITLE_PROBE], warnings


def _probe_label(probe) -> str:
    return probe if isinstance(probe, str) else probe.name


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9\s]+$")


def _looks_like_identifier(value: str) -> bool:
    return bool(_IDENTIFIER_RE.match(value)) and any(c.isdigit() for c in value)


def query_candidates(value: str) -> list:
    value = value.strip()
    candidates = [value]
    if _looks_like_identifier(value):
        normalized = re.sub(r"\s+", "", value).upper()
        if normalized != value:
            candidates.append(normalized)
    return candidates


def _same_title(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def _build_record(actor, probed_field_ids: set) -> FoundRecord:
    cleaned = clean_values(dict(actor.data or {}))
    fields = {k: v for k, v in cleaned.items() if k in probed_field_ids}
    return FoundRecord(id=actor.id, ref=actor.ref or "", title=actor.title or "", fields=fields)


def _probe_value(sim, t: Type, workspace_id: str, value: str, probes: list) -> ValueCheck:
    identity_probe_ids = {p.id for p in probes if not isinstance(p, str)}
    try:
        for probe in probes:
            if isinstance(probe, str):
                continue  # title handled after every field probe misses
            for candidate in query_candidates(value):
                actors = sim.list_actors(
                    t.form_id, workspace_id=workspace_id, filter=ACTOR_LIST_FILTER,
                    query=f"{probe.id}={candidate}", limit=PROBE_PAGE_LIMIT,
                )
                if actors:
                    records = [_build_record(a, identity_probe_ids) for a in actors]
                    return ValueCheck(value=value, matched_by=probe.name, records=records)

        if _TITLE_PROBE in probes:
            actors = sim.list_actors(
                t.form_id, workspace_id=workspace_id, filter=ACTOR_LIST_FILTER,
                search=value, limit=PROBE_PAGE_LIMIT,
            )
            exact = [a for a in actors if _same_title(a.title or "", value)]
            similar = [a for a in actors if not _same_title(a.title or "", value)]
            if exact:
                return ValueCheck(
                    value=value, matched_by="title",
                    records=[_build_record(a, identity_probe_ids) for a in exact],
                    similar=[_build_record(a, identity_probe_ids) for a in similar],
                )
            return ValueCheck(
                value=value,
                similar=[_build_record(a, identity_probe_ids) for a in similar],
            )
        return ValueCheck(value=value)
    except Exception as exc:  # noqa: BLE001 - a failed probe is UNKNOWN, not absent
        return ValueCheck(value=value, err=exc)


def _form_workspace(sim, form_id: int) -> "tuple[str, Optional[str]]":
    try:
        form = sim.get_form(form_id, "id,accId,title")
        return getattr(form, "acc_id", "") or "", None
    except Exception as exc:  # noqa: BLE001 - non-fatal, probes still run
        return "", f"could not resolve the workspace for form {form_id}: {exc} — probing with no workspace filter"


def find_records(sim, types: TypeSet, opts: FindRecordsOptions) -> FindResult:
    t = types.by_slug(opts.type)
    if t is None:
        raise ValueError(f"unknown type {opts.type!r} — check types.schema.yaml")
    if len(opts.values) > MAX_PROBE_VALUES:
        raise ValueError(f"{len(opts.values)} values is more than the {MAX_PROBE_VALUES} this call probes at once")

    probes, warnings = _probe_fields(t, opts.fields)
    workspace_id, ws_warning = _form_workspace(sim, t.form_id)
    if ws_warning:
        warnings.append(ws_warning)

    checks = [_probe_value(sim, t, workspace_id, v, probes) for v in opts.values]

    return FindResult(
        type_slug=t.slug, form_id=t.form_id, form_title=t.form_title,
        probes=[_probe_label(p) for p in probes], checks=checks, warnings=warnings,
    )
