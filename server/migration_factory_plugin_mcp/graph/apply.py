"""The write path: plans an ops file against the live layer and, when asked,
applies it. Ported from the Go `internal/graph/apply.go`.

A change reaches the platform one actor at a time; nothing here writes a
layer back as a whole. The order matters and is deliberate:

  1. plan (read-only) — resolve every op, diff against current state
  2. stamp `id:` into the ops file, BEFORE any write — so a crash mid-run
     never leaves an op addressable only by a path a rename is about to move
  3. resolve pictures (soft-fail — a bad image never blocks the rest of an op)
  4. apply each action in file order, creates stamped one at a time as they land
  5. record the tally into result.json — unconditionally, even after a
     partial failure: what landed, landed, and the record of it must not
     depend on the rest of the run going well
  6. refresh the export, only if the run actually touched the canvas
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from ..simulator.errors import SimulatorError, is_bad_request, is_conflict
from .export import ExportOptions, ExportResult, export_layer
from .ops import OpsFile, load_ops
from .pictures import PictureStore
from .plan import Action, Index, Plan, load_index, plan_ops
from .result import RunResult, load_result, result_path, write_result
from .stamp import stamp_ops

log = logging.getLogger("migration-factory-plugin")


@dataclass
class ApplyOptions:
    layer_id: str = ""
    from_dir: str = ""  # defaults to dirname(ops_path) in apply_ops_file
    dry_run: bool = False
    partial: bool = False
    ops_path: str = ""  # empty => in-memory ops: no stamping, no result.json
    keep_export: bool = False
    concurrency: int = 8
    workspace_id: str = ""
    group_id: int = 0  # >0 => share every created record with this group


@dataclass
class ActionFailure:
    action: Action
    error: str


@dataclass
class ApplyResult:
    plan: Optional[Plan] = None
    applied: list = field(default_factory=list)  # list[Action]
    failed: list = field(default_factory=list)  # list[ActionFailure]
    export: Optional[ExportResult] = None
    picture_warnings: list = field(default_factory=list)
    share_warnings: list = field(default_factory=list)
    stamped: int = 0
    stamp_warnings: list = field(default_factory=list)
    result: Optional[RunResult] = None
    result_path: str = ""
    result_added: int = 0

    def touched_the_layer(self) -> bool:
        """True iff something changed the actual canvas/tree — a run of pure
        creates (off-canvas records) does not, on its own, warrant a full
        re-export."""
        return any(not a.create for a in self.applied)


class ApplyError(Exception):
    """Carries the partial ApplyResult (in particular its Plan, which is
    always worth rendering even when the run failed) alongside the error."""

    def __init__(self, message: str, result: ApplyResult):
        super().__init__(message)
        self.result = result


def apply_ops_file(sim, path: str, opts: ApplyOptions) -> ApplyResult:
    ops = load_ops(path)
    if not opts.from_dir:
        opts.from_dir = os.path.dirname(path)
    if not opts.ops_path:
        opts.ops_path = path
    return apply_ops(sim, ops, opts)


def _apply_layer_id(ops: OpsFile, opts: ApplyOptions) -> str:
    if not opts.layer_id and not ops.layer:
        raise ValueError("graph: no layer — pass `layer` with the call, or put `layer:` in the ops file")
    if opts.layer_id and ops.layer and opts.layer_id != ops.layer:
        raise ValueError(
            f"graph: the call names layer {opts.layer_id}, the ops file names {ops.layer} — they must agree"
        )
    return opts.layer_id or ops.layer


def apply_ops(sim, ops: OpsFile, opts: ApplyOptions) -> ApplyResult:
    layer_id = _apply_layer_id(ops, opts)
    idx = load_index(sim, layer_id, opts.from_dir, opts.concurrency)
    plan = plan_ops(sim, idx, ops, opts.concurrency)
    res = ApplyResult(plan=plan)

    if opts.dry_run:
        return res

    if not plan.ok() and not opts.partial:
        raise ApplyError(
            f"graph: {len(plan.errors)} op(s) failed to plan — nothing written, fix them or run with partial",
            res,
        )

    if plan.creates() > 0 and not opts.ops_path:
        raise ApplyError(
            "graph: this ops file creates a record, which needs a file to stamp the resulting uuid "
            "into — in-memory ops cannot own an off-canvas record with no durable id:/ref: handle",
            res,
        )

    if opts.ops_path:
        stamped, stamp_warnings = stamp_ops(opts.ops_path, plan.resolved)
        res.stamped = stamped
        res.stamp_warnings = stamp_warnings

    pics = PictureStore(sim=sim, workspace_id=opts.workspace_id)

    write_err: Optional[Exception] = None
    for action in plan.actions:
        _resolve_picture(sim, pics, action, res)
        if not action.writes():
            continue
        try:
            _perform_action(sim, action, opts, res)
        except Exception as exc:  # noqa: BLE001 - collected as an ActionFailure, not re-raised here
            res.failed.append(ActionFailure(action=action, error=str(exc)))
            if not opts.partial:
                write_err = ValueError(
                    f"graph: op #{action.op_index} ({action.path}) failed after {len(res.applied)} applied: {exc}"
                )
                break
            continue
        res.applied.append(action)

    result_err = _record_result(opts, res)
    export_err = _refresh_export(sim, layer_id, idx, opts, res)

    if write_err is not None:
        raise ApplyError(str(write_err), res)
    if res.failed:
        raise ApplyError(
            f"graph: {len(res.failed)} action(s) failed; {len(res.applied)} applied", res
        )
    if result_err is not None:
        raise ApplyError(f"graph: applied, but could not save result.json: {result_err}", res)
    if export_err is not None:
        raise ApplyError(f"graph: applied, but could not refresh the export: {export_err}", res)
    return res


def _resolve_picture(sim, pics: PictureStore, action: Action, res: ApplyResult) -> None:
    if not action.picture:
        return
    try:
        action.stored_picture = pics.path_for(action.form_id, action.path, action.picture)
    except Exception as exc:  # noqa: BLE001 - a picture failure never blocks the rest of the op
        res.picture_warnings.append(f"{action.path}: picture not taken — {exc}")
        action.picture = ""
        action.stored_picture = ""


def _share_created(sim, action: Action, group_id: int, res: ApplyResult) -> None:
    if group_id <= 0:
        return
    try:
        sim.share_with_group(action.actor_id, group_id)
    except Exception as exc:  # noqa: BLE001 - best-effort, never fails the create
        log.warning("share %s with group %s: %s", action.actor_id, group_id, exc)
        res.share_warnings.append(
            f"{action.path} is not shared with group {group_id}, so only this run's key can see it: {exc}"
        )


def _write_update(sim, action: Action) -> None:
    sim.patch_actor(
        action.form_id, action.actor_id,
        data=action.data(), title=action.rename, description=action.describe,
        ref=action.ref, picture=action.stored_picture,
        hole=False if action.fills_hole else None,
    )


def _create_action(sim, action: Action, opts: ApplyOptions, res: ApplyResult) -> None:
    from .plan import storage_key

    try:
        actor = sim.create_actor(
            action.form_id, action.data(),
            title=action.rename, description=action.describe,
            ref=action.ref, picture=action.stored_picture,
        )
        action.actor_id = actor.id
    except SimulatorError as exc:
        if not (is_conflict(exc) or is_bad_request(exc)):
            raise
        # Lost a create race: another run (or this one, replayed) made the
        # same ref first. Adopt the winner and fall back to an update rather
        # than failing the whole op over a record that now genuinely exists.
        winner = sim.get_actor_by_ref(action.form_id, action.ref)
        if winner is None:
            raise
        action.create = False
        action.actor_id = winner.id
        if winner.picture:
            action.stored_picture = ""
        for change in action.changes:
            change.key = storage_key(winner.data, change.key)
        _write_update(sim, action)
        return

    if action.create:
        _share_created(sim, action, opts.group_id, res)
        if opts.ops_path:
            stamped_count, warnings = stamp_ops(opts.ops_path, {action.op_index: action.actor_id})
            res.stamped += stamped_count
            res.stamp_warnings.extend(warnings)


def _perform_action(sim, action: Action, opts: ApplyOptions, res: ApplyResult) -> None:
    if action.create:
        _create_action(sim, action, opts, res)
    else:
        _write_update(sim, action)


def _record_result(opts: ApplyOptions, res: ApplyResult) -> Optional[Exception]:
    path = result_path(opts.ops_path, opts.from_dir)
    if not path or not res.applied:
        return None
    try:
        tally = load_result(path)
        added = tally.add(res.applied)
        write_result(path, tally)
    except OSError as exc:
        return exc
    res.result = tally
    res.result_path = path
    res.result_added = added
    return None


def _append_warnings(lines: list, warnings: list) -> None:
    for w in warnings:
        lines.append(f"warning: {w}")


def render_apply_result(res: ApplyResult, ops_path: str, write: bool, elapsed: float) -> str:
    """Renders a successful (possibly dry-run) apply. For a failed apply,
    the caller should catch ApplyError and render `exc.result.plan.render()`
    alongside the error message instead — see server.py's run_apply."""
    lines = [("applying" if write else "planning") + " " + ops_path, ""]
    if res.plan is not None:
        lines.append(res.plan.render())
        lines.append("")

    if not write:
        lines.append("")
        lines.append("dry run only — nothing written; call again with write: true to apply")
        return "\n".join(lines) + "\n"

    ms = round(elapsed * 1000)
    lines.append("")
    lines.append(f"applied {len(res.applied)} action(s) in {ms}ms")
    for a in res.applied:
        if a.create:
            lines.append(
                f"created {a.path} [{a.type}] as actor {a.actor_id} — a record of form {a.form_id}, "
                "not a node on the layer"
            )
    if res.result is not None:
        lines.append(
            f"{os.path.basename(res.result_path)}: {res.result.holes_filled} hole(s) filled, "
            f"{res.result.actors_updated} actor(s) updated, {res.result.actors_created} record(s) "
            f"created in total across every run of this ops file ({res.result_added} new this run)"
        )
    _append_warnings(lines, res.picture_warnings)
    _append_warnings(lines, res.share_warnings)
    if res.stamped > 0:
        lines.append(
            f"stamped {res.stamped} op(s) with their node id — the file replays by id from now on, "
            "through any rename"
        )
    _append_warnings(lines, res.stamp_warnings)
    if res.export is not None:
        e = res.export
        lines.append(
            f"export refreshed: {e.nodes} nodes, {e.types} types, {e.nodes_with_values} with values "
            f"→ {os.path.dirname(e.values_path)}"
        )
        _append_warnings(lines, e.warnings)
    return "\n".join(lines) + "\n"


def _refresh_export(sim, layer_id: str, idx: Index, opts: ApplyOptions, res: ApplyResult) -> Optional[Exception]:
    if opts.keep_export or not idx.from_export or not res.touched_the_layer():
        return None
    try:
        res.export = export_layer(
            sim, ExportOptions(layer_id=layer_id, dir=idx.dir, concurrency=opts.concurrency)
        )
        return None
    except Exception as exc:  # noqa: BLE001
        return exc
