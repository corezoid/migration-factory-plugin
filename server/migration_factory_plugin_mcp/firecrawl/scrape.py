"""POST /v2/scrape: render one page to Markdown in a single synchronous call.

No traversal: just this URL, its main content, as Markdown — a site root and
a deep link are read the same way, and whether to read further into the site
is the caller's decision, not the connector's.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .images import Image, parse_image, with_alts

if TYPE_CHECKING:
    from .client import FirecrawlClient


@dataclass
class PageMetadata:
    source_url: str = ""
    url: str = ""
    title: str = ""
    og_image: str = ""
    favicon: str = ""
    status_code: int = 0


@dataclass
class Page:
    markdown: str = ""
    images: list[Image] = field(default_factory=list)
    # The cleaned markup, asked for only alongside the images to recover alt
    # text; folded into images and always cleared before returning to the caller.
    html: str = ""
    metadata: PageMetadata = field(default_factory=PageMetadata)

    def address(self) -> str:
        """The page's own address as the provider ended up at it, falling back to the one asked for."""
        return self.metadata.url or self.metadata.source_url or ""


DEFAULT_SCRAPE_OPTIONS: dict[str, Any] = {
    "formats": ["markdown"],
    "onlyMainContent": True,
    "removeBase64Images": True,
    "blockAds": True,
}

# The text is still main-content only; the image list is not filtered that
# way — asking for it is the point, and an inventory missing the one picture
# the page is about would be worse than no inventory. HTML rides along only
# for the alt texts the bare address list does not carry, and is discarded
# once folded in — it costs a larger response, never a larger context.
IMAGE_SCRAPE_OPTIONS: dict[str, Any] = {
    "formats": ["markdown", "images", "html"],
    "onlyMainContent": True,
    "removeBase64Images": True,
    "blockAds": True,
}


def _parse_metadata(raw: Any) -> PageMetadata:
    if not isinstance(raw, dict):
        return PageMetadata()
    return PageMetadata(
        source_url=str(raw.get("sourceURL") or ""),
        url=str(raw.get("url") or ""),
        title=str(raw.get("title") or ""),
        og_image=str(raw.get("ogImage") or ""),
        favicon=str(raw.get("favicon") or ""),
        status_code=int(raw.get("statusCode") or 0),
    )


def _parse_page(raw: Any) -> Page:
    if not isinstance(raw, dict):
        raw = {}
    images = [parse_image(item) for item in (raw.get("images") or [])]
    return Page(
        markdown=str(raw.get("markdown") or ""),
        images=images,
        html=str(raw.get("html") or ""),
        metadata=_parse_metadata(raw.get("metadata")),
    )


def _scrape(client: "FirecrawlClient", page_url: str, options: dict[str, Any]) -> Page:
    validate_page_url(page_url)
    body = json.dumps({"url": page_url.strip(), **options}).encode("utf-8")
    envelope = client._do("POST", f"{client.base_url}/v2/scrape", body)
    page = _parse_page(envelope.get("data"))
    if "images" in options["formats"]:
        page.images = with_alts(page.images, page.html)
    page.html = ""
    return page


def validate_page_url(raw: str) -> None:
    """Cheap deterministic gate before the call: a real host, no fragment, http/https, not obviously private.

    Not a security boundary — the page is fetched by the provider, not by
    this process, and what it may reach is the provider's policy. It exists
    so an address that was never going to work comes back as a sentence the
    caller can act on instead of as a 400 from a third party.
    """
    try:
        parsed = urlparse(raw.strip())
    except ValueError as exc:
        raise ValueError(f"unparseable url: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        scheme_desc = (
            "missing one (write the address with https://)" if not parsed.scheme else parsed.scheme.lower()
        )
        raise ValueError(f"url must be http or https, and this one is {scheme_desc}")
    if parsed.fragment:
        raise ValueError(
            "url must not carry a #fragment — it names a place inside a page, and the whole page is what is read"
        )
    host = parsed.hostname or ""
    if not host:
        raise ValueError("url has no host")
    if is_private_host(host):
        raise ValueError(f"host {host} is private, and the provider reads the page from the public internet")


def is_private_host(host: str) -> bool:
    """Catch the hosts we can rule out without resolving: localhost, .local, a literal private/loopback IP.

    Not a security boundary: Firecrawl fetches the page, not this process.
    This only avoids a doomed round trip to an address that obviously won't answer.
    """
    h = host.lower().rstrip(".")
    if h == "localhost" or h.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_unspecified or ip.is_link_local or ip.is_multicast
