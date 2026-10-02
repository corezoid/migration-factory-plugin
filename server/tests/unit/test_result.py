import json
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.graph import result as result_mod  # noqa: E402


@dataclass
class _Action:
    actor_id: str
    create: bool = False
    fills_hole: bool = False


def test_cyrillic_keys_and_field_order():
    res = result_mod.RunResult()
    d = res.to_json()
    assert list(d.keys()) == [
        "количество заполненных дырок",
        "количество обновленных акторов",
        "количество созданных акторов",
        "количество проведенных транзакций",
        "заполненные дырки",
        "обновленные акторы",
        "созданные акторы",
        "проведенные выписки",
    ]


def test_add_precedence_created_over_hole_over_updated():
    res = result_mod.RunResult()
    added = res.add([
        _Action(actor_id="a-created", create=True),
        _Action(actor_id="b-hole", fills_hole=True),
        _Action(actor_id="c-updated"),
    ])
    assert added == 3
    assert res.created == ["a-created"]
    assert res.holes == ["b-hole"]
    assert res.updated == ["c-updated"]
    assert res.holes_filled == 1 and res.actors_updated == 1 and res.actors_created == 1


def test_add_dedups_across_lists_on_replay():
    res = result_mod.RunResult()
    res.add([_Action(actor_id="x", create=True)])
    # Same id shows up again as a plain update on a replay — must not be
    # counted a second time in any list.
    added_again = res.add([_Action(actor_id="x")])
    assert added_again == 0
    assert res.created == ["x"]
    assert res.updated == []


def test_add_statement_replaces_by_ref_not_append():
    res = result_mod.RunResult()
    rec1 = result_mod.StatementRecord(ref="stmt|actor|Account|file.jsonl", transactions=5)
    rec2 = result_mod.StatementRecord(ref="stmt|actor|Account|file.jsonl", transactions=9)
    assert res.add_statement(rec1) is True
    assert res.add_statement(rec2) is False
    assert len(res.statements) == 1
    assert res.statements[0].transactions == 9
    assert res.transactions_posted == 9


def test_account_counts_are_optional_and_deduplicated_across_statements():
    res = result_mod.RunResult()
    assert "accountsCreated" not in res.to_json()
    res.add_statement(result_mod.StatementRecord(
        ref="a", created_account_refs=["account-1", "account-2"], reused_account_refs=[]
    ))
    res.add_statement(result_mod.StatementRecord(
        ref="b", created_account_refs=[], reused_account_refs=["account-1", "account-3"]
    ))
    assert res.to_json()["accountsCreated"] == 2
    assert res.to_json()["accountsReused"] == 1
    res.add_statement(result_mod.StatementRecord(
        ref="a", created_account_refs=[], reused_account_refs=["account-1"]
    ))
    assert res.to_json()["accountsCreated"] == 2
    assert res.to_json()["accountsReused"] == 1


def test_legacy_statement_keeps_account_counts_unknown():
    res = result_mod.RunResult()
    res.add_statement(result_mod.StatementRecord(ref="old", transactions=3))
    res.add_statement(result_mod.StatementRecord(
        ref="new", created_account_refs=["account-1"], reused_account_refs=[]
    ))
    assert "accountsCreated" not in res.to_json()
    assert "accountsReused" not in res.to_json()
    res.add_statement(result_mod.StatementRecord(
        ref="old", created_account_refs=[], reused_account_refs=["account-1"]
    ))
    assert "accountsCreated" not in res.to_json()


def test_load_missing_file_is_empty_not_error(tmp_path):
    res = result_mod.load_result(str(tmp_path / "nope.json"))
    assert res == result_mod.RunResult()


def test_write_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "result.json")
    res = result_mod.RunResult()
    res.add([_Action(actor_id="a", create=True)])
    res.add_statement(result_mod.StatementRecord(ref="r1", transactions=3, turnover=[
        result_mod.StatementTurnover(currency="UAH", debit=1.5, credit=0.0)
    ]))
    result_mod.write_result(path, res)
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    assert raw.endswith("\n")
    assert "количество заполненных дырок" in raw
    loaded = result_mod.load_result(path)
    assert loaded.created == ["a"]
    assert loaded.statements[0].turnover[0].currency == "UAH"


def test_statement_ref_identity_key():
    ref = result_mod.statement_ref("stmt", "actor-1", "Bank Statement", "/tmp/run/statement.jsonl")
    assert ref == "stmt|actor-1|Bank Statement|statement.jsonl"
    assert result_mod.statement_ref("", "actor-1", "Bank Statement", "s.jsonl") == "stmt|actor-1|Bank Statement|s.jsonl"


def test_statement_record_actors_roundtrip_when_many():
    rec = result_mod.StatementRecord(ref="r1", transactions=2, actors=["actor-1", "actor-2"])
    d = rec.to_json()
    assert d["акторы"] == ["actor-1", "actor-2"]
    back = result_mod.StatementRecord.from_json(d)
    assert back.actors == ["actor-1", "actor-2"]


def test_statement_record_actors_omitted_when_single():
    rec = result_mod.StatementRecord(ref="r1", transactions=1, actor_id="actor-1", actors=[])
    d = rec.to_json()
    assert "акторы" not in d
    back = result_mod.StatementRecord.from_json(d)
    assert back.actors == []
