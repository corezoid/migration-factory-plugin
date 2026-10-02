import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp import config, server  # noqa: E402
from migration_factory_plugin_mcp.firecrawl.scrape import Page, PageMetadata  # noqa: E402
from migration_factory_plugin_mcp.graph.export import ExportResult  # noqa: E402
from migration_factory_plugin_mcp.graph.records import FindResult, FoundRecord, ValueCheck  # noqa: E402
from migration_factory_plugin_mcp.ledger import CurrencyTally, Result  # noqa: E402
from migration_factory_plugin_mcp.simulator.finance import Account, AccountSides  # noqa: E402


class _FakeSim:
    def __init__(self, actors_by_query=None):
        self.transactions = []
        self.actors_by_query = actors_by_query or {}
        self.accounts = {}

    def ensure_account_pair(self, workspace_id, account_name, currency):
        return "name-1", 1

    def share_account_pair_with_group(self, name_id, currency_id, group_id):
        pass

    def ensure_actor_account(self, actor_id, *, name_id, currency_id, account_type="", search=False):
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

    def list_actors(self, form_id, *, workspace_id="", filter="", search="", query="", limit=0):
        return self.actors_by_query.get(query, [])


def _fake_cfg(monkeypatch, sim=None, workspace_id="ws-1", group_id=0):
    cfg = config.Config(base_url="", api_key="key", workspace_id=workspace_id, group_id=group_id)
    fake_sim = sim or _FakeSim()
    cfg.client = lambda: fake_sim  # type: ignore[method-assign]
    monkeypatch.setattr(config, "load_config", lambda override: cfg)
    return cfg, fake_sim


def test_run_export_renders_counts_and_files(monkeypatch, tmp_path):
    _fake_cfg(monkeypatch)

    for name, content in [("graph.values.yaml", b"x"), ("graph.ids.json", b"yy"), ("types.schema.yaml", b"zzz")]:
        (tmp_path / name).write_bytes(content)

    def fake_export_layer(sim, opts):
        return ExportResult(
            layer_id=opts.layer_id,
            values_path=str(tmp_path / "graph.values.yaml"),
            ids_path=str(tmp_path / "graph.ids.json"),
            types_path=str(tmp_path / "types.schema.yaml"),
            nodes=3, edges=2, types=1, nodes_with_values=2,
            warnings=["a heads-up"],
        )

    monkeypatch.setattr(server, "export_layer", fake_export_layer)
    text = server.run_export({"layer": "layer-1", "dir": str(tmp_path)})
    assert "exported layer layer-1" in text
    assert "3 nodes, 2 edges, 1 types, 2 nodes with values" in text
    assert "graph.values.yaml" in text
    assert "warning: a heads-up" in text


def test_run_export_requires_layer():
    import pytest

    with pytest.raises(ValueError, match="no layer to export"):
        server.run_export({})


def test_run_find_records_renders_found_and_not_found(monkeypatch, tmp_path):
    _fake_cfg(monkeypatch)
    (tmp_path / "types.schema.yaml").write_bytes(b"types:\n  widget:\n    formId: 1\n    fields: {}\n")

    def fake_find_records(sim, types, opts):
        return FindResult(
            type_slug="widget", form_id=1, form_title="WIDGET",
            probes=["tax_id", "title"],
            checks=[
                ValueCheck(value="ACME", matched_by="tax_id", records=[
                    FoundRecord(id="id-1", ref="ref-1", title="ACME Corp", fields={"tax_id": "123"})
                ]),
                ValueCheck(value="Nobody"),
            ],
            warnings=[],
        )

    monkeypatch.setattr(server.records_mod, "find_records", fake_find_records)
    text = server.run_find_records({"type": "widget", "values": ["ACME", "Nobody"], "dir": str(tmp_path)})
    assert "FOUND by tax_id" in text
    assert "ref: ref-1" in text
    assert "Nobody" in text and "not found" in text
    assert "1 found, 1 not found" in text


def test_run_find_records_requires_export_first(tmp_path, monkeypatch):
    _fake_cfg(monkeypatch)
    import pytest

    with pytest.raises(ValueError, match="export the layer first"):
        server.run_find_records({"type": "widget", "values": ["x"], "dir": str(tmp_path)})


def test_run_read_page_renders_metadata_and_images(monkeypatch):
    class _FakeFcClient:
        def scrape_with_images(self, url):
            from migration_factory_plugin_mcp.firecrawl.images import Image

            return Page(
                markdown="Hello world",
                images=[Image(url="https://example.com/logo.png", alt="Logo")],
                metadata=PageMetadata(title="Example", og_image="https://example.com/og.png", favicon="https://example.com/favicon.ico", url="https://example.com/"),
            )

    class _FakeFcConfig:
        def client(self):
            return _FakeFcClient()

    monkeypatch.setattr(config, "load_firecrawl_config", lambda: _FakeFcConfig())
    text = server.run_read_page({"url": "https://example.com/"})
    assert "title: Example" in text
    assert "og:image" in text
    assert "favicon" in text
    assert "pictures on this page — 1" in text
    assert "logo.png" in text and "Logo" in text
    assert text.strip().endswith("Hello world")


