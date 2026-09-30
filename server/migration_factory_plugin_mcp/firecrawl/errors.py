"""Errors for the Firecrawl client.

There is no error class hierarchy here: the one reader of these messages is a
model deciding what to do about a page it could not read, and what it needs is
not a code to switch on but the difference between "this page will never be
readable", "try it again" and "the key is wrong" — which is what parse_error
writes into the message text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class FirecrawlError(Exception):
    status: int = 0
    message: str = ""
    cause: Optional[Exception] = None

    def __str__(self) -> str:
        if self.status:
            return f"firecrawl: http {self.status}: {self.message}"
        return f"firecrawl: {self.message}"


def parse_error(status: int, body: bytes) -> FirecrawlError:
    """Map a non-2xx status to an error that says what it means for the caller's next move."""
    if status == 429:
        hint = "the provider is rate limiting us — read the page again in a moment"
    elif status == 403:
        hint = "the site refused the provider; this address cannot be read at all, so do not retry it"
    elif status == 404:
        hint = "there is no page at that address — check the link you followed it from"
    elif status == 400:
        hint = "the address was rejected as malformed"
    elif status in (401, 402):
        hint = "the Firecrawl key was rejected or is out of credit — configuration, not the page"
    elif status >= 500:
        hint = "the Firecrawl instance itself failed — reading the page again may well work"
    else:
        hint = "the provider refused the request"
    return FirecrawlError(status=status, message=f"{hint}: {provider_message(body)}")


def provider_message(body: bytes) -> str:
    """Pull the human message out of Firecrawl's error body, falling back to the raw body."""
    try:
        obj = json.loads(body) if body else None
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
        obj = None
    if isinstance(obj, dict):
        error = obj.get("error")
        if isinstance(error, str) and error:
            return error
        message = obj.get("message")
        if isinstance(message, str) and message:
            return message
    text = body.decode("utf-8", errors="replace") if body else ""
    if text:
        return text[:300] + "…" if len(text) > 300 else text
    return "no body"
