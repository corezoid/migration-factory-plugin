import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp import envfile  # noqa: E402


def test_parse_env_file_reads_what_people_write():
    text = (
        "# a comment\n"
        "\n"
        "export SIM_API_KEY = atn_abc123 \n"
        "SIM_BASE_URL=\"https://mw.simulator.company\"\n"
        "SINGLE='quoted value'\n"
        "ODD=a${NOT_EXPANDED}b\n"
    )
    values = envfile.parse_env_file(text)
    assert values["SIM_API_KEY"] == "atn_abc123"
    assert values["SIM_BASE_URL"] == "https://mw.simulator.company"
    assert values["SINGLE"] == "quoted value"
    assert values["ODD"] == "a${NOT_EXPANDED}b"  # never expanded


def test_parse_env_file_refuses_a_line_that_is_not_one():
    text = "SIM_API_KEY=abc\njust-a-key\n"
    with pytest.raises(ValueError, match="line 2"):
        envfile.parse_env_file(text)


def test_apply_env_file_only_fills_gaps(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_API_KEY", "from-the-environment")
    monkeypatch.setenv("SIM_BASE_URL", "")  # empty counts as a gap
    path = tmp_path / ".env"
    path.write_text("SIM_API_KEY=from-the-file\nSIM_BASE_URL=from-the-file\n")

    applied = envfile.apply_env_file(str(path))
    assert applied == 1
    assert os.environ["SIM_API_KEY"] == "from-the-environment"
    assert os.environ["SIM_BASE_URL"] == "from-the-file"


def test_env_file_path_prefers_the_one_named_outright(monkeypatch, tmp_path):
    monkeypatch.setenv(envfile.ENV_ENV_FILE, str(tmp_path / "explicit.env"))
    monkeypatch.setenv(envfile.ENV_PLUGIN_DATA, str(tmp_path))
    path, explicit = envfile.env_file_path()
    assert path == str(tmp_path / "explicit.env")
    assert explicit is True

    monkeypatch.delenv(envfile.ENV_ENV_FILE, raising=False)
    path, explicit = envfile.env_file_path()
    assert path == str(tmp_path / ".env")
    assert explicit is False

    monkeypatch.delenv(envfile.ENV_PLUGIN_DATA, raising=False)
    path, explicit = envfile.env_file_path()
    assert path == ""
    assert explicit is False


def test_load_env_file_carries_the_key_into_the_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SIM_API_KEY", raising=False)
    monkeypatch.setenv(envfile.ENV_PLUGIN_DATA, str(tmp_path))
    monkeypatch.delenv(envfile.ENV_ENV_FILE, raising=False)
    (tmp_path / ".env").write_text("SIM_API_KEY=from-file-key\n")

    envfile.load_env_file()
    assert os.environ["SIM_API_KEY"] == "from-file-key"

    from migration_factory_plugin_mcp import config

    cfg = config.load_config(None)
    assert cfg.api_key == "from-file-key"


def test_load_env_file_is_silent_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv(envfile.ENV_PLUGIN_DATA, str(tmp_path))
    monkeypatch.delenv(envfile.ENV_ENV_FILE, raising=False)
    # no .env under tmp_path
    envfile.load_env_file()  # must not raise
