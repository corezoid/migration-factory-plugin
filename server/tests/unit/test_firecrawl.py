"""Unit tests for the Firecrawl client port: base-url normalization, error
mapping (including the provider-message JSON parsing), page-url validation,
and the HTML <img> alt-text scanner. Pure unit tests, no real network calls.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from migration_factory_plugin_mcp.firecrawl import (  # noqa: E402
    Image,
    image_alts,
    normalize_base_url,
    parse_error,
    with_alts,
)
from migration_factory_plugin_mcp.firecrawl.scrape import validate_page_url  # noqa: E402


def test_normalize_base_url_bare_host_gets_https():
    assert normalize_base_url("dev-firecrawl.corezoid.com") == "https://dev-firecrawl.corezoid.com"


def test_normalize_base_url_already_schemed_untouched_modulo_trailing_slash():
    assert normalize_base_url("https://example.com/") == "https://example.com"
    assert normalize_base_url("https://example.com") == "https://example.com"
    assert normalize_base_url("  https://example.com/  ") == "https://example.com"


def test_normalize_base_url_empty_stays_empty():
    assert normalize_base_url("") == ""
    assert normalize_base_url("   ") == ""


def test_parse_error_403_is_non_retryable():
    err = parse_error(403, b'{"success": false, "error": "blocked by target site"}')
    assert err.status == 403
    assert "do not retry it" in err.message
    assert "blocked by target site" in err.message


def test_parse_error_429_is_retryable():
    err = parse_error(429, b"")
    assert err.status == 429
    assert "read the page again in a moment" in err.message
    assert "no body" in err.message


def test_parse_error_401_is_configuration():
    err = parse_error(401, b'{"message": "invalid api key"}')
    assert err.status == 401
    assert "configuration, not the page" in err.message
    assert "invalid api key" in err.message


def test_parse_error_500_is_worth_a_retry():
    err = parse_error(503, b"upstream unwell")
    assert err.status == 503
    assert "reading the page again may well work" in err.message
    assert "upstream unwell" in err.message


def test_parse_error_str_matches_format():
    err = parse_error(429, b'{"error": "slow down"}')
    assert str(err) == "firecrawl: http 429: the provider is rate limiting us — read the page again in a moment: slow down"


def test_provider_message_prefers_error_over_message():
    err = parse_error(400, b'{"success": false, "error": "bad url", "message": "ignored"}')
    assert "bad url" in err.message
    assert "ignored" not in err.message


def test_provider_message_falls_back_to_raw_body():
    err = parse_error(400, b"not json at all")
    assert "not json at all" in err.message


def test_validate_page_url_rejects_fragment():
    try:
        validate_page_url("https://example.com/page#section")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "fragment" in str(exc)


def test_validate_page_url_rejects_localhost():
    try:
        validate_page_url("http://localhost:8080/")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "private" in str(exc)


def test_validate_page_url_rejects_bare_private_ip():
    try:
        validate_page_url("http://192.168.1.1/")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "private" in str(exc)


def test_validate_page_url_accepts_normal_url():
    validate_page_url("https://example.com/page")  # must not raise


def test_image_alts_two_tags_alt_and_title():
    html = """
    <img src="https://example.com/a.png" alt="a > b">
    <img src="https://example.com/b.png" title="only a title">
    """
    alts = image_alts(html)
    assert alts["https://example.com/a.png"] == "a > b"
    assert alts["https://example.com/b.png"] == "only a title"


def test_image_alts_quoted_gt_does_not_break_tag_parsing():
    html = '<img src="https://example.com/a.png" alt="a > b"><img src="https://example.com/b.png" alt="ok">'
    alts = image_alts(html)
    assert len(alts) == 2
    assert alts["https://example.com/a.png"] == "a > b"
    assert alts["https://example.com/b.png"] == "ok"


def test_with_alts_folds_html_alt_into_provider_images():
    html = (
        '<img src="https://example.com/a.png" alt="a > b">'
        '<img src="https://example.com/b.png" title="only a title">'
    )
    images = [Image(url="https://example.com/a.png"), Image(url="https://example.com/b.png")]
    result = with_alts(images, html)
    assert result[0].alt == "a > b"
    assert result[1].alt == "only a title"


def test_with_alts_appends_html_only_images_in_order():
    html = (
        '<img src="https://example.com/known.png" alt="known one">'
        '<img src="https://example.com/extra.png" alt="extra one">'
    )
    images = [Image(url="https://example.com/known.png")]
    result = with_alts(images, html)
    assert [img.url for img in result] == [
        "https://example.com/known.png",
        "https://example.com/extra.png",
    ]
    assert result[1].alt == "extra one"
