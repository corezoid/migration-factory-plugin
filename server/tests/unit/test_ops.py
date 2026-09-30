import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import ops  # noqa: E402


def test_parse_full_ops_file():
    data = b"""
layer: "11111111-1111-4111-8111-111111111111"
source_doc: "invoice_2024.pdf"
mode: upsert
ops:
  - at: "ACME > Finance > Employee #1"
    set:
      role: CFO
    rename: "Ivan Petrov"
    describe: "hired 2024"
    picture: "https://example.com/photo.jpg"
  - type: hrs_employee
    ref: "UA-EDRPOU-12345678"
    set:
      full_name: "New Counterparty LLC"
unrouted:
  - text: "some fact"
    source: "page 4"
    guess: "maybe Employee #2"
    reason: "no matching node found"
"""
    parsed = ops.parse_ops(data)
    assert parsed.layer == "11111111-1111-4111-8111-111111111111"
    assert parsed.mode == "upsert"
    assert len(parsed.ops) == 2
    assert parsed.ops[0].at == "ACME > Finance > Employee #1"
    assert parsed.ops[0].set == {"role": "CFO"}
    assert parsed.ops[1].type == "hrs_employee"
    assert parsed.ops[1].ref == "UA-EDRPOU-12345678"
    assert len(parsed.unrouted) == 1
    assert parsed.unrouted[0].guess == "maybe Employee #2"


def test_default_mode_is_upsert():
    parsed = ops.parse_ops(b"layer: x\nops: []\n")
    assert parsed.mode == "upsert"


def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        ops.parse_ops(b"layer: x\nmode: yolo\nops: []\n")


def test_unknown_top_level_key_rejected():
    with pytest.raises(ValueError, match="unknown field"):
        ops.parse_ops(b"layer: x\nfoo: bar\nops: []\n")


def test_unknown_op_key_is_a_hard_error():
    data = b"""
layer: x
ops:
  - at: "ACME"
    sett:
      role: CFO
"""
    with pytest.raises(ValueError, match="unknown field"):
        ops.parse_ops(data)


def test_load_ops_from_file(tmp_path):
    path = tmp_path / "graph.ops.yaml"
    path.write_bytes(b"layer: x\nops: []\n")
    parsed = ops.load_ops(str(path))
    assert parsed.layer == "x"
