import io
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import ipaddress  # noqa: E402

import pytest  # noqa: E402

from migration_factory_plugin_mcp.graph import pictures  # noqa: E402


# --------------------------------------------------------------- SSRF guard

@pytest.mark.parametrize("addr", [
    "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.1.1",
    "0.0.0.1", "100.64.0.1", "192.0.0.5", "198.18.0.1", "240.0.0.1",
    "224.0.0.1", "::1", "fc00::1", "fe80::1",
    "64:ff9b::1", "2002::1", "2001::1",
])
def test_blocked_addresses(addr):
    ip = ipaddress.ip_address(addr)
    assert pictures._is_blocked_address(ip) is True


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses_not_blocked(addr):
    ip = ipaddress.ip_address(addr)
    assert pictures._is_blocked_address(ip) is False


def test_validate_picture_url():
    pictures.validate_picture_url("https://example.com/logo.png")
    with pytest.raises(ValueError):
        pictures.validate_picture_url("")
    with pytest.raises(ValueError):
        pictures.validate_picture_url("ftp://example.com/logo.png")
    with pytest.raises(ValueError):
        pictures.validate_picture_url("https:///no-host.png")


# ------------------------------------------------------------- image sniffing

_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8ffff3f0005fe02fea739666d0000000049454e44ae426082"
)


def _fake_png(width, height):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", width) + struct.pack(">I", height) + b"\x08\x02\x00\x00\x00"
    # Not a fully valid PNG (no real chunk length/CRC framing before IHDR data),
    # but _image_size only reads bytes[16:24], which this lays out correctly.
    return sig + b"\x00\x00\x00\x0dIHDR" + ihdr


def _fake_gif(width, height):
    return b"GIF89a" + struct.pack("<H", width) + struct.pack("<H", height) + b"\x00" * 3


def test_sniff_content_type():
    assert pictures._sniff_content_type(_PNG_1X1) == "image/png"
    assert pictures._sniff_content_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert pictures._sniff_content_type(b"GIF89a...") == "image/gif"
    assert pictures._sniff_content_type(b"RIFF????WEBPVP8 ") == "image/webp"
    assert pictures._sniff_content_type(b"not an image") == ""


def test_image_size_png_and_gif():
    assert pictures._image_size(_fake_png(64, 48)) == (64, 48)
    assert pictures._image_size(_fake_gif(100, 50)) == (100, 50)
    assert pictures._image_size(b"not an image") is None


def test_picture_name_from_content_type_and_url():
    name, ct = pictures._picture_name("https://example.com/assets/logo.png?x=1", _PNG_1X1, "image/png")
    assert name == "logo.png"
    assert ct == "image/png"


def test_picture_name_sniffs_when_content_type_unusable():
    name, ct = pictures._picture_name("https://example.com/assets/logo", _PNG_1X1, "application/octet-stream")
    assert name == "logo.png"
    assert ct == "image/png"


def test_picture_name_rejects_unknown_format():
    with pytest.raises(ValueError, match="PNG, JPEG, GIF or WebP"):
        pictures._picture_name("https://example.com/file.bin", b"not an image", "application/octet-stream")


def test_picture_name_falls_back_when_url_path_empty():
    name, _ = pictures._picture_name("https://example.com/", _PNG_1X1, "image/png")
    assert name == "picture.png"


def test_check_picture_size_rejects_too_small():
    with pytest.raises(ValueError, match="too small"):
        pictures._check_picture_size(_fake_png(10, 10), "tiny.png")


def test_check_picture_size_rejects_extreme_ratio():
    with pytest.raises(ValueError, match="too thin a strip"):
        pictures._check_picture_size(_fake_png(2000, 40), "strip.png")


def test_check_picture_size_accepts_reasonable_logo():
    pictures._check_picture_size(_fake_png(200, 60), "logo.png")


def test_check_picture_size_undecodable_falls_back_to_byte_floor():
    small_webp = b"RIFF" + b"?" * 4 + b"WEBP" + b"x" * 10
    with pytest.raises(ValueError, match="too small"):
        pictures._check_picture_size(small_webp, "x.webp")
    big_enough = b"RIFF" + b"?" * 4 + b"WEBP" + b"x" * (pictures.MIN_PICTURE_BYTES)
    pictures._check_picture_size(big_enough, "x.webp")  # does not raise


