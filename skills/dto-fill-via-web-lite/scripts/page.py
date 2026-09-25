#!/usr/bin/env python3
"""Read a saved HTML page: its text, its structured data, or its links.

    page.py <file.html> [--links] [--base URL] [--max N]

    (default)  title, meta description, every JSON-LD block, then the text
    --links    one line per link: anchor text, then the address
    --base     resolve relative addresses against this URL (the page's own)
    --max      cut the text at N characters (default: print all of it)

`read_page` is the better reader for a page that can be fetched — it returns
the same Markdown the pipeline renders a website source into. This is for the
copy already on disk: the one the user saved, the one behind a login, the one
whose JavaScript already ran. Raw HTML is unreadable at this size and mostly
markup, so it is never `cat`ed into a run.

The JSON-LD blocks are printed first and whole. On a company site that is
where the legal name, the address, the phone and the social profiles are
actually machine-stated, and it is the difference between reading a fact and
inferring one from a hero banner.

Links are the map for what to read next: the want list decides which of them
is worth a `read_page`, so `mailto:` and `tel:` are kept — on a contacts page
they are the fact, not the navigation.
"""

import html.parser
import re
import sys
import urllib.parse

# Everything inside these is markup or code, never the page's text. <script>
# is the exception that matters: type="application/ld+json" carries the
# structured facts, so its body is collected rather than dropped.
#
# <noscript> is deliberately NOT here, and it is the reason this script earns
# its place on a modern site: a page whose content is rendered by JavaScript
# often ships its only server-side copy inside <noscript>, and skipping it —
# the obvious thing to do — reads such a page as empty.
SKIP = {"script", "style", "svg", "template", "iframe"}

# Tags that end a line of text. Without them a page collapses into one
# paragraph and a label glues onto the value of the field below it.
BLOCK = {
    "p", "div", "br", "hr", "li", "tr", "section", "article", "header",
    "footer", "nav", "main", "aside", "form", "table", "ul", "ol", "dl",
    "dt", "dd", "blockquote", "pre", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.jsonld = []
        self.links = []          # [(text, href)]
        self.text = []
        self._skip = []          # stack of open skipped tags
        self._in_title = False
        self._in_jsonld = False
        self._link = None        # (href, [text parts])
        self.base = ""

    # -------------------------------------------------------------- markup
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and a.get("href"):
            self.base = a["href"]
        if tag == "script":
            if (a.get("type") or "").strip().lower() == "application/ld+json":
                self._in_jsonld = True
                self.jsonld.append([])
            else:
                self._skip.append(tag)
            return
        if tag in SKIP:
            self._skip.append(tag)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = (a.get("content") or "").strip()
        elif tag == "a":
            href = (a.get("href") or "").strip()
            if href:
                self._link = (href, [])
        elif tag == "img":
            # Alt text is text: a logo's alt is often the only place the legal
            # name is spelled in full on a front page.
            if alt := (a.get("alt") or "").strip():
                self.text.append(alt)
        if tag in BLOCK:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            return
        if self._skip and self._skip[-1] == tag:
            self._skip.pop()
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._link:
            href, parts = self._link
            self.links.append((" ".join("".join(parts).split()), href))
            self._link = None
        if tag in BLOCK:
            self.text.append("\n")

    def handle_data(self, data):
        if self._in_jsonld:
            self.jsonld[-1].append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self.text.append(data)
        if self._link:
            self._link[1].append(data)

    # --------------------------------------------------------------- output
    def body(self):
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.text))
        text = re.sub(r" *\n *", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def structured(self):
        return [b for b in ("".join(parts).strip() for parts in self.jsonld) if b]

    def addresses(self, base):
        """Links, absolutised and deduplicated, in the order they appear."""
        base = base or self.base
        seen, out = set(), []
        for text, href in self.links:
            if href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            url = urllib.parse.urljoin(base, href) if base else href
            if url in seen:
                continue
            seen.add(url)
            out.append((text, url))
        return out


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    src, opts = args[0], args[1:]

    def opt(name):
        if name not in opts:
            return None
        i = opts.index(name) + 1
        if i >= len(opts) or opts[i].startswith("--"):
            sys.exit(f"{name} needs a value")
        return opts[i]

    base, limit = opt("--base"), opt("--max")
    page = Page()
    page.feed(open(src, encoding="utf-8", errors="replace").read())
    page.close()

    links = page.addresses(base)
    if "--links" in opts:
        for text, url in links:
            print(f"{text or '—'}\t{url}")
        print(f"# {len(links)} links in {src}", file=sys.stderr)
        return

    if page.title.strip():
        print(f"# title: {' '.join(page.title.split())}")
    if page.description:
        print(f"# description: {page.description}")
    for block in page.structured():
        print(f"\n# json-ld\n{block}")

    text = page.body()
    cut = int(limit) if limit else 0
    print()
    if cut and len(text) > cut:
        print(f"{text[:cut]}\n… ({len(text) - cut} more characters — raise --max or read the page with read_page)")
    else:
        print(text)
    print(f"# {src}: {len(text)} characters of text, {len(links)} links, "
          f"{len(page.structured())} json-ld block(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
