"""Unit tests for the Simulator client port: error-envelope parsing,
is_duplicate_ref's exact string-search algorithm, base-url normalization, and
seg()'s path-segment escaping. Pure unit tests of pure functions, no network
calls.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.simulator import (  # noqa: E402
    SimulatorError,
    is_duplicate_ref,
    normalize_base_url,
    parse_error,
    seg,
)


def test_is_duplicate_ref_plain_message_shape():
    err = parse_error(400, b'{"message": "Not unique ref: abc123"}')
    assert is_duplicate_ref(err)


def test_is_duplicate_ref_string_shaped_error_envelope():
    # A string-shaped {"error": …} lands in .code, not .message.
    err = parse_error(400, b'{"error": "Not unique ref"}')
    assert err.code == "Not unique ref"
    assert err.message == ""
    assert is_duplicate_ref(err)


def test_is_duplicate_ref_false_for_other_400s():
    err = parse_error(400, b'{"message": "some other rejection"}')
    assert not is_duplicate_ref(err)


def test_is_duplicate_ref_false_for_non_400_status():
    err = parse_error(409, b'{"message": "Not unique ref"}')
    assert not is_duplicate_ref(err)


def test_is_duplicate_ref_false_for_non_simulator_error():
    assert not is_duplicate_ref(RuntimeError("Not unique ref"))


def test_parse_error_nested_error_object_shape():
    err = parse_error(400, b'{"error": {"message": "bad payload", "code": "VALIDATION"}}')
    assert err.status_code == 400
    assert err.message == "bad payload"
    assert err.code == "VALIDATION"


def test_parse_error_nested_error_object_falls_back_to_type():
    err = parse_error(400, b'{"error": {"message": "bad payload", "type": "ValidationError"}}')
    assert err.code == "ValidationError"


def test_parse_error_bare_message_and_description_shape():
    err = parse_error(500, b'{"description": "upstream unwell"}')
    assert err.message == "upstream unwell"
    assert err.code == ""


def test_parse_error_message_wins_over_description():
    err = parse_error(500, b'{"message": "primary", "description": "secondary"}')
    assert err.message == "primary"


def test_parse_error_unparseable_body_falls_back_to_raw_text():
    err = parse_error(502, b"upstream gateway exploded")
    assert err.message == "upstream gateway exploded"
    assert err.body == "upstream gateway exploded"


def test_parse_error_str_format_with_code_and_message():
    err = parse_error(400, b'{"error": {"message": "bad payload", "code": "VALIDATION"}}')
    assert str(err) == "simulator: http 400 (VALIDATION): bad payload"


def test_parse_error_str_format_falls_back_to_truncated_body():
    err = SimulatorError(status_code=500, body="x" * 600)
    text = str(err)
    assert text.startswith("simulator: http 500: ")
    assert text.endswith("…")
    assert len(text) < 600


def test_normalize_base_url_bare_host_gets_https_and_papi_suffix():
    assert normalize_base_url("mw.simulator.company") == "https://mw.simulator.company/papi/1.0"


def test_normalize_base_url_host_with_existing_papi_segment_untouched():
    assert normalize_base_url("mw.simulator.company/papi/2.0") == "https://mw.simulator.company/papi/2.0"


def test_normalize_base_url_localhost_gets_http():
    assert normalize_base_url("localhost:8080") == "http://localhost:8080/papi/1.0"


def test_normalize_base_url_loopback_ip_gets_http():
    assert normalize_base_url("127.0.0.1:9000") == "http://127.0.0.1:9000/papi/1.0"


def test_normalize_base_url_ipv6_loopback_gets_http():
    assert normalize_base_url("[::1]:9000") == "http://[::1]:9000/papi/1.0"


def test_normalize_base_url_full_https_url_untouched_modulo_trailing_slash():
    assert normalize_base_url("https://mw.simulator.company/papi/1.0/") == "https://mw.simulator.company/papi/1.0"
    assert normalize_base_url("https://mw.simulator.company/papi/1.0") == "https://mw.simulator.company/papi/1.0"


def test_normalize_base_url_empty_stays_empty():
    assert normalize_base_url("") == ""
    assert normalize_base_url("   ") == ""


def test_seg_escapes_embedded_slash():
    assert seg("a/b") == "a%2Fb"


def test_seg_escapes_space_and_leaves_simple_ref_alone():
    assert seg("simple-ref-123") == "simple-ref-123"
    assert seg("a b") == "a%20b"
