import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import apply as apply_mod  # noqa: E402
from migration_factory_plugin_mcp.simulator.errors import SimulatorError  # noqa: E402
from migration_factory_plugin_mcp.simulator.types import Actor, Form  # noqa: E402


def _write_export(tmp_path, actor_data=None):
    (tmp_path / "graph.ids.json").write_text(
        json.dumps({"layer": "layer-1", "sep": " > ", "paths": {"ACME": "a1"}})
    )
    (tmp_path / "types.schema.yaml").write_text(
        "types:\n"
        "  company:\n"
        "    formId: 1\n"
        "    form: COMPANY\n"
        "    fields:\n"
        "      industry: {id: industry, type: string}\n"
    )


def _write_ops(tmp_path, body):
    path = tmp_path / "graph.ops.yaml"
    path.write_text(body)
    return str(path)


class _BaseFakeSim:
    def __init__(self):
        self.actor = Actor(id="a1", title="ACME", form_id=1, data={}, hole=False)
        self.patch_calls = []
        self.create_calls = []
        self.shared_with = []

    def get_actor(self, actor_id, filter=""):
        assert actor_id == "a1"
        return self.actor

    def patch_actor(self, form_id, actor_id, *, data=None, title="", description="", ref="", picture="", hole=None):
        self.patch_calls.append(dict(form_id=form_id, actor_id=actor_id, data=data, title=title,
                                      description=description, ref=ref, picture=picture, hole=hole))
        self.actor = Actor(
            id=actor_id, title=title or self.actor.title, form_id=form_id,
            data={**self.actor.data, **(data or {})}, hole=self.actor.hole if hole is None else hole,
        )
        return self.actor


def test_dry_run_plans_but_writes_nothing(tmp_path):
    _write_export(tmp_path)
    ops_path = _write_ops(tmp_path, 'layer: layer-1\nops:\n  - at: "ACME"\n    set:\n      industry: Manufacturing\n')
    sim = _BaseFakeSim()

    res = apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=True, keep_export=True))
    assert res.plan is not None
    assert len(res.plan.actions) == 1
    assert res.applied == []
    assert sim.patch_calls == []
    assert not (tmp_path / "result.json").exists()


def test_write_applies_and_records_result(tmp_path):
    _write_export(tmp_path)
    ops_path = _write_ops(tmp_path, 'layer: layer-1\nops:\n  - at: "ACME"\n    set:\n      industry: Manufacturing\n')
    sim = _BaseFakeSim()

    res = apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True))
    assert len(res.applied) == 1
    assert sim.patch_calls[0]["data"] == {"industry": "Manufacturing"}
    assert res.result is not None
    assert res.result.actors_updated == 1

    with open(tmp_path / "result.json", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["количество обновленных акторов"] == 1


def test_replay_of_unchanged_ops_is_satisfied_not_applied(tmp_path):
    _write_export(tmp_path)
    ops_path = _write_ops(tmp_path, 'layer: layer-1\nops:\n  - at: "ACME"\n    set:\n      industry: Manufacturing\n')
    sim = _BaseFakeSim()
    apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True))

    # Second run against the now-updated actor: nothing left to write.
    res2 = apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True))
    assert res2.applied == []
    assert len(res2.plan.satisfied) == 1


def test_stamp_writes_id_before_any_network_call(tmp_path):
    _write_export(tmp_path)
    ops_path = _write_ops(tmp_path, 'layer: layer-1\nops:\n  - at: "ACME"\n    set:\n      industry: Manufacturing\n')
    sim = _BaseFakeSim()
    res = apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True))
    assert res.stamped == 1
    text = open(ops_path).read()
    assert "id: a1" in text


def test_hole_is_cleared_on_fill(tmp_path):
    _write_export(tmp_path)
    ops_path = _write_ops(tmp_path, 'layer: layer-1\nops:\n  - at: "ACME"\n    set:\n      industry: Manufacturing\n')
    sim = _BaseFakeSim()
    sim.actor.hole = True
    res = apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True))
    assert res.applied[0].fills_hole is True
    assert sim.patch_calls[0]["hole"] is False
    assert res.result.holes_filled == 1


class _CreateFakeSim(_BaseFakeSim):
    def __init__(self, conflict_then_found=False):
        super().__init__()
        self.conflict_then_found = conflict_then_found
        self.created = None

    def get_actor_by_ref(self, form_id, ref, filter=""):
        return None  # nothing exists yet -> create

    def create_actor(self, form_id, data, *, title="", description="", ref="", picture=""):
        self.create_calls.append(dict(form_id=form_id, data=data, title=title, ref=ref))
        if self.conflict_then_found:
            raise SimulatorError(status_code=409, message="Conflict", code="", body="")
        self.created = Actor(id="new-actor-1", title=title, form_id=form_id, ref=ref, data=data or {})
        return self.created

    def share_with_group(self, actor_id, group_id):
        self.shared_with.append((actor_id, group_id))


def test_create_record_stamps_and_shares(tmp_path):
    _write_export(tmp_path)
    ops_path = _write_ops(
        tmp_path,
        'layer: layer-1\nops:\n  - type: company\n    ref: "UA-123"\n    rename: "New Co"\n    set:\n      industry: Retail\n',
    )
    sim = _CreateFakeSim()
    res = apply_mod.apply_ops_file(
        sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True, group_id=42)
    )
    assert len(res.applied) == 1
    assert res.applied[0].create is True
    assert res.applied[0].actor_id == "new-actor-1"
    assert sim.shared_with == [("new-actor-1", 42)]
    assert res.stamped == 1
    assert "id: new-actor-1" in open(ops_path).read()
    assert res.result.actors_created == 1


