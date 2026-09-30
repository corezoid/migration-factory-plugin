"""The diff/plan engine: works out what an ops file (graph.ops.yaml) would do
to a layer, without writing anything. Ported from the Go
`internal/graph/plan.go`.

Two-pass design: pass 1 resolves every op's target (by path or by ref)
without reading node state; pass 2 batch-reads state for every distinct actor
id needed, then computes the diff/action per op. This is what lets a plan of
a 200-op file cost one round trip per distinct node instead of one per op.

circular import note: create.py needs plan.py's Action/plan_set/etc, and
plan_ops() needs create.py's resolve_record_op/find_record/plan_create.
create.py does `from . import plan as plan_mod` at module top level (safe —
this module never imports create.py at ITS top level). This module imports
create.py's three functions with a LOCAL import statement inside plan_ops()
itself, so the cycle never actually closes at import time.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from .paths import (
    AmbiguousMatchError,
    NoMatchError,
    PATH_SEP,
    DISCRIMINATOR,
    PathEntry,
    path_title,
)
from .paths import rename as rename_path
from .pictures import validate_picture_url
from .schema import (
    TYPE_BOOL,
    TYPE_DATE,
    TYPE_INT,
    TYPE_NUMBER,
    TYPE_STRING,
    TYPE_TEXT,
)
from .valuesfile import _format_value as format_value
from .types import _go_sprint

# actor_state_filter is the projection the write path reads a node with: its
# stored values and description, the form they are keyed by, and whether the
# node is still an empty placeholder. It is read live, per addressed node,
# because "already applied" is what lets an ops file be replayed without a
# journal of what ran before.
ACTOR_STATE_FILTER = "id,title,description,data,formId,formTitle,hole,ref,picture"

# dateLayout is how a date field is written. The graph vocabulary has one
# date type and no time zone, so a date is a calendar day and nothing else.
_DATE_LAYOUT_PY = "%Y-%m-%d"
_DATE_LAYOUT_DISPLAY = "2006-01-02"  # shown in error text, matching the Go original's literal

_DEFAULT_CONCURRENCY = 10

# planValueWidth caps a value in the diff. graph.values.yaml truncates
# nothing because it is the only copy of the data; a plan is a summary, and a
# 2 000-character description would bury the ten changes around it.
_PLAN_VALUE_WIDTH = 72


# --------------------------------------------------------------------- data


@dataclass
class Change:
    field: str  # the schema slug (FieldSpec.name)
    key: str  # the actual storage key in `data`
    value: object  # the coerced value to write
    old: str  # formatted OLD value, for the diff
    new: str  # formatted NEW value, for the diff


@dataclass
class Action:
    op_index: int
    actor_id: str = ""
    form_id: int = 0
    path: str = ""  # display path — node path, or "ref:<ref>" for a record op
    create: bool = False
    type: str = ""  # type slug, meaningful for create/record ops
    ref: str = ""
    rename: str = ""
    describe: str = ""
    picture: str = ""
    stored_picture: str = ""  # filled in later by apply.py after uploading
    fills_hole: bool = False
    changes: list = field(default_factory=list)  # list[Change]

    # Not part of the prose spec, but present on the Go Action struct and
    # required to render the "old" side of a rename/describe diff line: the
    # node's CURRENT title/description, as read at plan time.
    title: str = ""
    description: str = ""

    def data(self) -> dict:
        return {c.key: c.value for c in self.changes}

    def writes(self) -> bool:
        return bool(
            self.create or self.rename or self.describe or self.ref or self.stored_picture or self.changes
        )

    def mark(self) -> str:
        if self.create:
            return "*"
        if self.fills_hole:
            return "+"
        if self.changes:
            return "~"
        return ">"


@dataclass
class Satisfied:
    op_index: int
    path: str = ""


@dataclass
class Index:
    layer_id: str
    paths: "object"  # PathIndex
    types: "object"  # TypeSet
    paths_from: str
    from_export: bool
    dir: str
    types_from: str
    warnings: list = field(default_factory=list)


@dataclass
class Plan:
    layer_id: str
    source_doc: str
    paths_from: str
    types_from: str
    actions: list = field(default_factory=list)  # list[Action]
    satisfied: list = field(default_factory=list)  # list[Satisfied]
    errors: list = field(default_factory=list)  # list[str], each a fully-rendered "op #N ..." block
    resolved: dict = field(default_factory=dict)  # 1-based op index -> resolved actor id
    warnings: list = field(default_factory=list)
    unrouted: list = field(default_factory=list)  # OpsFile.unrouted, passed straight through

    def ok(self) -> bool:
        return len(self.errors) == 0

    def creates(self) -> int:
        return sum(1 for a in self.actions if a.create)

    # ------------------------------------------------------------- render

    def render(self) -> str:
        out = []
        header = "plan for layer " + self.layer_id
        if self.source_doc:
            header += "  (source: " + self.source_doc + ")"
        out.append(header)
        out.append("\n  " + self._summary() + "\n")
        if self.paths_from:
            out.append("  paths from " + self.paths_from + "\n")
        if self.types_from:
            out.append("  field schemas from " + self.types_from + "\n")
        for w in self.warnings:
            out.append("  ! " + w + "\n")

        for a in self.actions:
            out.append("\n  " + a.mark() + " " + a.path + "  [" + a.type + "]")
            if a.create:
                out.append("  (new record of form " + str(a.form_id) + ", not on the layer)")
            elif a.fills_hole:
                out.append("  (fills a placeholder hole)")
            out.append("\n")
            if a.rename:
                out.append("      title: " + _shorten(a.title) + " -> " + _shorten(a.rename) + "\n")
            if a.describe:
                out.append(
                    "      description: "
                    + _shorten(_one_line(a.description))
                    + " -> "
                    + _shorten(_one_line(a.describe))
                    + "\n"
                )
            if a.ref and not a.create:
                out.append("      ref: — -> " + _shorten(a.ref) + "\n")
            if a.picture:
                out.append("      picture: " + _shorten(a.picture) + "\n")
            for c in a.changes:
                out.append("      " + c.field + ": " + _shorten(c.old) + " -> " + _shorten(c.new) + "\n")

        if self.creates() > 0:
            out.append(
                "\n  a new record is created as an actor of its form and is not placed on the\n"
                "  canvas: it will not appear in graph.values.yaml, and `ref:` plus the id stamped\n"
                "  into the ops file are the only handles on it. Nothing here undoes a create.\n"
            )

        if self.satisfied:
            out.append("\n  already applied — the layer holds these values:\n")
            for s in self.satisfied:
                out.append("      " + s.path + "\n")

        if self.errors:
            out.append("\n  " + _plural(len(self.errors), "error", "errors") + ":\n")
            for e in self.errors:
                out.append("    " + e + "\n")

        if self.unrouted:
            out.append(
                "\n  unrouted — "
                + _plural(len(self.unrouted), "fact", "facts")
                + " matched no node; this is where the model has holes:\n"
            )
            for u in self.unrouted:
                out.append("      " + _shorten(_one_line(u.text)) + "\n")
                if u.guess or u.reason:
                    out.append("        guess: " + _first_non_empty(u.guess, "-"))
                    if u.reason:
                        out.append(" — " + _one_line(u.reason))
                    out.append("\n")

        return "".join(out)

    def _summary(self) -> str:
        writes, renames, describes, holes, pictures = 0, 0, 0, 0, 0
        for a in self.actions:
            if a.picture:
                # Counted for creates too: a record arriving with a face is
                # the same write as a node getting one.
                pictures += 1
            if a.create:
                continue
            if a.changes:
                writes += 1
            if a.rename:
                renames += 1
            if a.describe:
                describes += 1
            if a.fills_hole:
                holes += 1

        parts: list = []

        def add(n: int, one: str, many: str) -> None:
            if n > 0:
                parts.append(_plural(n, one, many))

        add(self.creates(), "record to create", "records to create")
        add(writes, "node to update", "nodes to update")
        add(renames, "rename", "renames")
        add(describes, "description", "descriptions")
        add(holes, "hole to fill", "holes to fill")
        add(pictures, "picture to take", "pictures to take")
        add(len(self.satisfied), "op already applied", "ops already applied")
        add(len(self.errors), "error", "errors")
        if not parts:
            return "nothing to do"
        return ", ".join(parts)


# --------------------------------------------------------------- rendering


def _q(s: str) -> str:
    """A close-enough stand-in for Go's strconv.Quote/%q: a double-quoted,
    backslash-escaped string, keeping printable unicode intact."""
    return json.dumps(s, ensure_ascii=False)


def _shorten(s: str) -> str:
    """Renders a value for the diff: an unset value as an em dash, a long one
    cut to _PLAN_VALUE_WIDTH."""
    if s == "":
        return "—"
    if len(s) > _PLAN_VALUE_WIDTH:
        s = s[:_PLAN_VALUE_WIDTH] + "…"
    return _q(s)


def _one_line(s: str) -> str:
    return " ".join(s.split())


def _first_non_empty(*vs: str) -> str:
    for v in vs:
        if v.strip() != "":
            return v
    return ""


def _plural(n: int, one: str, many: str) -> str:
    if n == 1:
        return "1 " + one
    return str(n) + " " + many


# ------------------------------------------------------------------ index


def load_index(sim, layer_id: str, dir: str = "", concurrency: int = _DEFAULT_CONCURRENCY) -> Index:
    """Builds the index an ops file is planned against: the paths it
    addresses and the types its field names resolve to.

    dir is the export the ops were written against. When it holds both
    sidecars (graph.ids.json + types.schema.yaml), neither the layer nor the
    forms are read at all — otherwise both are read live. Node state itself
    is never taken from the export: it is always read live, per addressed
    node, at plan time.
    """
    if dir:
        idx = _load_index_from_export(layer_id, dir)
        if idx is not None:
            return idx

    from . import pull as pull_mod
    from . import tree as tree_mod
    from . import types as types_mod

    file = pull_mod.pull_graph(sim, layer_id)
    built_tree, tree_warnings = tree_mod.build_tree(file)
    path_idx = tree_mod.path_index_from_tree(built_tree)
    type_set, type_warnings = types_mod.resolve_types(sim, file.actors, concurrency)
    return Index(
        layer_id=layer_id,
        paths=path_idx,
        types=type_set,
        paths_from="the layer, read now",
        from_export=False,
        dir=dir or "",
        types_from="the form API",
        warnings=tree_warnings + type_warnings,
    )


def _load_index_from_export(layer_id: str, dir: str) -> "Optional[Index]":
    from . import render as render_mod
    from . import schema as schema_mod

    ids_layer_id, path_idx, found_ids = render_mod.load_ids(dir)
    if not found_ids:
        return None
    type_set, found_types = schema_mod.load_types_schema(dir)
    if not found_types:
        return None
    if ids_layer_id != layer_id:
        raise ValueError(
            f"graph: {os.path.join(dir, render_mod.IDS_FILE_NAME)} describes layer {ids_layer_id}, "
            f"applying to {layer_id}"
        )
    return Index(
        layer_id=layer_id,
        paths=path_idx,
        types=type_set,
        paths_from=(
            f"{os.path.join(dir, render_mod.IDS_FILE_NAME)} "
            f"({_plural(len(path_idx.entries), 'node', 'nodes')})"
        ),
        from_export=True,
        dir=dir,
        types_from=(
            f"{os.path.join(dir, schema_mod.TYPES_FILE_NAME)} "
            f"({_plural(len(type_set.types), 'type', 'types')})"
        ),
        warnings=[],
    )


# ------------------------------------------------------------ plan context


@dataclass
class PlanContext:
    """Carries what planning one op needs beyond the op itself: the index,
    and a reader for actors the plan turns out to need — the target of a ref
    field, whose type has to be checked before the reference is written."""

    sim: object
    idx: Index
    states: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def state(self, actor_id: str):
        """Reads an actor, once. The addressed nodes are already in the map
        from the batch read; a ref target usually is not, and refs are rare
        enough that one extra round trip beats a second planning pass."""
        if actor_id in self.states:
            return self.states[actor_id]
        a = self.sim.get_actor(actor_id, ACTOR_STATE_FILTER)
        self.states[actor_id] = a
        return a


# ------------------------------------------------------------------- ops


def unsupported_vocabulary(op) -> None:
    """Rejects the op shapes this version cannot carry out, whichever way
    the op is addressed."""
    if op.under or op.create:
        raise ValueError(
            "`under:`/`create:` is not supported: the layer edge endpoint is not wired, so a "
            "created node would sit on the canvas unlinked from its parent. Add the node in "
            "Simulator (or as a hole) and address it with `at:`, or give the fact a record of its "
            "own with `type:` and `ref:`"
        )
    if op.append:
        raise ValueError("`append:` is not supported: use `set:` with the full field value")


def resolve_op(idx: Index, op) -> PathEntry:
    """Turns an op into the node it addresses, rejecting the op shapes this
    version cannot carry out."""
    unsupported_vocabulary(op)
    if not op.id and not op.at:
        raise ValueError("op has no address: give it an `at:` path or an `id:`")
    if not op.set and not op.rename and not op.describe and not op.picture:
        raise ValueError("op does nothing: no `set:`, `rename:`, `describe:` or `picture:`")

    # A stamped op is addressed by uuid and the path is not consulted: `at:`
    # is what the node was called when the file was written, and the whole
    # point of the stamp is that it may not be called that any more.
    if op.id:
        return idx.paths.by_id_lookup(op.id)

    try:
        return idx.paths.resolve(op.at)
    except NoMatchError as e:
        if not op.rename:
            raise
        # A rename moves the node's address, because the title is the last
        # segment of the path. An ops file replayed after its own rename
        # addresses a node that no longer answers to that name, so the name
        # it renames to is tried as well.
        try:
            return idx.paths.resolve(rename_path(op.at, op.rename))
        except Exception:
            raise e


def _rename_collision(idx: Index, path: str, rename: str) -> str:
    """Reports a rename that gives a node the title a sibling already
    carries. Not an error — the layer allows it — but it changes the address
    of the sibling as well."""
    if not rename:
        return ""
    sibling = rename_path(path, rename)
    if sibling == path or sibling not in idx.paths.by_path:
        return ""
    return (
        f"renaming {path} to {_q(rename)} joins a sibling of that name — both take an "
        f"{_q(DISCRIMINATOR)} discriminator in the next export, changing the address of each"
    )


def _op_address(op) -> str:
    from .create import record_path

    if op.at:
        return op.at
    if op.type and op.ref:
        return record_path(op.ref) + " [" + op.type + "]"
    if op.id:
        return op.id
    if op.under:
        return op.under + PATH_SEP + op.create
    return ""


def plan_ops(sim, idx: Index, ops, concurrency: int = _DEFAULT_CONCURRENCY) -> Plan:
    """Works out what an ops file would do, without writing anything.

    It reads the current values of every node an op addresses: a plan that
    does not know what is stored cannot tell a write from a no-op, and
    telling them apart is what lets the same ops file be replayed instead of
    journalled.
    """
    from .create import find_record, plan_create, record_key, resolve_record_op

    if idx is None or ops is None:
        raise ValueError("graph: plan_ops needs an index and an ops file")
    if ops.layer and ops.layer != idx.layer_id:
        raise ValueError(f"graph: ops file targets layer {ops.layer}, applying to {idx.layer_id}")

    plan = Plan(
        layer_id=idx.layer_id,
        source_doc=ops.source_doc,
        paths_from=idx.paths_from,
        types_from=idx.types_from,
        warnings=list(idx.warnings),
        unrouted=list(ops.unrouted),
    )

    def record_error(n: int, op, message: str) -> None:
        at = _op_address(op)
        line = f"op #{n}"
        if at:
            line += "  " + _q(at)
        line += "\n      " + message.replace("\n", "\n      ")
        plan.errors.append(line)

    class _Target:
        __slots__ = ("op", "n", "entry", "create")

        def __init__(self, op, n, entry, create=None):
            self.op = op
            self.n = n
            self.entry = entry
            self.create = create

    targets: list = []
    seeded: dict = {}
    claimed: dict = {}

    # A record op is addressed by ref, which the batch read below cannot
    # express, so it needs its own lookup — but a file can carry hundreds of
    # them (a bulk import into one form), not just a handful beside the path
    # addresses, so this still runs concurrently: one pass resolves each op's
    # type and dedups its ref (cheap, no I/O), then every find_record call is
    # fired off together instead of waited on one at a time.
    lookups: list = []  # [(n, op, t), ...] in file order
    type_by_n: dict = {}  # n -> Type, or the Exception resolving it raised
    conflict_by_n: dict = {}  # n -> (prev op #, type slug) when a ref is claimed twice

    for i, op in enumerate(ops.ops):
        n = i + 1
        if not op.type:
            continue
        try:
            t = resolve_record_op(idx, op)
        except Exception as e:  # noqa: BLE001 - collected as a plan error, not a hard failure
            type_by_n[n] = e
            continue

        key = record_key(t.form_id, op.ref)
        if key in claimed:
            conflict_by_n[n] = (claimed[key], t.slug)
            continue
        claimed[key] = n
        type_by_n[n] = t
        lookups.append((n, op, t))

    found_by_n: dict = {}  # n -> (actor or None, exception or None)
    if lookups:
        def _one(item: tuple) -> tuple:
            n, op, t = item
            try:
                return n, find_record(sim, t, op), None
            except Exception as e:  # noqa: BLE001 - captured per-op, not raised
                return n, None, e

        workers = concurrency if concurrency and concurrency > 0 else _DEFAULT_CONCURRENCY
        workers = min(workers, len(lookups))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for n, actor, err in ex.map(_one, lookups):
                found_by_n[n] = (actor, err)

    from .create import record_path
    from .ops import MODE_STRICT

    for i, op in enumerate(ops.ops):
        n = i + 1

        if op.type:
            if n in conflict_by_n:
                prev, slug = conflict_by_n[n]
                record_error(
                    n,
                    op,
                    f"op #{prev} already claims ref {_q(op.ref)} on type {_q(slug)} — a ref is "
                    "unique per form, so these are the same record written twice",
                )
                continue

            t_or_err = type_by_n[n]
            if isinstance(t_or_err, Exception):
                record_error(n, op, str(t_or_err))
                continue
            t = t_or_err

            actor, err = found_by_n[n]
            if err is not None:
                record_error(n, op, str(err))
                continue

            if actor is None and ops.mode == MODE_STRICT:
                record_error(
                    n, op, f"{record_path(op.ref)} does not exist and mode is {MODE_STRICT}, which never creates"
                )
            elif actor is None:
                targets.append(_Target(op, n, PathEntry(path=record_path(op.ref), id=""), create=t))
            else:
                # The record is already there — an earlier run made it, or
                # somebody made it by hand. From here it is an ordinary
                # update, which is what makes the file replayable.
                seeded[actor.id] = actor
                plan.resolved[n] = actor.id
                targets.append(_Target(op, n, PathEntry(id=actor.id, path=record_path(op.ref))))
            continue

        try:
            entry = resolve_op(idx, op)
        except Exception as e:  # noqa: BLE001
            record_error(n, op, str(e))
            continue
        plan.resolved[n] = entry.id
        targets.append(_Target(op, n, entry))

    ids: list = []
    seen: set = set()
    for t in targets:
        if not t.entry.id or t.entry.id in seen or t.entry.id in seeded:
            continue
        seen.add(t.entry.id)
        ids.append(t.entry.id)

    states, read_errs = _fetch_states(sim, ids, concurrency)
    for actor_id, actor in seeded.items():
        states[actor_id] = actor

    pc = PlanContext(sim=sim, idx=idx, states=states)

    for t in targets:
        if t.create is not None:
            try:
                action = plan_create(pc, t.op, t.n, t.create)
            except Exception as e:  # noqa: BLE001
                record_error(t.n, t.op, str(e))
                continue
            if not action.rename:
                # Not an error — a record is legitimate without one — but an
                # untitled actor is what a person scrolling the form's
                # records cannot tell from any other.
                plan.warnings.append(
                    f"{action.path} is created without a `rename:`, so it has no title to be "
                    "recognised by"
                )
            plan.actions.append(action)
            continue

        state = states.get(t.entry.id)
        if state is None:
            err = read_errs.get(t.entry.id)
            record_error(
                t.n,
                t.op,
                f"{t.entry.path}: {err} — cannot tell a write from a no-op without the stored values",
            )
            continue

        # Both checks below read the address as a path, so they are for the
        # ops that have one: a record addressed by ref is not on the layer
        # and has neither a title in its address nor siblings to collide
        # with.
        if not t.op.type:
            want = path_title(t.entry.path)
            got = (state.title or "").strip()
            if want != got and got != "":
                plan.warnings.append(
                    f"{t.entry.path} is titled {_q(got)} on the layer — the index is older than "
                    "the node"
                )
            warning = _rename_collision(idx, t.entry.path, t.op.rename)
            if warning:
                plan.warnings.append(warning)

        try:
            action = plan_op(pc, t.op, t.n, t.entry, state)
        except Exception as e:  # noqa: BLE001
            record_error(t.n, t.op, str(e))
            continue
        if action is None:
            plan.satisfied.append(Satisfied(op_index=t.n, path=t.entry.path))
            continue
        plan.actions.append(action)

    plan.warnings.extend(pc.warnings)
    return plan


def _fetch_states(sim, ids: list, concurrency: int) -> "tuple[dict, dict]":
    """Reads the current state of the addressed nodes, concurrently. A node
    that cannot be read is reported per node rather than failing the plan:
    one unreadable actor should not hide the other forty ops."""
    if sim is None:
        raise ValueError("graph: planning ops needs a simulator client")
    if not ids:
        return {}, {}

    actors: list = [None] * len(ids)
    errs: list = [None] * len(ids)

    def _one(i: int) -> None:
        try:
            actors[i] = sim.get_actor(ids[i], ACTOR_STATE_FILTER)
        except Exception as e:  # noqa: BLE001 - captured per-id, not raised
            errs[i] = e

    workers = concurrency if concurrency and concurrency > 0 else _DEFAULT_CONCURRENCY
    workers = min(workers, len(ids))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, range(len(ids))))

    states: dict = {}
    failures: dict = {}
    for i, actor_id in enumerate(ids):
        if errs[i] is not None:
            failures[actor_id] = errs[i]
        else:
            states[actor_id] = actors[i]
    return states, failures


def plan_op(pc: PlanContext, op, n: int, entry: PathEntry, state) -> "Optional[Action]":
    """Builds the action for one resolved op, or None when the layer already
    holds everything the op asks for."""
    # The form to address the actor by comes from the actor itself, not from
    # the index: on a multiform node it is the leaf form its own values are
    # keyed under, and that is readable only from the data the node carries.
    form_id = state.resolved_form_id()
    action = Action(
        op_index=n,
        actor_id=entry.id,
        path=entry.path,
        title=state.title,
        description=state.description,
        form_id=form_id,
        type=pc.idx.types.slug_for_form(form_id),
    )

    if op.rename and op.rename.strip() != (state.title or "").strip():
        action.rename = op.rename
    # The description is compared verbatim: it is multi-line free text, and
    # a trailing newline someone added in the source document is a change
    # nobody wants to be told about, but a leading one is not worth guessing
    # about either. Trim the ends, keep the middle.
    if op.describe and op.describe.strip() != (state.description or "").strip():
        action.describe = op.describe
    # A record reached by its stamped `id:` may carry no ref at all — it was
    # made by hand, and find_records handed out the uuid because nothing
    # else could find it. The op's `ref:` is written into it here, once.
    if op.type and op.id and not state.ref:
        action.ref = op.ref

    # A picture is filled, never replaced. The stored one came from
    # somewhere — an earlier run, a person, another source — and an image
    # found on a web page does not outrank it.
    if op.picture:
        try:
            validate_picture_url(op.picture)
        except Exception as e:
            raise ValueError(f"{entry.path}: {e}") from e
        if not (state.picture or "").strip():
            action.picture = op.picture
        else:
            pc.warn(
                f"{entry.path} already carries a picture, so {op.picture} was not taken — a "
                "stored image is not replaced from a web page"
            )

    if op.set:
        t = pc.idx.types.type_by_form(form_id)
        if t is None or not t.fields:
            raise ValueError(
                f"{entry.path}: form {form_id} is not in {pc.idx.types_from} — re-export, or the "
                "write would go against a field nobody can check"
            )
        action.changes = plan_set(pc, op.set, t, state, entry.path)

    if not action.rename and not action.describe and not action.ref and not action.picture and not action.changes:
        return None
    # A hole is an empty placeholder slot, and any write at all ends that.
    # This is decided here rather than per kind of change on purpose: the
    # check sits after the nothing-to-do return above, so it is reached only
    # when the run actually writes.
    action.fills_hole = state.hole
    return action


def plan_set(pc: PlanContext, raw_set: dict, t, state, path: str) -> list:
    """Checks an op's field writes against a node's stored values and
    returns the ones that would change something.

    Shared by an update and a create: a create passes a synthetic empty
    actor, so every value reads as a change and storage_key returns the
    plain field id.
    """
    if "title" in raw_set:
        raise ValueError(f"{path}: do not write `title` in `set:` — use `rename:`")

    changes: list = []
    problems: list = []

    # Fields in the order the type declares them, so two plans of the same
    # ops file read the same way.
    for f in t.fields:
        if f.name not in raw_set:
            continue
        raw = raw_set[f.name]
        try:
            change = plan_change(pc, f, raw, state)
        except Exception as e:  # noqa: BLE001 - collected, not raised immediately
            problems.append(str(e))
        else:
            if change is not None:
                changes.append(change)

    known = {f.name for f in t.fields}
    unknown = sorted(name for name in raw_set if name not in known)
    if unknown:
        problems.append("type " + _q(t.slug) + " has " + _describe_unknown(unknown, t))

    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return changes


def plan_change(pc: PlanContext, f, raw, state) -> "Optional[Change]":
    """Coerces one value and compares it with what is stored, returning
    None when the layer already holds it."""
    if not f.writable:
        raise ValueError(f"field {_q(f.name)} is not writable")
    value = coerce_value(pc, f, raw)

    key = storage_key(state.data, f.id)
    old_raw = (state.data or {}).get(key)
    old = format_value(old_raw)
    new = format_value(value)
    if old == new:
        return None
    return Change(field=f.name, key=key, value=value, old=old, new=new)


def storage_key(data: "Optional[dict]", field_id: str) -> str:
    """The key an actor stores a field under: the field id, except on a
    multiform node, where another form's fields are keyed
    "__form__<thatFormId>:<fieldId>" — and a write has to use the same key
    the node already carries or it lands beside the value instead of on it.
    """
    data = data or {}
    if field_id in data:
        return field_id
    suffix = ":" + field_id
    for key in data:
        if key.startswith("__form__") and key.endswith(suffix):
            return key
    return field_id


def _ref_target(field_type: str) -> "Optional[str]":
    if field_type.startswith("ref(") and field_type.endswith(")"):
        return field_type[len("ref(") : -1]
    return None


def _enum_options(field_type: str) -> "Optional[list]":
    if field_type.startswith("enum[") and field_type.endswith("]"):
        inner = field_type[len("enum[") : -1]
        return [x.strip() for x in inner.split(",")]
    return None


def _is_empty_value(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, list):
        return len(v) == 0
    if isinstance(v, dict):
        return len(v) == 0
    return False


def coerce_value(pc: PlanContext, f, raw):
    """Turns a value from the ops file into the shape the field takes,
    refusing anything the type cannot hold."""
    if _is_empty_value(raw):
        raise ValueError(
            f"field {_q(f.name)}: empty value — clearing a field is not supported, do it in Simulator"
        )

    want = _ref_target(f.type)
    if want is not None:
        if not isinstance(raw, str):
            raise ValueError(
                f"field {_q(f.name)} wants ref({want}) — write the target's path, not "
                f"{type(raw).__name__}"
            )
        try:
            target = pc.idx.paths.resolve(raw)
        except Exception as e:
            raise ValueError(f"field {_q(f.name)}: {e}") from e
        try:
            target_state = pc.state(target.id)
        except Exception as e:
            raise ValueError(f"field {_q(f.name)}: read the ref target {_q(target.path)}: {e}") from e
        got = pc.idx.types.slug_for_form(target_state.resolved_form_id())
        if got != want:
            raise ValueError(f"field {_q(f.name)} wants ref({want}), {_q(target.path)} is [{got}]")
        # The shape a reference is stored in: the uuid, and the title the UI
        # shows beside it.
        return {"id": target.id, "title": target_state.title}

    options = _enum_options(f.type)
    if options is not None:
        got = _go_sprint(raw)
        for o in options:
            if o == got:
                return o
        raise ValueError(f"field {_q(f.name)}: {_q(got)} is not one of {', '.join(options)}")

    if f.type == TYPE_INT:
        return _coerce_int(f, raw)
    if f.type == TYPE_NUMBER:
        return _coerce_number(f, raw)
    if f.type == TYPE_BOOL:
        return _coerce_bool(f, raw)
    if f.type == TYPE_DATE:
        return _coerce_date(f, raw)
    if f.type in (TYPE_STRING, TYPE_TEXT):
        return _coerce_string(f, raw)
    # Unknown types come from a widget nobody has mapped yet; _field_type
    # already degrades those to string, so this is only reachable if the
    # vocabulary grows without this dispatch growing with it.
    raise ValueError(f"field {_q(f.name)}: unknown field type {_q(f.type)}")


def _coerce_int(f, raw):
    if isinstance(raw, bool):
        raise ValueError(f"field {_q(f.name)}: {_go_sprint(raw)} is not an int")
    if isinstance(raw, int):
        return int(raw)
    if isinstance(raw, float):
        if raw != int(raw):
            raise ValueError(f"field {_q(f.name)}: {_go_sprint(raw)} is not a whole number")
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            raise ValueError(f"field {_q(f.name)}: {_q(raw)} is not an int") from None
    raise ValueError(f"field {_q(f.name)}: {_go_sprint(raw)} is not an int")


def _coerce_number(f, raw):
    if isinstance(raw, bool):
        raise ValueError(f"field {_q(f.name)}: {_go_sprint(raw)} is not a number")
    if isinstance(raw, int):
        return float(raw)
    if isinstance(raw, float):
        return raw
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            raise ValueError(f"field {_q(f.name)}: {_q(raw)} is not a number") from None
    raise ValueError(f"field {_q(f.name)}: {_go_sprint(raw)} is not a number")


def _coerce_bool(f, raw):
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("1", "t", "true"):
            return True
        if s in ("0", "f", "false"):
            return False
        raise ValueError(f"field {_q(f.name)}: {_q(raw)} is not a bool")
    raise ValueError(f"field {_q(f.name)}: {_go_sprint(raw)} is not a bool")


def _coerce_date(f, raw):
    if isinstance(raw, (_dt.datetime, _dt.date)):
        return raw.strftime(_DATE_LAYOUT_PY)
    if isinstance(raw, str):
        s = raw.strip()
        try:
            return _dt.datetime.strptime(s, _DATE_LAYOUT_PY).strftime(_DATE_LAYOUT_PY)
        except ValueError:
            pass
        iso_candidates = [s]
        if s.endswith("Z"):
            iso_candidates.append(s[:-1] + "+00:00")
        for cand in iso_candidates:
            try:
                return _dt.datetime.fromisoformat(cand).strftime(_DATE_LAYOUT_PY)
            except ValueError:
                pass
        try:
            return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime(_DATE_LAYOUT_PY)
        except ValueError:
            pass
        raise ValueError(f"field {_q(f.name)}: {_q(raw)} is not a date (want {_DATE_LAYOUT_DISPLAY})")
    raise ValueError(f"field {_q(f.name)}: {_go_sprint(raw)} is not a date")


def _coerce_string(f, raw):
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        text = repr(raw)
        if "e" in text or "E" in text:
            text = f"{raw:.17f}".rstrip("0").rstrip(".")
        if text.endswith(".0"):
            text = text[:-2]
        return text
    if isinstance(raw, (_dt.datetime, _dt.date)):
        return raw.strftime(_DATE_LAYOUT_PY)
    # A list or a mapping under a text field is a structure the writer meant
    # to put somewhere else; flattening it would bury that mistake.
    raise ValueError(f"field {_q(f.name)}: {type(raw).__name__} cannot be written to a {f.type} field")


def _describe_unknown(unknown: list, t) -> str:
    parts: list = []
    for name in unknown:
        part = "no field " + _q(name)
        near = _nearest_field(name, t)
        if near:
            part += " (did you mean " + _q(near) + "?)"
        parts.append(part)
    return "; ".join(parts)


def _nearest_field(name: str, t) -> str:
    """The field a misspelt name most likely meant: the closest by edit
    distance, and only when it is close enough that the suggestion is worth
    making."""
    budget = min(3, max(1, len(name) // 3))
    best = ""
    for f in t.fields:
        d = _edit_distance(name, f.name)
        if d <= budget:
            best, budget = f.name, d - 1
    return best


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein, one row at a time."""
    prev = list(range(len(b) + 1))
    curr = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        curr[0] = i
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[len(b)]