# ------------------------------------------------------------------ the store

class _FakeUpload:
    def __init__(self, file_name):
        self.file_name = file_name


class _FakeForm:
    def __init__(self, acc_id):
        self.acc_id = acc_id


class _FakeSim:
    def __init__(self):
        self.upload_calls = []

    def upload_file(self, workspace, name, content_type, data):
        self.upload_calls.append((workspace, name, content_type, data))
        return _FakeUpload(f"stored/{name}-{len(self.upload_calls)}")

    def get_form(self, form_id, filter=""):
        return _FakeForm(acc_id=f"ws-for-form-{form_id}")


_PNG_200X60 = _fake_png(200, 60)


def test_store_dedups_by_url_and_by_content_hash(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(url):
        calls["n"] += 1
        return _PNG_200X60, "image/png"

    monkeypatch.setattr(pictures, "_fetch", fake_fetch)
    sim = _FakeSim()
    store = pictures.PictureStore(sim=sim, workspace_id="ws-1")

    p1 = store.path_for(1, "ACME > A", "https://example.com/logo.png")
    p2 = store.path_for(1, "ACME > A", "https://example.com/logo.png")  # same URL, same node
    assert p1 == p2
    assert calls["n"] == 1  # fetched once
    assert len(sim.upload_calls) == 1

    # A different URL serving the exact same bytes, bound to the SAME node,
    # uploads once (by hash) even though it's fetched again. (Binding the
    # same resulting path to a DIFFERENT node is covered separately below —
    # that's the one-image-one-node rule, not the hash-dedup rule.)
    p3 = store.path_for(1, "ACME > A", "https://example.com/logo-mirror.png")
    assert calls["n"] == 2
    assert len(sim.upload_calls) == 1
    assert p3 == p1


def test_store_rejects_binding_same_picture_to_two_nodes(monkeypatch):
    monkeypatch.setattr(pictures, "_fetch", lambda url: (_PNG_200X60, "image/png"))
    sim = _FakeSim()
    store = pictures.PictureStore(sim=sim, workspace_id="ws-1")
    store.path_for(1, "ACME > A", "https://example.com/logo.png")
    with pytest.raises(ValueError, match="already bound"):
        store.path_for(1, "ACME > B", "https://example.com/logo.png")


def test_store_enforces_max_pictures_per_run(monkeypatch):
    counter = {"n": 0}

    def fake_fetch(url):
        counter["n"] += 1
        # distinct bytes per url so each one is a genuinely new upload
        return _PNG_200X60 + bytes([counter["n"] % 256]), "image/png"

    monkeypatch.setattr(pictures, "_fetch", fake_fetch)
    sim = _FakeSim()
    store = pictures.PictureStore(sim=sim, workspace_id="ws-1")
    store.uploads = pictures.MAX_PICTURES_PER_RUN
    with pytest.raises(ValueError, match="rest are refused"):
        store.path_for(1, "ACME > A", "https://example.com/new-logo.png")


def test_store_resolves_workspace_from_form_when_not_configured(monkeypatch):
    monkeypatch.setattr(pictures, "_fetch", lambda url: (_PNG_200X60, "image/png"))
    sim = _FakeSim()
    store = pictures.PictureStore(sim=sim, workspace_id="")
    store.path_for(7, "ACME > A", "https://example.com/logo.png")
    assert sim.upload_calls[0][0] == "ws-for-form-7"


def test_store_fetch_failure_is_cached_not_retried(monkeypatch):
    calls = {"n": 0}

    def failing_fetch(url):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(pictures, "_fetch", failing_fetch)
    sim = _FakeSim()
    store = pictures.PictureStore(sim=sim, workspace_id="ws-1")
    with pytest.raises(RuntimeError, match="boom"):
        store.path_for(1, "ACME > A", "https://example.com/broken.png")
    with pytest.raises(RuntimeError, match="boom"):
        store.path_for(1, "ACME > A", "https://example.com/broken.png")
    assert calls["n"] == 1  # not retried within the same run
