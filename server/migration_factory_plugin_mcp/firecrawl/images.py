"""Client-side HTML <img> tag scanner — only invoked when the caller requested images.

The `images` format answers with addresses and nothing else, and an address on
its own identifies nobody: what ties a photograph to a person, a product or an
office is the text beside it. So a read that asks for pictures asks for the
page's cleaned HTML too, and this is where the two are joined — the alt text
and title of every <img>, against the address it sits on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Image:
    url: str = ""
    alt: str = ""


def parse_image(raw: Any) -> Image:
    """Parse one image from either shape the `images` format is served in.

    A bare address (a list of strings is still useful — the addresses are the
    point) or an object carrying the address under one of the names an
    extractor might use.
    """
    if isinstance(raw, str):
        return Image(url=raw.strip())
    if isinstance(raw, dict):
        return Image(
            url=_first_string(raw, "url", "src", "imageUrl", "href"),
            alt=_first_string(raw, "alt", "altText", "caption", "title"),
        )
    raise ValueError("image is neither an address nor an object")


def _first_string(obj: dict[str, Any], *keys: str) -> str:
    for key in keys:
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def image_alts(html: str) -> dict[str, str]:
    """Read the alt text and title of every <img> in the page, keyed by the address it is on."""
    out: dict[str, str] = {}
    for tag in _img_tags(html):
        attrs = _attributes(tag)
        src = _first_attr(attrs, "src", "data-src", "data-original")
        if not src:
            continue
        # The first mention wins: a later, emptier alt on the same address
        # must not erase what the first one carried.
        if src in out:
            continue
        out[src] = _first_attr(attrs, "alt", "title", "aria-label")
    return out


def _img_tags(html: str) -> list[str]:
    """Return the body of every <img …> tag, quotes respected so a '>' inside an attribute doesn't end it early."""
    tags: list[str] = []
    rest = html
    while True:
        i = _index_tag(rest, "<img")
        if i < 0:
            return tags
        rest = rest[i + len("<img"):]

        quote = ""
        end = -1
        for j, c in enumerate(rest):
            if quote:
                if c == quote:
                    quote = ""
            elif c in ("\"", "'"):
                quote = c
            elif c == ">":
                end = j
            if end >= 0:
                break
        if end < 0:
            tags.append(rest)
            return tags
        tags.append(rest[:end])
        rest = rest[end + 1:]


def _index_tag(html: str, tag: str) -> int:
    """Find a tag opener that is really one: "<img" and not "<images"."""
    lowered = html.lower()
    tag_lower = tag.lower()
    frm = 0
    while True:
        i = lowered.find(tag_lower, frm)
        if i < 0:
            return -1
        nxt = i + len(tag)
        if nxt >= len(html) or _is_tag_break(html[nxt]):
            return i
        frm = nxt


def _is_tag_break(c: str) -> bool:
    return c in (" ", "\t", "\n", "\r", "/", ">")


def _attributes(tag: str) -> dict[str, str]:
    """Read a tag's attributes into a map, lowercasing the names."""
    out: dict[str, str] = {}
    i = 0
    n = len(tag)
    while i < n:
        while i < n and tag[i] in (" ", "\t", "\n", "\r"):
            i += 1
        start = i
        while i < n and tag[i] not in ("=", " ", "\t", "\n", "\r"):
            i += 1
        name = tag[start:i].lower()
        if name == "":
            i += 1
            continue
        while i < n and tag[i] in (" ", "\t"):
            i += 1
        if i >= n or tag[i] != "=":
            out[name] = ""  # a bare attribute, e.g. `loading`
            continue
        i += 1  # past '='
        while i < n and tag[i] in (" ", "\t"):
            i += 1
        if i >= n:
            break
        if tag[i] in ("\"", "'"):
            q = tag[i]
            i += 1
            start = i
            while i < n and tag[i] != q:
                i += 1
            value = tag[start:i]
            i += 1  # past the closing quote
        else:
            start = i
            while i < n and tag[i] not in (" ", "\t", "\n", "\r"):
                i += 1
            value = tag[start:i]
        out[name] = _unescape(value.strip())
    return out


def _first_attr(attrs: dict[str, str], *names: str) -> str:
    """Pick the first attribute that carries text."""
    for name in names:
        v = attrs.get(name, "").strip()
        if v:
            return v
    return ""


# Only the handful of entities an alt text realistically carries are resolved.
# Anything else is left as written: this text is quoted back to a reader as
# the page's own words, and a half-decoded entity is less confusing than a
# wrong guess at one.
_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&nbsp;": " ",
}
# A single-pass alternation, not sequential str.replace calls: replacing
# "&amp;" before "&lt;" would otherwise turn a literal "&amp;lt;" into "<"
# instead of the correct "&lt;".
_ENTITY_PATTERN = re.compile("|".join(re.escape(e) for e in _ENTITIES))


def _unescape(s: str) -> str:
    if "&" not in s:
        return s
    return _ENTITY_PATTERN.sub(lambda m: _ENTITIES[m.group(0)], s)


def with_alts(images: list[Image], html: str) -> list[Image]:
    """Fold the alt texts into the image list and append the images only the HTML knew about.

    The provider's own order is kept for the images it listed; anything the
    HTML scan found that the provider didn't declare is appended afterwards,
    in document order, deduplicated by URL.
    """
    if not html.strip():
        return images
    alts = image_alts(html)

    seen: set[str] = set()
    out: list[Image] = []
    for img in images:
        if not img.url or img.url in seen:
            continue
        seen.add(img.url)
        if not img.alt:
            img.alt = alts.get(img.url, "")
        out.append(img)
    for tag in _img_tags(html):
        src = _first_attr(_attributes(tag), "src", "data-src", "data-original")
        if not src or src in seen:
            continue
        seen.add(src)
        out.append(Image(url=src, alt=alts.get(src, "")))
    return out
