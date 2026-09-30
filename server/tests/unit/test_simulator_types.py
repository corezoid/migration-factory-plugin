import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.simulator.types import Actor  # noqa: E402


def test_actor_from_json_decodes_created_at():
    actor = Actor.from_json({"id": "a-1", "createdAt": "2026-06-01T00:00:00Z"})
    assert actor.created_at == "2026-06-01T00:00:00Z"


def test_actor_from_json_created_at_defaults_empty():
    actor = Actor.from_json({"id": "a-1"})
    assert actor.created_at == ""
