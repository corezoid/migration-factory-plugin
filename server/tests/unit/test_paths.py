import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import paths as paths_mod  # noqa: E402


def _idx(*pairs):
    return paths_mod.PathIndex.build([paths_mod.PathEntry(path=p, id=i) for p, i in pairs])


def test_exact_and_suffix_match():
    idx = _idx(("ACME", "id-1"), ("ACME > Finance > Employee #1", "id-2"))
    assert idx.resolve("ACME").id == "id-1"
    assert idx.resolve("Employee #1").id == "id-2"
    assert idx.resolve("Finance > Employee #1").id == "id-2"


def test_suffix_match_is_segment_boundary_safe():
    idx = _idx(("ACME > Employee #1", "id-1"), ("ACME > Employee #10", "id-2"))
    # "Employee #1" must not incorrectly match the "#10" entry.
    assert idx.resolve("Employee #1").id == "id-1"


def test_no_match_raises_no_match_error():
    idx = _idx(("ACME", "id-1"))
    with pytest.raises(paths_mod.NoMatchError):
        idx.resolve("Nonexistent")


def test_ambiguous_match_raises_and_lists_candidates():
    idx = _idx(("A > X", "id-1"), ("B > X", "id-2"))
    with pytest.raises(paths_mod.AmbiguousMatchError, match="A > X"):
        idx.resolve("X")


def test_discriminator_stripped_retry():
    idx = _idx(("ACME > Employee #1@aaaa", "id-1"), ("ACME > Employee #1@bbbb", "id-2"))
    # Typed without knowing about the @hex discriminator -> ambiguous (two
    # siblings share the undecorated name).
    with pytest.raises(paths_mod.AmbiguousMatchError):
        idx.resolve("Employee #1")
    # But the fully-decorated address resolves directly on the first pass.
    assert idx.resolve("Employee #1@aaaa").id == "id-1"


def test_discriminator_with_short_or_nonhex_tail_is_left_alone():
    # An "@" tail under 4 hex chars, or non-hex, is a real title character,
    # not a discriminator - it must not be stripped.
    idx = _idx(("Support@ab", "id-1"),)
    assert idx.resolve("Support@ab").id == "id-1"


def test_type_suffix_hint_on_no_match():
    idx = _idx(("ACME", "id-1"),)
    with pytest.raises(paths_mod.NoMatchError, match="ACME"):
        idx.resolve("ACME [company]")


def test_path_title_strips_discriminator():
    assert paths_mod.path_title("ACME > Employee #1@aaaa") == "Employee #1"
    assert paths_mod.path_title("ACME") == "ACME"


def test_rename_replaces_only_last_segment():
    assert paths_mod.rename("ACME > Finance > Ivan", "Ivan Petrov") == "ACME > Finance > Ivan Petrov"
    assert paths_mod.rename("ACME", "New Name") == "New Name"


def test_by_id_lookup():
    idx = _idx(("ACME", "id-1"), ("ACME > Finance", "id-2"))
    assert idx.by_id_lookup("id-2").path == "ACME > Finance"
    with pytest.raises(paths_mod.NoMatchError):
        idx.by_id_lookup("missing-id")
