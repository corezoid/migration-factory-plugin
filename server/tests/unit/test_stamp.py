import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.graph import stamp  # noqa: E402


def _write(tmp_path, text):
    path = tmp_path / "graph.ops.yaml"
    path.write_text(text)
    return str(path)


def test_stamps_id_after_at_key_preserving_everything_else(tmp_path):
    original = (
        "layer: x\n"
        "ops:\n"
        "  - at: \"ACME > Finance > Employee #1\"\n"
        "    set:\n"
        "      role: CFO\n"
        "\n"
        "  # a comment worth keeping\n"
        "  - at: \"ACME > Docs\"\n"
        "    describe: hello\n"
    )
    path = _write(tmp_path, original)
    count, warnings = stamp.stamp_ops(path, {1: "uuid-1111", 2: "uuid-2222"})
    assert count == 2
    assert warnings == []

    new_text = open(path).read()
    lines = new_text.split("\n")
    assert lines[2] == '  - at: "ACME > Finance > Employee #1"'
    assert lines[3] == "    id: uuid-1111"
    assert lines[4] == "    set:"
    # Blank line and comment between ops must survive untouched.
    assert lines[6] == ""
    assert lines[7] == "  # a comment worth keeping"
    assert lines[8] == '  - at: "ACME > Docs"'
    assert lines[9] == "    id: uuid-2222"
    assert lines[10] == "    describe: hello"


def test_already_stamped_op_is_left_alone(tmp_path):
    original = 'layer: x\nops:\n  - at: "ACME"\n    id: existing-uuid\n'
    path = _write(tmp_path, original)
    count, warnings = stamp.stamp_ops(path, {1: "should-not-be-used"})
    assert count == 0
    assert warnings == []
    assert open(path).read() == original


def test_unresolved_op_is_skipped(tmp_path):
    original = 'layer: x\nops:\n  - at: "ACME"\n'
    path = _write(tmp_path, original)
    count, warnings = stamp.stamp_ops(path, {})
    assert count == 0
    assert open(path).read() == original


def test_record_op_anchors_on_type_then_ref(tmp_path):
    original = 'layer: x\nops:\n  - type: hrs_employee\n    ref: "UA-123"\n    set:\n      full_name: X\n'
    path = _write(tmp_path, original)
    count, warnings = stamp.stamp_ops(path, {1: "uuid-3333"})
    assert count == 1
    lines = open(path).read().split("\n")
    # Anchored under `type:`, the first key in the preference list that's
    # present on a record op.
    assert lines[2] == "  - type: hrs_employee"
    assert lines[3] == "    id: uuid-3333"


def test_block_scalar_anchor_is_not_stamped_with_warning(tmp_path):
    original = (
        "layer: x\n"
        "ops:\n"
        "  - describe: |\n"
        "      multi\n"
        "      line\n"
    )
    path = _write(tmp_path, original)
    count, warnings = stamp.stamp_ops(path, {1: "uuid-4444"})
    assert count == 0
    assert len(warnings) == 1
    assert "op #1" in warnings[0]
    assert open(path).read() == original


def test_multiline_double_quoted_value_is_not_stamped(tmp_path):
    original = 'layer: x\nops:\n  - at: "ACME\n      > Finance"\n'
    path = _write(tmp_path, original)
    count, warnings = stamp.stamp_ops(path, {1: "uuid-5555"})
    assert count == 0
    assert len(warnings) == 1


def test_multiple_inserts_use_original_line_numbers_bottom_up(tmp_path):
    # Three ops; stamping all three must not corrupt line numbers for the
    # earlier ones because of insertions made after (bottom-up) ones.
    original = (
        "layer: x\n"
        "ops:\n"
        "  - at: \"A\"\n"
        "  - at: \"B\"\n"
        "  - at: \"C\"\n"
    )
    path = _write(tmp_path, original)
    count, warnings = stamp.stamp_ops(path, {1: "id-a", 2: "id-b", 3: "id-c"})
    assert count == 3
    lines = open(path).read().split("\n")
    assert lines == [
        "layer: x",
        "ops:",
        '  - at: "A"',
        "    id: id-a",
        '  - at: "B"',
        "    id: id-b",
        '  - at: "C"',
        "    id: id-c",
        "",
    ]


def test_missing_ops_key_raises(tmp_path):
    path = _write(tmp_path, "layer: x\n")
    import pytest

    with pytest.raises(ValueError, match="ops"):
        stamp.stamp_ops(path, {1: "uuid"})


def test_preserves_file_permissions(tmp_path):
    path = _write(tmp_path, 'layer: x\nops:\n  - at: "A"\n')
    os.chmod(path, 0o640)
    stamp.stamp_ops(path, {1: "uuid-6666"})
    assert (os.stat(path).st_mode & 0o777) == 0o640
