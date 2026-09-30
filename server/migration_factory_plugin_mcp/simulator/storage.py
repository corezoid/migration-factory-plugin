"""The one storage route needed: putting bytes into a workspace's storage so
an actor can carry them.

It exists for pictures. An actor's `picture` is a path in that storage and
not an address on the web, which is the whole reason a picture found on a
site has to be copied here first: the twin keeps its own copy, and it
outlives the redesign that moves the original.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .types import decode_item

if TYPE_CHECKING:
    from .client import _ClientCore

# The only status the scanner clears a file with. Any other value means the
# file was rejected and must not be handed on.
UPLOAD_STATUS_CLEAN = "clean"


@dataclass
class Upload:
    """A stored file. file_name is its storage path — the string an actor's
    `picture` carries, and the one thing a caller normally wants back."""

    id: int
    acc_id: str = ""
    title: str = ""
    type: str = ""
    size: int = 0
    file_name: str = ""
    status: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "Upload":
        return cls(
            id=int(d.get("id", 0) or 0),
            acc_id=d.get("accId", "") or "",
            title=d.get("title", "") or "",
            type=d.get("type", "") or "",
            size=int(d.get("size", 0) or 0),
            file_name=d.get("fileName", "") or "",
            status=d.get("status", "") or "",
        )

    def clean(self) -> bool:
        """Whether the scanner cleared the file for use."""
        return self.status in ("", UPLOAD_STATUS_CLEAN)


def _quote_filename(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _build_multipart(name: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{_quote_filename(name)}"\r\n'
    ).encode()
    if content_type:
        body += f"Content-Type: {content_type}\r\n".encode()
    body += b"\r\n"
    body += data
    body += f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), boundary


class StorageMixin:
    def upload_file(self: "_ClientCore", acc_id: str, name: str, content_type: str, data: bytes) -> Upload:
        """Stores bytes in a workspace's storage and returns the record.

        It is multipart rather than a base64 route: only multipart preserves
        the file's real type and the extension of its name, and the graph UI
        renders a picture by both. content_type is what the store records —
        pass the real one, because "application/octet-stream" comes back as
        a download link instead of an image.

        ttl=0 mirrors what the UI sends. Without it the gateway sometimes
        stores the file with a lifetime, and a picture that expires is worse
        than one that was never set.
        """
        if not acc_id:
            raise ValueError("simulator: UploadFile needs the workspace to store the file in")
        if not name:
            raise ValueError("simulator: UploadFile needs a file name")
        if not data:
            raise ValueError("simulator: UploadFile got no bytes")

        raw_body, boundary = _build_multipart(name, content_type, data)
        payload = self._call(
            "POST",
            f"/upload/{self._seg(acc_id)}",
            query={"ttl": "0"},
            raw_body=raw_body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        upload = Upload.from_json(decode_item(payload))
        if not upload.clean():
            raise RuntimeError(
                f"simulator: {name} was stored as {upload.status!r}, not {UPLOAD_STATUS_CLEAN!r} — "
                "the scanner rejected it"
            )
        if upload.file_name == "":
            raise RuntimeError(f"simulator: {name} was uploaded but the store returned no path to it")
        return upload
