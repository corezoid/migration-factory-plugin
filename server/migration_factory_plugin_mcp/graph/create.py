"""The second way an op addresses its subject: by type and business ref
rather than by a path on the layer. Ported from the Go
`internal/graph/create.go`.

It is what a document does with a fact that has nowhere to go — the layer
carries one node of the right type and it already holds a different record.
The record is created as an actor of its form and is NOT placed on the
canvas: putting it there needs the edge to its parent as well, and that
endpoint is not wired. So the new actor lives in the form's records, where
`ref:` and the uuid stamped back into the ops file are the only handles on
it — it will not appear in the next graph.values.yaml.

This module imports plan.py at the top level (safe: plan.py never imports
this module at ITS top level, only inside plan_ops()'s body).
"""
from __future__ import annotations

from typing import Optional

from . import plan as plan_mod
from .pictures import validate_picture_url
from ..simulator.errors import SimulatorError, is_not_found
from ..simulator.types import Actor


def record_path(ref: str) -> str:
    """What the plan and the errors call a record addressed by ref. It is
    deliberately not a layer path: the actor is not on the layer, and a
    reader who goes looking for one on the canvas would not find it."""
    return "ref:" + ref


def record_key(form_id: int, ref: str) -> tuple:
    """Identifies a record within one ops file — a ref is unique per form,
    so two ops may carry the same ref only if their types differ."""
    return (form_id, ref)


def resolve_record_op(idx: "plan_mod.Index", op) -> "object":
    """Validates a `type:` op and returns the type it names."""
    plan_mod.unsupported_vocabulary(op)
    if op.at:
        raise ValueError(
            "op has both `at:` and `type:` — `at:` writes to a node on the layer, `type:` owns a "
            "record of its own; pick one"
        )
    if op.ref.strip() == "":
        raise ValueError(
            "`type:` needs a `ref:`: the business key is what stops a replay of this file — or of "
            "the same document — from creating the record a second time"
        )
    if not op.set and not op.rename and not op.describe and not op.picture:
        raise ValueError("op does nothing: no `set:`, `rename:`, `describe:` or `picture:`")

    t = idx.types.by_slug(op.type)
    if t is None:
        raise ValueError(
            f"no type {plan_mod._q(op.type)} in {idx.types_from} — the slug is the name in "
            f"[square brackets] in graph.values.yaml; the layer has {', '.join(idx.types.slugs())}"
        )
    if not t.fields:
        raise ValueError(
            f"type {plan_mod._q(t.slug)} has no field schema in {idx.types_from} — re-export, or "
            "the write would go against a field nobody can check"
        )
    return t


def find_record(sim, t, op) -> "Optional[Actor]":
    """Reads the record an op owns, or reports that it does not exist yet by
    returning None.

    A stamped op is read by its uuid: the record was created by an earlier
    run, and the uuid is the address that survives a `rename:`.
    """
    if op.id:
        try:
            return sim.get_actor(op.id, plan_mod.ACTOR_STATE_FILTER)
        except SimulatorError as e:
            if is_not_found(e):
                # The stamped record is gone — deleted in Simulator since
                # the last run. Creating a replacement silently would
                # resurrect a record somebody deleted on purpose.
                raise ValueError(
                    f"the record stamped as {op.id} is gone from Simulator; drop the `id:` from "
                    "this op to create it again, or drop the op"
                ) from e
            raise

    # get_actor_by_ref already swallows a 404 into None (see
    # simulator/actors.py) — that is exactly "does not exist yet".
    return sim.get_actor_by_ref(t.form_id, op.ref, plan_mod.ACTOR_STATE_FILTER)


def plan_create(pc: "plan_mod.PlanContext", op, n: int, t) -> "plan_mod.Action":
    """Builds the action for a record that does not exist yet.

    Every field is a write, because there is nothing stored to compare
    against — which is also why a create can never come back satisfied, and
    why it is the one action in a plan that is not an overwrite.
    """
    action = plan_mod.Action(
        op_index=n,
        path=record_path(op.ref),
        form_id=t.form_id,
        type=t.slug,
        create=True,
        ref=op.ref,
        rename=op.rename,
        describe=op.describe,
        picture=op.picture,
    )
    # Nothing is stored to leave alone, so a create's picture is never the
    # overwrite plan_op guards against — only the address has to hold up.
    if action.picture:
        try:
            validate_picture_url(action.picture)
        except Exception as e:
            raise ValueError(f"{action.path}: {e}") from e

    # An empty actor as the left-hand side: storage_key then returns the
    # plain field id, which is the right key for a record that carries no
    # other form's fields yet, and every value reads as a change.
    empty_actor = Actor(id="")
    action.changes = plan_mod.plan_set(pc, op.set, t, empty_actor, action.path)
    return action
