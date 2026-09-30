"""A node's picture is COPIED, never linked — Simulator stores it as a path
in the workspace's own storage, so a web address is fetched here and
re-uploaded. Dedup is per-apply-run: the same URL is fetched once, the same
bytes served from two different URLs upload once, and the same resulting
storage path can never be claimed by two different nodes in one run (a
photograph that fits two subjects has identified neither).

The fetch goes through an SSRF-guarded connection: after DNS resolution and
before the socket opens (and again on every redirect hop), the resolved
address is checked against loopback/private/link-local/multicast/reserved
ranges. This is a real trust boundary — an op's `picture:` is a URL taken
from a source document, not from an operator — so it is not incidental
hardening.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
import socket
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

MAX_PICTURE_BYTES = 20 << 20  # 20 MiB
MIN_PICTURE_BYTES = 3 << 10  # 3 KiB — floor used only when dimensions can't be decoded (webp)
MIN_PICTURE_SIDE = 32  # px — rejects spacers/tracking-pixels, low enough to accept a small favicon
MAX_PICTURE_RATIO = 12  # rejects rules/gradients/separator strips, loose enough for a wordmark logo
MAX_PICTURES_PER_RUN = 50
PICTURE_FETCH_TIMEOUT = 30  # seconds

# SVG deliberately excluded — the canvas serves it as octet-stream and draws
# nothing.
_PICTURE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def validate_picture_url(url: str) -> None:
    if not url or not url.strip():
        raise ValueError("no picture url")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"picture url {url!r} must be http or https")
    if not parsed.hostname:
        raise ValueError(f"picture url {url!r} has no host")


# ------------------------------------------------------------------ SSRF guard

_RESERVED_NETS = [
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / shared address space
    ipaddress.ip_network("0.0.0.0/8"),  # "this network"
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("240.0.0.0/4"),  # class E
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64
    ipaddress.ip_network("2002::/16"),  # 6to4 (embeds an IPv4 address)
    ipaddress.ip_network("2001::/32"),  # Teredo (embeds an IPv4 address)
]


def _is_blocked_address(ip: "ipaddress._BaseAddress") -> bool:
    if ip.is_loopback or ip.is_private or ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return True
    return any(ip.version == net.version and ip in net for net in _RESERVED_NETS)


class _GuardedConnectionMixin:
    """Overrides connect() to validate the resolved address AFTER DNS lookup
    and connect directly to that validated address — never re-resolving the
    hostname a second time, which would open a DNS-rebinding window between
    the check and the actual connection."""

    def connect(self):  # noqa: ANN001 - matches http.client's own signature
        infos = socket.getaddrinfo(self.host, self.port, proto=socket.IPPROTO_TCP)
        if not infos:
            raise OSError(f"could not resolve {self.host}")
        family, socktype, proto, _canonname, sockaddr = infos[0]
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_address(ip):
            raise PermissionError(
                f"picture fetch refused: {self.host} resolves to {sockaddr[0]}, which is not a public address"
            )
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(self.timeout)
        try:
            sock.connect(sockaddr)
        except OSError:
            sock.close()
            raise
        self.sock = sock
        if isinstance(self, http.client.HTTPSConnection):
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _GuardedHTTPConnection(_GuardedConnectionMixin, http.client.HTTPConnection):
    pass


class _GuardedHTTPSConnection(_GuardedConnectionMixin, http.client.HTTPSConnection):
    pass


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardedHTTPConnection, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GuardedHTTPSConnection, req, context=self._context)


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old_scheme = urlparse(req.full_url).scheme
        new_scheme = urlparse(newurl).scheme
        if old_scheme == "https" and new_scheme == "http":
            raise urllib.error.HTTPError(
                newurl, code, "refusing to follow a redirect from https to http", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _guarded_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        _GuardedHTTPHandler(), _GuardedHTTPSHandler(), _GuardedRedirectHandler()
    )


def _fetch(url: str) -> "tuple[bytes, str]":
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "migration-factory-plugin-mcp (picture for a digital twin)",
            "Accept": "image/*",
        },
    )
    try:
        with _guarded_opener().open(req, timeout=PICTURE_FETCH_TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read(MAX_PICTURE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"fetch {url}: http {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"fetch {url}: {exc.reason}") from exc
    if not data:
        raise RuntimeError(f"fetch {url}: empty response")
    if len(data) > MAX_PICTURE_BYTES:
        raise RuntimeError(f"fetch {url}: exceeds {MAX_PICTURE_BYTES} bytes")
    return data, content_type


# --------------------------------------------------------------- image sniffing

def _sniff_content_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _picture_name(url: str, data: bytes, content_type: str) -> "tuple[str, str]":
    ct = content_type.split(";")[0].strip().lower()
    ext = _PICTURE_TYPES.get(ct)
    resolved_ct = ct
    if not ext:
        sniffed = _sniff_content_type(data)
        ext = _PICTURE_TYPES.get(sniffed)
        if ext:
            resolved_ct = sniffed
    if not ext:
        raise ValueError(f"{url}: has to be a PNG, JPEG, GIF or WebP")

    base = os.path.basename(urlparse(url).path)
    base = os.path.splitext(base)[0]
    if not base or base in (".", "/"):
        base = "picture"
    return base + ext, resolved_ct


def _jpeg_size(data: bytes) -> "Optional[tuple[int, int]]":
    i = 2
    n = len(data)
    while i + 1 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker in (0x00, 0x01):
            i += 2
            continue
        if i + 4 > n:
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        is_sof = 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC)
        if is_sof:
            if i + 9 > n:
                break
            height = struct.unpack(">H", data[i + 5:i + 7])[0]
            width = struct.unpack(">H", data[i + 7:i + 9])[0]
            return width, height
        i += 2 + seg_len
    return None


def _image_size(data: bytes) -> "Optional[tuple[int, int]]":
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = struct.unpack(">I", data[16:20])[0]
        height = struct.unpack(">I", data[20:24])[0]
        return width, height
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width = struct.unpack("<H", data[6:8])[0]
        height = struct.unpack("<H", data[8:10])[0]
        return width, height
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)
    return None  # webp or unknown -> caller falls back to the byte-size floor


def _check_picture_size(data: bytes, name: str) -> None:
    size = _image_size(data)
    if size is None:
        if len(data) < MIN_PICTURE_BYTES:
            raise ValueError(f"{name}: only {len(data)} bytes — too small to be a picture of anything")
        return
    width, height = size
    if width < MIN_PICTURE_SIDE or height < MIN_PICTURE_SIDE:
        raise ValueError(f"{name}: {width}x{height} — too small to be a picture of anything")
    long_side, short_side = max(width, height), min(width, height)
    if short_side == 0 or long_side / short_side > MAX_PICTURE_RATIO:
        raise ValueError(f"{name}: {width}x{height} — too thin a strip to be a picture of a subject")


# ------------------------------------------------------------------ the store

@dataclass
class PictureStore:
    """One instance per apply run — the dedup maps only make sense scoped to
    a single run."""

    sim: object  # SimulatorClient — typed loosely to avoid a hard import cycle
    workspace_id: str = ""
    by_url: dict = field(default_factory=dict)
    by_sha: dict = field(default_factory=dict)
    bound_to: dict = field(default_factory=dict)  # storage path -> node path
    by_form: dict = field(default_factory=dict)  # form_id -> workspace accId
    uploads: int = 0
    _fetch_failures: dict = field(default_factory=dict)  # url -> Exception

    def path_for(self, form_id: int, node_path: str, url: str) -> str:
        """Resolves `url` to a storage path (fetching/uploading as needed)
        and binds it to `node_path` in this run. Raises if that storage path
        is already bound to a DIFFERENT node path in this run."""
        validate_picture_url(url)
        stored = self._stored(form_id, url)
        existing = self.bound_to.get(stored)
        if existing is not None and existing != node_path:
            raise ValueError(
                f"picture {url!r} already bound to {existing!r} in this run — one image belongs to one "
                "subject, and a photograph that fits two nodes has identified neither"
            )
        self.bound_to[stored] = node_path
        return stored

    def _stored(self, form_id: int, url: str) -> str:
        if url in self.by_url:
            return self.by_url[url]
        if url in self._fetch_failures:
            raise self._fetch_failures[url]
        try:
            stored = self._fetch_and_upload(form_id, url)
        except Exception as exc:
            self._fetch_failures[url] = exc
            raise
        self.by_url[url] = stored
        return stored

    def _fetch_and_upload(self, form_id: int, url: str) -> str:
        data, content_type = _fetch(url)
        name, resolved_ct = _picture_name(url, data, content_type)
        _check_picture_size(data, name)

        digest = hashlib.sha256(data).hexdigest()
        if digest in self.by_sha:
            return self.by_sha[digest]

        if self.uploads >= MAX_PICTURES_PER_RUN:
            raise ValueError(f"more than {MAX_PICTURES_PER_RUN} distinct pictures in one run — the rest are refused")

        workspace = self._workspace_for(form_id)
        upload = self.sim.upload_file(workspace, name, resolved_ct, data)
        self.by_sha[digest] = upload.file_name
        self.uploads += 1
        return upload.file_name

    def _workspace_for(self, form_id: int) -> str:
        if self.workspace_id:
            return self.workspace_id
        cached = self.by_form.get(form_id)
        if cached:
            return cached
        form = self.sim.get_form(form_id, "id,accId,title")
        acc_id = getattr(form, "acc_id", "") or ""
        if not acc_id:
            raise ValueError(
                f"no workspace to store a picture in for form {form_id} — "
                "set SIM_WORKSPACE_ID, or the form must report its own accId"
            )
        self.by_form[form_id] = acc_id
        return acc_id
