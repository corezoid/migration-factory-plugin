"""Golden-vector tests for the pieces that must never silently drift:
ref_for's exact hash scheme (idempotency depends on it byte-for-byte),
pair_name's group suffix, occurred_at's timezone-aware epoch-ms parsing,
amount validation, and sharePair's never-raises guarantee.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp import ledger  # noqa: E402
from migration_factory_plugin_mcp.simulator.finance import Account, AccountSides  # noqa: E402


def test_ref_for_exact_vector():
    raw_line = (
        '{"transaction_date":"2026-09-21","transaction_time":"08:20:02",'
        '"debit_sum":"236.10","credit_sum":"0.00","currency":"UAH",'
        '"description":"MAGAZiN 5499"}'
    )
    assert ledger.ref_for("stmt", raw_line, "debit") == (
        "stmt-3270a3bf150b2109d0ce58313852b0ef-debit"
    )
    # Same line, different side -> different ref (no collision between the
    # two transactions one row can produce).
    assert ledger.ref_for("stmt", raw_line, "credit") != ledger.ref_for("stmt", raw_line, "debit")
    # Different prefix -> different ref (deliberate second posting).
    assert ledger.ref_for("other", raw_line, "debit") != ledger.ref_for("stmt", raw_line, "debit")


def test_pair_name():
    assert ledger.pair_name("Bank Statement", 0) == "Bank Statement"
    assert ledger.pair_name("Bank Statement", -1) == "Bank Statement"
    assert ledger.pair_name("Bank Statement", 42) == "Bank Statement 42"
    assert ledger.pair_name("  Bank Statement  ", 42) == "Bank Statement 42"


def test_occurred_at_epoch_ms_with_timezone():
    from zoneinfo import ZoneInfo

    loc = ZoneInfo("Europe/Kyiv")
    r = ledger.Record(date="2026-09-21", time="08:20:02")
    ms = ledger._occurred_at(r, loc)
    # 2026-09-21 08:20:02 Europe/Kyiv (EEST, UTC+3 in September) ==
    # 2026-09-21 05:20:02 UTC.
    import datetime

    expected = datetime.datetime(2026, 9, 21, 5, 20, 2, tzinfo=datetime.timezone.utc)
    assert ms == int(expected.timestamp() * 1000)


def test_occurred_at_date_only_defaults_to_midnight():
    from zoneinfo import ZoneInfo

    r = ledger.Record(date="2026-01-01")
    ms = ledger._occurred_at(r, ZoneInfo("UTC"))
    import datetime

    expected = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    assert ms == int(expected.timestamp() * 1000)


def test_occurred_at_missing_date_is_fatal():
    import pytest

    with pytest.raises(ValueError, match="no transaction_date"):
        ledger._occurred_at(ledger.Record(date=""), __import__("zoneinfo").ZoneInfo("UTC"))


def test_amount_rejects_negative_and_nonnumeric():
    import pytest

    assert ledger._amount("") == 0.0
    assert ledger._amount("236.10") == 236.10
    with pytest.raises(ValueError, match="is negative"):
        ledger._amount("-5")
    with pytest.raises(ValueError, match="is not a number"):
        ledger._amount("abc")


class _FakeSim:
    """Minimal stand-in for SimulatorClient exercising only what ledger.post
    calls, so the share-retry-then-raise behavior can be tested without a
    network dependency."""

    def __init__(self, share_raises: bool = False, share_fails_times: int = 0):
        self.share_raises = share_raises
        self.share_fails_times = share_fails_times  # succeeds after this many failures
        self.share_attempts = 0
        self.shared_with = None
        self.transactions = []
        self.accounts = {}

    def ensure_account_pair(self, workspace_id, account_name, currency):
        return "name-1", 1

    def share_account_pair_with_group(self, name_id, currency_id, group_id):
        self.share_attempts += 1
        if self.share_raises or self.share_attempts <= self.share_fails_times:
            raise RuntimeError("403 Access Denied")
        self.shared_with = (name_id, currency_id, group_id)

    def ensure_actor_account(self, actor_id, *, name_id, currency_id, account_type="", search=False):
        self.attached = getattr(self, "attached", [])
        self.attached.append(actor_id)
        sides = AccountSides(debit=f"{actor_id}-debit", credit=f"{actor_id}-credit")
        if actor_id not in self.accounts:
            self.accounts[actor_id] = [
                Account(sides.debit, name_id, currency_id, "debit"),
                Account(sides.credit, name_id, currency_id, "credit"),
            ]
        return sides

    def get_actor_accounts(self, actor_id):
        return list(self.accounts.get(actor_id, []))

    def create_transaction(self, account_id, *, amount, comment, ref, data, original_date):
        self.transactions.append((account_id, amount, ref))
        return 1


def _write_jsonl(tmp_path, rows):
    path = tmp_path / "statement.jsonl"
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return str(path)


def test_post_share_pair_retries_then_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger.time, "sleep", lambda _seconds: None)
    path = _write_jsonl(
        tmp_path,
        [{"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "currency": "UAH"}],
    )
    sim = _FakeSim(share_raises=True)
    import pytest

    with pytest.raises(RuntimeError, match="could not be shared with group 42"):
        ledger.post(
            sim,
            ledger.Options(
                account_name="Bank Statement", actor_id="actor-1", path=path,
                workspace_id="ws-1", group_id=42,
            ),
        )
    assert sim.share_attempts == 1 + len(ledger._SHARE_RETRY_DELAYS)
    assert sim.transactions == []


def test_post_share_pair_succeeds_after_a_transient_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger.time, "sleep", lambda _seconds: None)
    path = _write_jsonl(
        tmp_path,
        [{"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "currency": "UAH"}],
    )
    sim = _FakeSim(share_fails_times=2)  # fails twice, succeeds on the third attempt
    res = ledger.post(
        sim,
        ledger.Options(
            account_name="Bank Statement", actor_id="actor-1", path=path,
            workspace_id="ws-1", group_id=42,
        ),
    )
    assert sim.share_attempts == 3
    assert sim.shared_with == ("name-1", 1, 42)
    assert res.posted == 1


def test_post_duplicate_ref_on_rerun():
    from migration_factory_plugin_mcp.simulator import errors as sim_errors

    class DupSim(_FakeSim):
        def __init__(self):
            super().__init__()
            self.seen_refs = set()

        def create_transaction(self, account_id, *, amount, comment, ref, data, original_date):
            if ref in self.seen_refs:
                raise sim_errors.SimulatorError(status_code=400, message="Not unique ref", code="", body="")
            self.seen_refs.add(ref)
            return super().create_transaction(
                account_id, amount=amount, comment=comment, ref=ref, data=data, original_date=original_date
            )

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "statement.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0"}) + "\n")
        sim = DupSim()
        opts = ledger.Options(account_name="Bank Statement", actor_id="actor-1", path=path, workspace_id="ws-1")
        first = ledger.post(sim, opts)
        second = ledger.post(sim, opts)
        assert first.posted == 1 and first.duplicate == 0
        assert second.posted == 0 and second.duplicate == 1
        assert first.accounts_created == {"actor-1-debit", "actor-1-credit"}
        assert first.accounts_reused == set()
        assert second.accounts_created == set()
        assert second.accounts_reused == {"actor-1-debit", "actor-1-credit"}


def test_account_measurement_failure_is_unknown_not_zero(tmp_path):
    class UnreadableAccounts(_FakeSim):
        def get_actor_accounts(self, actor_id):
            raise RuntimeError("account listing unavailable")

    path = _write_jsonl(tmp_path, [
        {"transaction_date": "2026-01-01", "debit_sum": "10", "currency": "UAH"},
    ])
    res = ledger.post(UnreadableAccounts(), ledger.Options(
        account_name="Statement", actor_id="actor-1", path=path, workspace_id="ws-1",
    ))
    assert res.posted == 1
    assert not res.account_measurement_complete


def test_account_measurement_at_page_limit_is_unknown(tmp_path):
    class FullPage(_FakeSim):
        def get_actor_accounts(self, actor_id):
            return [Account(str(i), "other", 1, "debit") for i in range(100)]

    path = _write_jsonl(tmp_path, [
        {"transaction_date": "2026-01-01", "debit_sum": "10", "currency": "UAH"},
    ])
    res = ledger.post(FullPage(), ledger.Options(
        account_name="Statement", actor_id="actor-1", path=path, workspace_id="ws-1",
    ))
    assert res.posted == 1
    assert not res.account_measurement_complete


def _one_type_set(field_name="iban", field_id="item_1", form_id=42, slug="client", title=""):
    from migration_factory_plugin_mcp.graph.schema import FieldSpec, Type, TypeSet

    return TypeSet(
        types=[
            Type(
                slug=slug, form_id=form_id, form_title="Client",
                fields=[FieldSpec(name=field_name, id=field_id, title=title)],
            )
        ]
    )


class _FieldFakeSim(_FakeSim):
    """Adds list_actors, for the actor_field/actor_type resolution path."""

    def __init__(self, actors_by_query=None):
        super().__init__()
        self.actors_by_query = actors_by_query or {}
        self.queries = []

    def list_actors(self, form_id, *, workspace_id="", filter="", search="", query="", limit=0):
        self.queries.append(query)
        return self.actors_by_query.get(query, [])


def _actor(id, created_at=""):
    from migration_factory_plugin_mcp.simulator.types import Actor

    return Actor(id=id, created_at=created_at)


def test_post_resolves_actor_per_row_from_uniq_actor_field_value(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "RO1"},
            {"transaction_date": "2026-01-02", "debit_sum": "0", "credit_sum": "20", "uniq_actor_field_value": "RO2"},
        ],
    )
    sim = _FieldFakeSim(
        actors_by_query={
            "item_1=RO1": [_actor("actor-1")],
            "item_1=RO2": [_actor("actor-2")],
        }
    )
    res = ledger.post(
        sim,
        ledger.Options(
            account_name="Bank Statement", path=path, workspace_id="ws-1",
            actor_field="iban", actor_type="client",
        ),
        _one_type_set(),
    )
    assert res.posted == 2
    assert res.actors == {"actor-1", "actor-2"}
    assert sorted(sim.attached) == ["actor-1", "actor-2"]
    assert sorted(c.actor_id for c in res.currencies) == ["actor-1", "actor-2"]


def test_post_actor_field_lookup_picks_most_recently_created_on_a_tie(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [{"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "RO1"}],
    )
    sim = _FieldFakeSim(
        actors_by_query={
            "item_1=RO1": [_actor("actor-old", created_at="2026-01-01T00:00:00Z"),
                           _actor("actor-new", created_at="2026-06-01T00:00:00Z")],
        }
    )
    res = ledger.post(
        sim,
        ledger.Options(
            account_name="Bank Statement", path=path, workspace_id="ws-1",
            actor_field="iban", actor_type="client",
        ),
        _one_type_set(),
    )
    assert res.actors == {"actor-new"}


def test_post_mixed_file_falls_back_to_actor_id_when_row_has_no_uniq_value(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "RO1"},
            {"transaction_date": "2026-01-02", "debit_sum": "5", "credit_sum": "0"},
        ],
    )
    sim = _FieldFakeSim(actors_by_query={"item_1=RO1": [_actor("actor-1")]})
    res = ledger.post(
        sim,
        ledger.Options(
            account_name="Bank Statement", path=path, workspace_id="ws-1",
            actor_id="actor-default", actor_field="iban", actor_type="client",
        ),
        _one_type_set(),
    )
    assert res.posted == 2
    assert res.actors == {"actor-1", "actor-default"}


def test_post_row_with_uniq_value_and_no_match_is_skipped_not_fatal(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            {"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "RO1"},
            {"transaction_date": "2026-01-02", "debit_sum": "5", "credit_sum": "0", "uniq_actor_field_value": "RO2"},
        ],
    )
    sim = _FieldFakeSim(actors_by_query={"item_1=RO1": [_actor("actor-1")]})
    res = ledger.post(
        sim,
        ledger.Options(
            account_name="Bank Statement", path=path, workspace_id="ws-1",
            actor_field="iban", actor_type="client",
        ),
        _one_type_set(),
    )
    assert res.posted == 1
    assert res.skipped == 1
    assert any("RO2" in w for w in res.warnings)


def test_post_warns_when_actor_field_is_not_marked_identity(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [{"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "RO1"}],
    )
    sim = _FieldFakeSim(actors_by_query={"item_1=RO1": [_actor("actor-1")]})
    res = ledger.post(
        sim,
        ledger.Options(account_name="x", path=path, workspace_id="ws-1", actor_field="iban", actor_type="client"),
        _one_type_set(title="just a description, not a key"),
    )
    assert any("not marked as an identity key" in w for w in res.warnings)


def test_post_no_warning_when_actor_field_is_marked_identity(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [{"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "RO1"}],
    )
    sim = _FieldFakeSim(actors_by_query={"item_1=RO1": [_actor("actor-1")]})
    res = ledger.post(
        sim,
        ledger.Options(account_name="x", path=path, workspace_id="ws-1", actor_field="iban", actor_type="client"),
        _one_type_set(title="Identity Key"),
    )
    assert not any("identity key" in w for w in res.warnings)


def test_post_actor_field_lookup_retries_with_normalized_candidate(tmp_path):
    """A JSONL value like 'ro 1234' should still find an actor stored as
    'RO1234' -- the same query_candidates normalization find_records applies."""
    path = _write_jsonl(
        tmp_path,
        [{"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "ro 1234"}],
    )
    sim = _FieldFakeSim(actors_by_query={"item_1=RO1234": [_actor("actor-1")]})
    res = ledger.post(
        sim,
        ledger.Options(
            account_name="x", path=path, workspace_id="ws-1", actor_field="iban", actor_type="client",
        ),
        _one_type_set(title="Identity Key"),
    )
    assert res.posted == 1
    assert res.actors == {"actor-1"}
    assert "item_1=ro 1234" in sim.queries
    assert "item_1=RO1234" in sim.queries


def test_post_actor_field_without_actor_type_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="go together"):
        ledger.post(
            _FieldFakeSim(),
            ledger.Options(account_name="x", path="unused", workspace_id="ws-1", actor_field="iban"),
        )


def test_post_no_actor_at_all_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="no actor"):
        ledger.post(_FieldFakeSim(), ledger.Options(account_name="x", path="unused", workspace_id="ws-1"))


def test_post_actor_field_needs_types(tmp_path):
    import pytest

    path = _write_jsonl(tmp_path, [{"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0"}])
    with pytest.raises(ValueError, match="type dictionary"):
        ledger.post(
            _FieldFakeSim(),
            ledger.Options(
                account_name="x", path=path, workspace_id="ws-1", actor_field="iban", actor_type="client",
            ),
        )


def test_post_batch_isolates_failures():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        good_path = os.path.join(tmp, "good.jsonl")
        with open(good_path, "w") as f:
            f.write(json.dumps({"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0"}) + "\n")
        bad_path = os.path.join(tmp, "missing.jsonl")

        sim = _FakeSim()
        batch = ledger.post_batch(
            sim,
            [
                ledger.StatementJob(actor_id="actor-1", path=good_path),
                ledger.StatementJob(actor_id="actor-2", path=bad_path),
            ],
            account_name="State Changes",
            workspace_id="ws-1",
        )
        assert batch.outcomes[0].result is not None and batch.outcomes[0].error is None
        assert batch.outcomes[1].result is None and batch.outcomes[1].error is not None
        totals = batch.totals()
        assert totals.posted == 1
