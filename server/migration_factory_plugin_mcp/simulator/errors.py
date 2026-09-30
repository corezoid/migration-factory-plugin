"""Error shapes the Simulator gateway hands back, and the helpers callers use
to tell one kind of failure from another without parsing messages themselves.
"""
from __future__ import annotations

import json


class NoCredentialError(Exception):
    """No API key is configured; raised before the request leaves the process."""

    def __init__(self) -> None:
        super().__init__("simulator: no api key set")


class SimulatorError(Exception):
    """A non-2xx response from the gateway. The platform is not uniform about
    its error envelope, so message is filled best-effort from the shapes it
    does use and body always keeps the raw payload."""

    def __init__(self, status_code: int, message: str = "", code: str = "", body: str = "") -> None:
        self.status_code = status_code
        self.message = message
        self.code = code
        self.body = body
        super().__init__(str(self))

    def __str__(self) -> str:
        text = f"simulator: http {self.status_code}"
        if self.code:
            text += f" ({self.code})"
        if self.message:
            text += f": {self.message}"
        elif self.body:
            text += f": {_truncate(self.body, 512)}"
        return text


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "…"


def parse_error(status: int, body: bytes) -> SimulatorError:
    """Converts a non-2xx response into a SimulatorError.

    The shapes seen in the wild: Fastify's {statusCode,error,message}, a
    nested {error:{message,code}}, and a bare {message} / {description}.
    """
    raw = body[: 1 << 20]
    text = raw.decode("utf-8", errors="replace")

    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return SimulatorError(status_code=status, message=text.strip(), body=text)
    if not isinstance(envelope, dict):
        return SimulatorError(status_code=status, message=text.strip(), body=text)

    message = envelope.get("message") or envelope.get("description") or ""
    code = ""
    error = envelope.get("error")
    if isinstance(error, str):
        # A string-shaped {"error": …} is a code, not a message.
        code = error
    elif isinstance(error, dict):
        message = message or error.get("message", "")
        code = error.get("code") or error.get("type") or ""

    return SimulatorError(status_code=status, message=message, code=code, body=text)


def status_code(err: Exception) -> int:
    """The HTTP status behind err, or 0 if it is not a SimulatorError."""
    if isinstance(err, SimulatorError):
        return err.status_code
    return 0


def is_not_found(err: Exception) -> bool:
    """A 404 — no such actor or form."""
    return status_code(err) == 404


def is_bad_request(err: Exception) -> bool:
    """A 400 — a rejected payload, e.g. actor data keyed by field titles
    instead of item ids, or a create under a non-root UAT form."""
    return status_code(err) == 400


def is_conflict(err: Exception) -> bool:
    """A 409 — e.g. an actor ref already taken on that form."""
    return status_code(err) == 409


def is_duplicate_ref(err: Exception) -> bool:
    """Reports the refusal a transaction gets when its ref was posted before:
    the platform answers 400 "Not unique ref" rather than deduplicating
    silently, so a retried post is refused, not doubled — which is exactly the
    idempotency a caller relies on, and no reason to warn. The phrase is
    looked for wherever parse_error may have put it: the envelope is not
    uniform, and a string-shaped {"error": …} lands in code, not message.
    """
    if not isinstance(err, SimulatorError) or err.status_code != 400:
        return False
    phrase = "Not unique ref"
    return phrase in err.message or phrase in err.code or phrase in err.body
