import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp import config  # noqa: E402

_ALL_ENV_VARS = [
    config.ENV_BASE_URL, config.ENV_API_KEY, config.ENV_WORKSPACE_ID, config.ENV_GROUP_ID,
    config.ENV_DEFAULT_API_KEY, config.ENV_FIRECRAWL_BASE_URL, config.ENV_FIRECRAWL_API_KEY,
    config.ENV_DEFAULT_FIRECRAWL_API_KEY,
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


def test_load_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv(config.ENV_API_KEY, "  atn_abc123  ")
    monkeypatch.setenv(config.ENV_BASE_URL, "mw.simulator.company")
    cfg = config.load_config(None)
    assert cfg.api_key == "atn_abc123"
    # cfg.base_url itself is stored raw; normalization happens lazily when
    # .client() builds the actual SimulatorClient (matching the Go original,
    # where NormalizeBaseURL lives in simulator.New, not in loadConfig).
    assert cfg.base_url == "mw.simulator.company"
    assert cfg.client().base_url == "https://mw.simulator.company/papi/1.0"


def test_load_config_names_the_missing_variable():
    with pytest.raises(ValueError, match=config.ENV_API_KEY):
        config.load_config(None)


def test_load_config_leaves_an_absent_gateway_to_the_client(monkeypatch):
    monkeypatch.setenv(config.ENV_API_KEY, "key")
    cfg = config.load_config(None)
    assert cfg.base_url == ""

    monkeypatch.setenv(config.ENV_BASE_URL, "${SIM_BASE_URL:-fallback}")
    cfg = config.load_config(None)
    assert cfg.base_url == ""


def test_load_config_falls_back_to_the_default_key(monkeypatch):
    monkeypatch.setenv(config.ENV_DEFAULT_API_KEY, "pinned-default")
    cfg = config.load_config(None)
    assert cfg.api_key == "pinned-default"

    monkeypatch.setenv(config.ENV_API_KEY, "caller-key")
    cfg = config.load_config(None)
    assert cfg.api_key == "caller-key"

    monkeypatch.setenv(config.ENV_API_KEY, "${SIM_API_KEY}")
    cfg = config.load_config(None)
    assert cfg.api_key == "pinned-default"


def test_load_config_reads_the_group(monkeypatch):
    monkeypatch.setenv(config.ENV_API_KEY, "key")
    for raw in ["garbage", "0", "-5", "${SIM_GROUP_ID}"]:
        monkeypatch.setenv(config.ENV_GROUP_ID, raw)
        cfg = config.load_config(None)
        assert cfg.group_id == 0
    monkeypatch.setenv(config.ENV_GROUP_ID, "42")
    cfg = config.load_config(None)
    assert cfg.group_id == 42


def test_load_firecrawl_config_falls_back_to_the_pinned_key(monkeypatch):
    monkeypatch.setenv(config.ENV_DEFAULT_FIRECRAWL_API_KEY, "pinned-fc")
    cfg = config.load_firecrawl_config()
    assert cfg.api_key == "pinned-fc"

    monkeypatch.setenv(config.ENV_FIRECRAWL_API_KEY, "caller-fc")
    cfg = config.load_firecrawl_config()
    assert cfg.api_key == "caller-fc"


def test_load_firecrawl_config_names_the_missing_key(monkeypatch):
    monkeypatch.setenv(config.ENV_DEFAULT_FIRECRAWL_API_KEY, "${FIRECRAWL_API_KEY:-fc-pinned}")
    with pytest.raises(ValueError, match=config.ENV_FIRECRAWL_API_KEY):
        config.load_firecrawl_config()


def test_a_call_can_name_the_workspace_it_writes_to(monkeypatch):
    monkeypatch.setenv(config.ENV_API_KEY, "server-key")
    monkeypatch.setenv(config.ENV_BASE_URL, "server.example.com")
    override = config.SimOverride(api_key="call-key")
    cfg = config.load_config(override)
    assert cfg.api_key == "call-key"
    # Unset override fields fall back to the server-level env config.
    assert cfg.base_url == "server.example.com"
    assert cfg.client().base_url == "https://server.example.com/papi/1.0"


def test_what_one_call_names_does_not_reach_the_next(monkeypatch):
    monkeypatch.setenv(config.ENV_API_KEY, "server-key")
    cfg1 = config.load_config(config.SimOverride(api_key="key-1"))
    cfg2 = config.load_config(config.SimOverride(api_key="key-2"))
    cfg3 = config.load_config(config.SimOverride(api_key="key-3"))
    cfg4 = config.load_config(None)
    assert [cfg1.api_key, cfg2.api_key, cfg3.api_key, cfg4.api_key] == [
        "key-1", "key-2", "key-3", "server-key",
    ]


def test_an_unexpanded_placeholder_in_a_call_is_not_a_key(monkeypatch):
    monkeypatch.setenv(config.ENV_API_KEY, "server-key")
    cfg = config.load_config(config.SimOverride(api_key="${SIM_API_KEY}"))
    assert cfg.api_key == "server-key"


def test_missing_key_names_both_ways_to_give_one():
    with pytest.raises(ValueError) as exc_info:
        config.load_config(None)
    msg = str(exc_info.value)
    assert "sim.api_key" in msg
    assert config.ENV_API_KEY in msg


def test_resolve_path_absolute_and_relative(monkeypatch, tmp_path):
    monkeypatch.delenv(config.ENV_CWD, raising=False)
    assert config.resolve_path("/already/absolute") == "/already/absolute"

    monkeypatch.setenv(config.ENV_CWD, str(tmp_path))
    assert config.resolve_path("sub/dir") == os.path.join(str(tmp_path), "sub/dir")

    monkeypatch.delenv(config.ENV_CWD, raising=False)
    assert config.resolve_path(".") == os.path.abspath(".")