def test_run_read_page_empty_markdown_is_an_error(monkeypatch):
    import pytest

    class _FakeFcClient:
        def scrape_with_images(self, url):
            return Page(markdown="   ", metadata=PageMetadata())

    class _FakeFcConfig:
        def client(self):
            return _FakeFcClient()

    monkeypatch.setattr(config, "load_firecrawl_config", lambda: _FakeFcConfig())
    with pytest.raises(ValueError, match="rendered to no text"):
        server.run_read_page({"url": "https://example.com/"})


def test_run_post_statement_single_file(monkeypatch, tmp_path):
    _fake_cfg(monkeypatch)
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0"}) + "\n")
    text = server.run_post_statement({"account_name": "Bank Statement", "actor_id": "actor-1", "path": str(path)})
    assert "Bank Statement: 1 rows, posted 1 transactions" in text
    tally = json.loads((tmp_path / "result.json").read_text())
    assert tally["accountsCreated"] == 2
    assert tally["accountsReused"] == 0
    assert tally["количество проведенных транзакций"] == 1


def test_run_post_statement_replay_does_not_reclassify_created_accounts(monkeypatch, tmp_path):
    _fake_cfg(monkeypatch)
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0"}) + "\n")
    args = {"account_name": "Bank Statement", "actor_id": "actor-1", "path": str(path)}
    server.run_post_statement(args)
    server.run_post_statement(args)
    tally = json.loads((tmp_path / "result.json").read_text())
    assert tally["accountsCreated"] == 2
    assert tally["accountsReused"] == 0


def test_run_post_statement_batch(monkeypatch, tmp_path):
    _fake_cfg(monkeypatch)
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    p1.write_text(json.dumps({"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0"}) + "\n")
    p2.write_text(json.dumps({"transaction_date": "2026-01-01", "debit_sum": "20", "credit_sum": "0"}) + "\n")

    text = server.run_post_statement({
        "account_name": "State Changes",
        "statements": [
            {"actor_id": "actor-1", "path": str(p1)},
            {"actor_id": "actor-2", "path": str(p2)},
        ],
    })
    assert "State Changes: 2 statement(s)" in text
    assert "actor-1" in text and "actor-2" in text
    assert "total: 2 rows, posted 2 transactions" in text
    tally = json.loads((tmp_path / "result.json").read_text())
    assert tally["accountsCreated"] == 4
    assert tally["accountsReused"] == 0


def test_run_post_statement_resolves_actor_field_per_row(monkeypatch, tmp_path):
    from migration_factory_plugin_mcp.simulator.types import Actor

    sim = _FakeSim(actors_by_query={"item_1=RO1": [Actor(id="actor-1")], "item_1=RO2": [Actor(id="actor-2")]})
    _fake_cfg(monkeypatch, sim=sim)
    (tmp_path / "types.schema.yaml").write_bytes(
        b"types:\n  client:\n    formId: 42\n    fields:\n      iban: {id: item_1}\n"
    )
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps({"transaction_date": "2026-01-01", "debit_sum": "10", "credit_sum": "0", "uniq_actor_field_value": "RO1"}) + "\n"
        + json.dumps({"transaction_date": "2026-01-02", "debit_sum": "0", "credit_sum": "20", "uniq_actor_field_value": "RO2"}) + "\n"
    )
    text = server.run_post_statement({
        "account_name": "Bank Statement", "actor_field": "iban", "actor_type": "client",
        "path": str(path), "dir": str(tmp_path),
    })
    assert "posted 2 transactions" in text
    assert "across 2 actors" in text
    tally = json.loads((tmp_path / "result.json").read_text())
    posted = tally["проведенные выписки"][0]
    assert sorted(posted["акторы"]) == ["actor-1", "actor-2"]


def test_run_post_statement_requires_account_name():
    import pytest

    with pytest.raises(ValueError, match="no account name"):
        server.run_post_statement({})


def test_run_post_statement_requires_workspace(monkeypatch):
    import pytest

    cfg = config.Config(base_url="", api_key="key", workspace_id="", group_id=0)
    monkeypatch.setattr(config, "load_config", lambda override: cfg)
    with pytest.raises(ValueError, match="no workspace"):
        server.run_post_statement({"account_name": "Bank Statement", "actor_id": "a", "path": "x.jsonl"})
