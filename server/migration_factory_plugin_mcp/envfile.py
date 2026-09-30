"""A portable Agent Plugins v1 host (Hermes) starts this server with a
filtered environment — a fixed safe list plus whatever mcp.json spells out —
so a workspace API key can't travel as a literal in that public file. The fix:
read credentials from a ``.env`` file inside PLUGIN_DATA, the per-package
writable directory the host grants, before anything else reads the
environment. The process environment always wins — a file can only fill a
gap, never displace what the caller deliberately set.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("migration-factory-plugin")

ENV_ENV_FILE = "MIGRATION_FACTORY_PLUGIN_ENV_FILE"
ENV_PLUGIN_DATA = "PLUGIN_DATA"
ENV_FILE_NAME = ".env"


def env_file_path() -> tuple[str, bool]:
    explicit = os.environ.get(ENV_ENV_FILE, "").strip()
    if explicit:
        return explicit, True
    plugin_data = os.environ.get(ENV_PLUGIN_DATA, "").strip()
    if plugin_data:
        return os.path.join(plugin_data, ENV_FILE_NAME), False
    return "", False


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_file(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            raise ValueError(f"line {i} is not NAME=VALUE")
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            raise ValueError(f"line {i} is not NAME=VALUE")
        # No expansion of any kind — the file holds secrets, and a secret
        # containing "$" must not be treated as a reference.
        result[name] = _unquote(value.strip())
    return result


def apply_env_file(path: str) -> int:
    with open(path, "r", encoding="utf-8") as f:
        values = parse_env_file(f.read())
    applied = 0
    for name, value in values.items():
        if os.environ.get(name, "") == "":
            os.environ[name] = value
            applied += 1
    return applied


def load_env_file() -> None:
    path, explicit = env_file_path()
    if not path:
        return
    try:
        applied = apply_env_file(path)
    except FileNotFoundError:
        if explicit:
            log.info("%s=%s: no such file", ENV_ENV_FILE, path)
        # Conventional PLUGIN_DATA/.env location: absent is the normal case
        # (a host that passes the environment through never needs this file).
        return
    except OSError as exc:
        log.info("%s: %s", path, exc)
        return
    except ValueError as exc:
        log.info("%s: %s", path, exc)
        return
    if applied:
        log.info("%s: filled %d variable(s) from the environment", path, applied)
