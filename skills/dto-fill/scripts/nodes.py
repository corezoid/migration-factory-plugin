#!/usr/bin/env python3
"""Index the nodes of a graph.values.yaml export.

Prints one line per node: the shortest unique suffix address (what goes in
`at:`), the type slug, and how many field values the node already holds.

    nodes.py <graph.values.yaml> [--type SLUG] [--empty] [--filled]
             [--under TEXT] [--fields] [--full]

    --type    only nodes of that type slug
    --empty   only nodes with no stored values (the holes to fill first)
    --filled  only nodes that already hold values
    --under   only nodes whose full path contains TEXT (case-insensitive)
    --fields  also print the stored field values, indented
    --full    print the full path instead of the shortest unique suffix
"""

import re
import sys

NODE = re.compile(r"^(?P<indent>\s*)(?P<label>.+?) \[(?P<type>[A-Za-z0-9_]+)\](?:\s+# (?P<desc>.*))?$")
FIELD = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z0-9_.\-]+): (?P<value>.*)$")
SEP = " > "


def parse(path):
    """Return [{path: [segments], type, desc, fields: [(name, value)]}]."""
    lines = open(path, encoding="utf-8").read().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.rstrip() == "tree: |") + 1
    except StopIteration:
        sys.exit(f"{path}: no `tree: |` block")

    nodes, stack = [], []  # stack: [(indent, node)]
    for line in lines[start:]:
        if not line.strip():
            continue
        if not line.startswith("  "):  # the block ended
            break
        # FIELD is tried first: a node line and a field line are told apart
        # only by shape, and a field VALUE ending in "[something]" — which the
        # prose in gaps/evidence_quote can easily do — otherwise parses as a
        # node and takes the following fields onto its stack frame. A field
        # name cannot contain spaces, so the only thing this mis-reads is a
        # node whose title starts with "Identifier: ", which titles do not.
        f = FIELD.match(line)
        if f and stack:
            stack[-1][1]["fields"].append((f.group("name"), f.group("value")))
            continue

        m = NODE.match(line)
        if m:
            indent = len(m.group("indent"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            node = {
                "path": [n["label"] for _, n in stack] + [m.group("label")],
                "label": m.group("label"),
                "type": m.group("type"),
                "desc": m.group("desc") or "",
                "fields": [],
            }
            stack.append((indent, node))
            nodes.append(node)
    return nodes


def addresses(nodes):
    """Shortest unique path suffix per node — the address `at:` resolves by."""
    counts = {}
    for n in nodes:
        for k in range(1, len(n["path"]) + 1):
            counts[SEP.join(n["path"][-k:])] = counts.get(SEP.join(n["path"][-k:]), 0) + 1
    out = []
    for n in nodes:
        addr = SEP.join(n["path"])
        for k in range(1, len(n["path"]) + 1):
            cand = SEP.join(n["path"][-k:])
            if counts[cand] == 1:
                addr = cand
                break
        out.append(addr)
    return out


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    src, opts = args[0], args[1:]

    def opt(name):
        """The value after `name`, or None — a trailing flag is not a value."""
        if name not in opts:
            return None
        i = opts.index(name) + 1
        if i >= len(opts) or opts[i].startswith("--"):
            sys.exit(f"{name} needs a value")
        return opts[i]

    want_type, under = opt("--type"), opt("--under")
    nodes = parse(src)
    addrs = addresses(nodes)

    shown = 0
    for node, addr in zip(nodes, addrs):
        if want_type and node["type"] != want_type:
            continue
        if "--empty" in opts and node["fields"]:
            continue
        if "--filled" in opts and not node["fields"]:
            continue
        if under and under.lower() not in SEP.join(node["path"]).lower():
            continue
        shown += 1
        out = SEP.join(node["path"]) if "--full" in opts else addr
        print(f'{out}\t[{node["type"]}]\t{len(node["fields"])} values')
        if "--fields" in opts:
            for name, value in node["fields"]:
                print(f"    {name}: {value}")
    print(f"# {shown} of {len(nodes)} nodes", file=sys.stderr)


if __name__ == "__main__":
    main()
