import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import render, tree as tree_mod  # noqa: E402


def _sample_tree():
    a = tree_mod.Actor(id="a-1", title="ACME", form_id=1)
    b = tree_mod.Actor(id="b-1", title="Employee #1", form_id=2)
    f = tree_mod.File(layer_id="layer-1", actors=[a, b], edges=[tree_mod.Edge(source="a-1", target="b-1")])
    tree, _ = tree_mod.build_tree(f)
    return tree


def test_render_ids_no_html_escaping_and_preserves_order():
    tree = _sample_tree()
    rendered = render.render_ids(tree).decode("utf-8")
    assert "\\u003e" not in rendered  # no HTML-escaping of ">"
    assert " > " in rendered
    doc = json.loads(rendered)
    assert doc["layer"] == "layer-1"
    assert doc["sep"] == " > "
    assert list(doc["paths"].keys()) == ["ACME", "ACME > Employee #1"]


def test_parse_ids_roundtrip():
    tree = _sample_tree()
    data = render.render_ids(tree)
    layer_id, idx = render.parse_ids(data)
    assert layer_id == "layer-1"
    assert idx.resolve("Employee #1").id == "b-1"


def test_parse_ids_rejects_wrong_separator():
    data = b'{"layer": "x", "sep": "/", "paths": {"a": "1"}}'
    with pytest.raises(ValueError, match="separator"):
        render.parse_ids(data)


def test_parse_ids_rejects_empty_paths():
    data = b'{"layer": "x", "sep": " > ", "paths": {}}'
    with pytest.raises(ValueError, match="no paths"):
        render.parse_ids(data)


def test_load_ids_missing_file_is_not_an_error(tmp_path):
    layer_id, idx, found = render.load_ids(str(tmp_path))
    assert found is False
    assert idx is None
