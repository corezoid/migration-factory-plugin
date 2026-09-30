"""Client for a Firecrawl v2 instance — one route, POST /v2/scrape: one
address in, the text of that one page out.

Crawling is deliberately absent. The tool over this client exists so the
agent can follow a link it judged worth following, one page at a time.

Auth is one header, Authorization: Bearer <key>. The page itself is fetched by
Firecrawl and not by this process: what a given address may reach is the
provider's policy, and the only host this client ever dials is the instance
it was configured with.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

from .errors import FirecrawlError, parse_error

if TYPE_CHECKING:
    from .scrape import Page

DEFAULT_BASE_URL = "https://dev-firecrawl.corezoid.com"
DEFAULT_TIMEOUT = 120  # seconds — a page renders in seconds; one still hanging after two minutes will not answer
MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # one page of Markdown is kilobytes; a runaway response is refused, not read whole


def normalize_base_url(input: str) -> str:
    """Trim blanks and a trailing slash and add https to a bare host.

    An empty input stays empty, so the caller can tell "unconfigured" from a
    base that was actually set.
    """
    s = input.strip().rstrip("/")
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    return s


class FirecrawlClient:
    """Talks to one Firecrawl instance with one API key."""

    def __init__(self, base_url: str, *, api_key: str = "", user_agent: str = "") -> None:
        self.base_url = normalize_base_url(base_url) or DEFAULT_BASE_URL
        self.api_key = api_key.strip()
        self.user_agent = user_agent or "migration-factory-plugin-mcp/1"

    def scrape(self, page_url: str) -> "Page":
        from .scrape import DEFAULT_SCRAPE_OPTIONS, _scrape

        return _scrape(self, page_url, DEFAULT_SCRAPE_OPTIONS)

    def scrape_with_images(self, page_url: str) -> "Page":
        """Read the page and, with it, the list of pictures on it.

        A separate call rather than an option on every read: the inventory is
        only worth its bytes when something will bind a picture to a node.
        """
        from .scrape import IMAGE_SCRAPE_OPTIONS, _scrape

        return _scrape(self, page_url, IMAGE_SCRAPE_OPTIONS)

    def _do(self, method: str, url: str, body: Optional[bytes] = None) -> dict[str, Any]:
        headers: dict[str, str] = {"User-Agent": self.user_agent}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(url, data=body, method=method, headers=headers)

        try:
            response = urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT)
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            exc.close()
            # Same read-then-classify order as a successful response: an oversized
            # error body is refused rather than handed to parse_error truncated.
            if len(raw) > MAX_RESPONSE_BYTES:
                raise FirecrawlError(message=f"response exceeds {MAX_RESPONSE_BYTES} bytes", cause=exc) from exc
            raise parse_error(exc.code, raw) from exc
        except urllib.error.URLError as exc:
            if _is_timeout(exc.reason):
                message = f"{method} {_redact_path(url)}: timed out or was cancelled"
            else:
                message = f"{method} {_redact_path(url)}: {exc.reason}"
            raise FirecrawlError(message=message, cause=exc) from exc
        except OSError as exc:
            # Not wrapped as a URLError by urllib on this platform/path — still a
            # transport fault, and still worth telling a timeout apart from the rest.
            if _is_timeout(exc):
                message = f"{method} {_redact_path(url)}: timed out or was cancelled"
            else:
                message = f"{method} {_redact_path(url)}: {exc}"
            raise FirecrawlError(message=message, cause=exc) from exc

        with response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status

        if len(raw) > MAX_RESPONSE_BYTES:
            raise FirecrawlError(message=f"response exceeds {MAX_RESPONSE_BYTES} bytes")
        if status < 200 or status >= 300:
            raise parse_error(status, raw)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise FirecrawlError(message=f"decode {_redact_path(url)} response: {exc}", cause=exc) from exc


def _is_timeout(err: Any) -> bool:
    return isinstance(err, (socket.timeout, TimeoutError))


def _redact_path(raw_url: str) -> str:
    """Keep a URL's path for a message without repeating the whole of it back at the caller."""
    try:
        return urlparse(raw_url).path
    except ValueError:
        return raw_url