def test_create_race_loss_falls_back_to_update(tmp_path, monkeypatch):
    _write_export(tmp_path)
    ops_path = _write_ops(
        tmp_path,
        'layer: layer-1\nops:\n  - type: company\n    ref: "UA-123"\n    set:\n      industry: Retail\n',
    )
    sim = _CreateFakeSim(conflict_then_found=True)
    winner = Actor(id="winner-1", title="Existing Co", form_id=1, ref="UA-123", data={"industry": "Old"})

    calls = {"n": 0}

    def get_actor_by_ref(form_id, ref, filter=""):
        calls["n"] += 1
        # First call (in find_record, before the create attempt) says "not
        # found yet"; the second call (after the conflict) finds the winner
        # that just won the race.
        return None if calls["n"] == 1 else winner

    sim.get_actor_by_ref = get_actor_by_ref

    res = apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True))
    assert len(res.applied) == 1
    assert res.applied[0].create is False
    assert res.applied[0].actor_id == "winner-1"
    assert sim.patch_calls[0]["actor_id"] == "winner-1"
    assert sim.patch_calls[0]["data"] == {"industry": "Retail"}


def test_partial_mode_continues_past_a_failed_action(tmp_path):
    _write_export(tmp_path)
    (tmp_path / "graph.ids.json").write_text(
        json.dumps({"layer": "layer-1", "sep": " > ", "paths": {"ACME": "a1", "Other": "a2"}})
    )
    ops_path = _write_ops(
        tmp_path,
        'layer: layer-1\nops:\n'
        '  - at: "ACME"\n    set:\n      industry: Manufacturing\n'
        '  - at: "Other"\n    set:\n      industry: Retail\n',
    )

    class _PartialFailSim(_BaseFakeSim):
        def get_actor(self, actor_id, filter=""):
            if actor_id == "a2":
                return Actor(id="a2", title="Other", form_id=1, data={})
            return super().get_actor(actor_id, filter)

        def patch_actor(self, form_id, actor_id, **kwargs):
            if actor_id == "a2":
                raise RuntimeError("boom")
            return super().patch_actor(form_id, actor_id, **kwargs)

    sim = _PartialFailSim()
    with pytest.raises(apply_mod.ApplyError):
        apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True, partial=True))


def test_non_partial_mode_aborts_on_first_failure_but_still_records_result(tmp_path):
    _write_export(tmp_path)
    (tmp_path / "graph.ids.json").write_text(
        json.dumps({"layer": "layer-1", "sep": " > ", "paths": {"ACME": "a1", "Other": "a2"}})
    )
    ops_path = _write_ops(
        tmp_path,
        'layer: layer-1\nops:\n'
        '  - at: "ACME"\n    set:\n      industry: Manufacturing\n'
        '  - at: "Other"\n    set:\n      industry: Retail\n',
    )

    class _AbortSim(_BaseFakeSim):
        def get_actor(self, actor_id, filter=""):
            if actor_id == "a2":
                return Actor(id="a2", title="Other", form_id=1, data={})
            return super().get_actor(actor_id, filter)

        def patch_actor(self, form_id, actor_id, **kwargs):
            if actor_id == "a2":
                raise RuntimeError("boom")
            return super().patch_actor(form_id, actor_id, **kwargs)

    sim = _AbortSim()
    with pytest.raises(apply_mod.ApplyError) as exc_info:
        apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True, partial=False))
    # The first op still applied and is still recorded, even though the run
    # as a whole raised.
    assert exc_info.value.result.applied
    assert (tmp_path / "result.json").exists()


def test_planning_failure_with_no_partial_raises_before_any_write(tmp_path):
    _write_export(tmp_path)
    ops_path = _write_ops(tmp_path, 'layer: layer-1\nops:\n  - at: "Nonexistent"\n    set:\n      industry: X\n')
    sim = _BaseFakeSim()
    with pytest.raises(apply_mod.ApplyError):
        apply_mod.apply_ops_file(sim, ops_path, apply_mod.ApplyOptions(dry_run=False, keep_export=True))
    assert sim.patch_calls == []


def test_create_without_ops_path_is_a_hard_error(monkeypatch):
    from migration_factory_plugin_mcp.graph.ops import OpsFile, Op

    ops = OpsFile(layer="layer-1", ops=[Op(type="company", ref="UA-1", set={"industry": "X"})])
    sim = _CreateFakeSim()

    class _Idx:
        pass

    import migration_factory_plugin_mcp.graph.plan as plan_mod
    from migration_factory_plugin_mcp.graph.paths import PathIndex
    from migration_factory_plugin_mcp.graph.schema import TypeSet, Type, FieldSpec

    types = TypeSet(types=[Type(slug="company", form_id=1, fields=[FieldSpec(name="industry", id="industry")])])

    def fake_load_index(sim_, layer_id, from_dir, concurrency):
        return plan_mod.Index(
            layer_id="layer-1", paths=PathIndex.build([]), types=types,
            paths_from="test", from_export=False, dir="", types_from="test", warnings=[],
        )

    monkeypatch.setattr(apply_mod, "load_index", fake_load_index)

    with pytest.raises(apply_mod.ApplyError, match="in-memory ops cannot own"):
        apply_mod.apply_ops(sim, ops, apply_mod.ApplyOptions(dry_run=False, layer_id="layer-1"))
