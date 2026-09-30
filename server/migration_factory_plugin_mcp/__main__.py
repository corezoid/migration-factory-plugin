"""Entrypoint: python -m migration_factory_plugin_mcp. MCP over stdio, so
everything on stdout is protocol; logs go to stderr.
"""
from __future__ import annotations

import logging
import sys

import anyio

from .server import run_stdio


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="migration-factory-plugin: %(message)s")
    anyio.run(run_stdio)


if __name__ == "__main__":
    main()
