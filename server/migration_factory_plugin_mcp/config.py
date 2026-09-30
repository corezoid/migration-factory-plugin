"""The whole configuration of this server, read from the environment plus an
optional per-call override (see SimOverride) — a host that shares one server
process across many workspaces (Hermes) sends the workspace's own credentials
with every call instead of relying on the process environment.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

# How launch-mcp hands over the directory the MCP client started it in — a
# relative path in a tool call resolves against the user's project, which is
# what makes `dir: "."` mean anything.
ENV_CWD = "MIGRATION_FACTORY_PLUGIN_CWD"


def resolve_path(path: str) -> str:
    path = (path or "").strip()
    if os.path.isabs(path):
        return os.path.normpath(path)
    base = env(ENV_CWD)
    if base:
        return os.path.join(base, path)
    return os.path.abspath(path)


def first_non_empty(*values: str) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""

from .firecrawl.client import FirecrawlClient
from .simulator.client import SimulatorClient

log = logging.getLogger("migration-factory-plugin")

SERVER_NAME = "migration-factory-plugin"
SERVER_VERSION = "0.1.0"

# Simulator gateway. A bare host works — NormalizeBaseURL turns it into a full
# URL. Unset is not an error but the client's own DefaultBaseURL.
ENV_BASE_URL = "SIM_BASE_URL"
# Workspace API key issued at account.corezoid.com.
ENV_API_KEY = "SIM_API_KEY"
# The workspace (accId) uploads go into; a file upload names its workspace in
# the path, which the API key alone cannot say.
ENV_WORKSPACE_ID = "SIM_WORKSPACE_ID"
# The Single Account group every record a run creates is shared to.
ENV_GROUP_ID = "SIM_GROUP_ID"
# What ENV_API_KEY falls back to — the one a deployer pins, kept separate so a
# caller's real key (set in the process environment) is never displaced by a
# literal value an mcp.json spells out.
ENV_DEFAULT_API_KEY = "DEFAULT_SIM_API_KEY"

ENV_FIRECRAWL_BASE_URL = "FIRECRAWL_BASE_URL"
ENV_FIRECRAWL_API_KEY = "FIRECRAWL_API_KEY"
ENV_DEFAULT_FIRECRAWL_API_KEY = "DEFAULT_FIRECRAWL_API_KEY"


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def first_configured(*values: str) -> str:
    """First value that is non-empty and not an unexpanded ``${...}`` literal
    (an Agent Plugins v1 mcp.json has no ``${VAR:-fallback}`` expansion, so an
    unexpanded placeholder must not be mistaken for a real value)."""
    for v in values:
        if v and not v.startswith("${"):
            return v
    return ""


@dataclass
class SimOverride:
    """The workspace one call speaks to, handed over by whoever owns the run
    instead of read from the server's environment. Every field is optional; an
    absent one leaves the environment exactly as it was."""

    base_url: str = ""
    api_key: str = ""
    workspace_id: str = ""
    group_id: int = 0

    @staticmethod
    def from_dict(data: Optional[dict]) -> "Optional[SimOverride]":
        if not data:
            return None
        group_id = data.get("group_id") or 0
        try:
            group_id = int(group_id)
        except (TypeError, ValueError):
            group_id = 0
        return SimOverride(
            base_url=str(data.get("base_url") or "").strip(),
            api_key=str(data.get("api_key") or "").strip(),
            workspace_id=str(data.get("workspace_id") or "").strip(),
            group_id=group_id,
        )


def sim_schema() -> dict:
    """The ``sim`` argument as every tool advertises it."""
    return {
        "type": "object",
        "description": (
            "The Simulator workspace THIS run writes to, when the caller named one. "
            "Pass it unchanged on every call to this plugin's tools — it is what decides whose layer is written, "
            "and leaving it out on one call sends that call to the server's own workspace instead."
        ),
        "properties": {
            "base_url": {"type": "string", "description": "Gateway of that workspace."},
            "api_key": {"type": "string", "description": "Workspace API key the layer is written with."},
            "workspace_id": {"type": "string", "description": "Workspace the key belongs to."},
            "group_id": {"type": "integer", "description": "Group every created record is shared to."},
        },
    }


@dataclass
class Config:
    base_url: str
    api_key: str
    workspace_id: str
    group_id: int

    def client(self) -> SimulatorClient:
        return SimulatorClient(self.base_url, api_key=self.api_key, user_agent=f"{SERVER_NAME}/{SERVER_VERSION}")


_no_group_notice = threading.Lock()
_no_group_notice_shown = False


def _group_id() -> int:
    raw = first_configured(env(ENV_GROUP_ID))
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value > 0:
        return value
    global _no_group_notice_shown
    with _no_group_notice:
        if not _no_group_notice_shown:
            _no_group_notice_shown = True
            what = "unset" if not raw else f"{raw!r} is not a positive integer"
            log.info("%s %s: records a run creates are shared to no group", ENV_GROUP_ID, what)
    return 0


def load_config(override: Optional[SimOverride]) -> Config:
    cfg = Config(
        base_url=first_configured(env(ENV_BASE_URL)),
        api_key=first_configured(env(ENV_API_KEY), env(ENV_DEFAULT_API_KEY)),
        workspace_id=first_configured(env(ENV_WORKSPACE_ID)),
        group_id=_group_id(),
    )
    # The call wins over the environment, field by field.
    if override is not None:
        cfg.base_url = first_configured(override.base_url, cfg.base_url)
        cfg.api_key = first_configured(override.api_key, cfg.api_key)
        cfg.workspace_id = first_configured(override.workspace_id, cfg.workspace_id)
        if override.group_id > 0:
            cfg.group_id = override.group_id
    if not cfg.api_key:
        raise ValueError(
            f"no Simulator API key: pass `sim.api_key` with the call, or set {ENV_API_KEY} "
            f"in the MCP server's env (or {ENV_DEFAULT_API_KEY} for a fallback)"
        )
    return cfg


@dataclass
class FirecrawlConfig:
    base_url: str
    api_key: str

    def client(self) -> FirecrawlClient:
        return FirecrawlClient(self.base_url, api_key=self.api_key, user_agent=f"{SERVER_NAME}/{SERVER_VERSION}")


def load_firecrawl_config() -> FirecrawlConfig:
    cfg = FirecrawlConfig(
        base_url=first_configured(env(ENV_FIRECRAWL_BASE_URL)),
        api_key=first_configured(env(ENV_FIRECRAWL_API_KEY), env(ENV_DEFAULT_FIRECRAWL_API_KEY)),
    )
    if not cfg.api_key:
        raise ValueError(
            f"no Firecrawl API key: set {ENV_FIRECRAWL_API_KEY} in the MCP server's env "
            f"(or {ENV_DEFAULT_FIRECRAWL_API_KEY} for a fallback) — reading a page needs one, "
            "exporting and applying a layer does not"
        )
    return cfg
