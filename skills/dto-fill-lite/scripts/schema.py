#!/usr/bin/env python3
"""Read one type out of types.schema.yaml without loading the whole file.

    schema.py <types.schema.yaml> --list            # every slug + its description
    schema.py <types.schema.yaml> document employee_ticket   # those types' fields

Field lines print as `name  type  title` — the title is the instruction the
form author wrote for the extractor, including the allowed values of an
enum-by-convention string field, so read it before choosing a value.
"""

import re
import sys

SLUG = re.compile(r"^  (?P<slug>[A-Za-z0-9_]+):\s*$")
KEY = re.compile(r"^    (?P<key>formId|form|description): (?P<value>.*)$")
FIELD = re.compile(r"^      (?P<name>[A-Za-z0-9_.\-]+): \{(?P<body>.*)\}\s*$")
ATTR = re.compile(r"(\w+): ('(?:[^']|'')*'|\"[^\"]*\"|[^,}]*)")


def unquote(s):
    s = s.strip()
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def parse(path):
    types, cur = {}, None
    for line in open(path, encoding="utf-8").read().splitlines():
        m = SLUG.match(line)
        if m:
            cur = {"slug": m.group("slug"), "description": "", "form": "", "fields": []}
            types[cur["slug"]] = cur
            continue
        if cur is None:
            continue
        k = KEY.match(line)
        if k:
            cur[k.group("key")] = unquote(k.group("value"))
            continue
        f = FIELD.match(line)
        if f:
            attrs = {a: unquote(v) for a, v in ATTR.findall(f.group("body"))}
            cur["fields"].append((f.group("name"), attrs.get("type", "?"),
                                  attrs.get("title", ""), attrs.get("writable", "true")))
    return types


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    types = parse(args[0])
    wanted = args[1:]

    if not wanted or wanted[0] == "--list":
        for slug, t in types.items():
            print(f'{slug}\t{t["form"]}\t{t["description"][:140]}')
        return

    for slug in wanted:
        t = types.get(slug)
        if not t:
            print(f"{slug}: not in the schema", file=sys.stderr)
            continue
        print(f'{slug}  ({t["form"]})')
        if t["description"]:
            print(f'  {t["description"]}')
        for name, ftype, title, writable in t["fields"]:
            ro = "" if writable == "true" else "  [read-only]"
            print(f"  {name}\t{ftype}{ro}\t{title}")
        print()


if __name__ == "__main__":
    main()
