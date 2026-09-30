"""Firecrawl v2 HTTP client — render one web page to Markdown, one address at a time."""

from __future__ import annotations

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    MAX_RESPONSE_BYTES,
    FirecrawlClient,
    normalize_base_url,
)
from .errors import FirecrawlError, parse_error, provider_message
from .images import Image, image_alts, parse_image, with_alts
from .scrape import (
    DEFAULT_SCRAPE_OPTIONS,
    IMAGE_SCRAPE_OPTIONS,
    Page,
    PageMetadata,
    is_private_host,
    validate_page_url,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "MAX_RESPONSE_BYTES",
    "FirecrawlClient",
    "normalize_base_url",
    "FirecrawlError",
    "parse_error",
    "provider_message",
    "Image",
    "image_alts",
    "parse_image",
    "with_alts",
    "DEFAULT_SCRAPE_OPTIONS",
    "IMAGE_SCRAPE_OPTIONS",
    "Page",
    "PageMetadata",
    "is_private_host",
    "validate_page_url",
]
