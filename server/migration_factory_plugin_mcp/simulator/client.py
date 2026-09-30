"""A small typed client for the Simulator.Company public API (pong-server
`/papi/1.0`), cut down to what the migration factory tools need: read a
layer's nodes and edges, read an actor and the form behind it, write an actor
back, move money, share access, store files.

Auth is one header, `Authorization: Bearer <workspace API key>` — a key
issued for one workspace on one gateway, and the only credential this client
speaks.

Stdlib only (urllib), one timeout for the whole client, no retries: a single
attempt per request, mirroring the Go original's "small server, no needless
deps" philosophy.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .access import AccessMixin
from .actors import ActorsMixin
from .errors import NoCredentialError, parse_error
from .finance import FinanceMixin
from .layers import LayersMixin
from .storage import StorageMixin

# The public Simulator cloud gateway.
DEFAULT_BASE_URL = "https://mw.simulator.company/papi/1.0"
# Caps every request the client makes.
DEFAULT_TIMEOUT = 60
# The Authorization scheme a workspace API key goes out with.
SCHEME_BEARER = "Bearer"


def _host_of(s: str) -> str:
    """Extracts the bare host from a scheme-less authority, handling the
    bracketed IPv6 form ("[::1]:9000" -> "::1") as well as "host", "host:port"
    and "host/path"."""
    if s.startswith("["):
        end = s.find("]")
        if end > 0:
            return s[1:end]
    for i, ch in enumerate(s):
        if ch in "/:":
            return s[:i]
    return s


def normalize_base_url(input: str) -> str:
    """Turns a user-entered gateway (a bare host, a host:port or a full URL,
    with or without the /papi/<version> prefix) into a canonical base URL:
    https:// unless the host is loopback, /papi/1.0 appended when no /papi/
    segment is present, no trailing slash. An empty input stays empty."""
    s = input.strip()
    if not s:
        return ""
    if "://" not in s:
        scheme = "http" if _host_of(s) in ("localhost", "127.0.0.1", "::1") else "https"
        s = f"{scheme}://{s}"
    s = s.rstrip("/")
    if "/papi/" not in s:
        s += "/papi/1.0"
    return s


def seg(s: str) -> str:
    """Escapes one path segment — including a literal '/', matching Go's
    url.PathEscape."""
    return urllib.parse.quote(s, safe="")


class _ClientCore:
    """Low-level request machinery shared by every endpoint mixin below.
    A client is built for one gateway on behalf of one API key."""

    def __init__(self, base_url: str, *, api_key: str = "", user_agent: str = "") -> None:
        self.base_url = normalize_base_url(base_url) or DEFAULT_BASE_URL
        self.api_key = api_key.strip()
        self.user_agent = user_agent or "migration-factory-plugin-mcp/1"

    def _seg(self, s: str) -> str:
        return seg(s)

    def _get(self, path: str, query: Optional[dict] = None) -> Any:
        return self._call("GET", path, query=query)

    def _call(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict] = None,
        body: Any = None,
        raw_body: Optional[bytes] = None,
        content_type: Optional[str] = None,
        decode: bool = True,
    ) -> Any:
        """Performs one request and decodes the JSON body (nil out discards
        it, which also covers an empty 204)."""
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)

        data: Optional[bytes] = None
        ctype = content_type
        if raw_body is not None:
            data = raw_body
        elif body is not None:
            try:
                data = json.dumps(body).encode("utf-8")
            except (TypeError, ValueError) as e:
                raise RuntimeError(f"simulator: encode request body: {e}") from e
            ctype = "application/json"

        if not self.api_key:
            raise NoCredentialError()

        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Authorization": f"{SCHEME_BEARER} {self.api_key}",
        }
        if ctype:
            headers["Content-Type"] = ctype

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise parse_error(e.code, e.read()) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"simulator: {method} {path}: {e}") from e
        except OSError as e:
            raise RuntimeError(f"simulator: {method} {path}: {e}") from e

        if not decode or status == 204:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"simulator: decode {method} {path} response: {e}") from e


class SimulatorClient(ActorsMixin, AccessMixin, FinanceMixin, LayersMixin, StorageMixin, _ClientCore):
    """One client per (gateway, api key). Composed of the endpoint mixins in
    actors.py, access.py, finance.py, layers.py and storage.py — each mirrors
    one file of the Go client, all methods living on this one type."""
