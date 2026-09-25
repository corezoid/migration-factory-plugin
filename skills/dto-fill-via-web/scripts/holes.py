#!/usr/bin/env python3
"""List what every node on the layer is still missing — the want list.

    holes.py <graph.values.yaml> <types.schema.yaml>
             [--type SLUG] [--under TEXT] [--empty] [--all] [--addr] [--titles]

This is the join `nodes.py` and `schema.py` cannot do between them: the tree
says what a node holds, the schema says what its type could hold, and the
question this skill runs on is the difference. Asking it node by node across
fifty nodes and twenty types is the part a run gets wrong by forgetting one,
and forgetting one is invisible — the report then reads as though the site had
nothing to say about a field nobody looked for.

Prints one numbered block per node with an unfilled field, field names only:

    # want list — 154 node(s) on the layer, 70 with holes, 812 field(s) missing
    # blocks are numbered [n/70]; --titles adds what goes in each field
    [2/70] COMPANY > Acme SRL	[company]	2/14 filled, no picture
        legal_name, registration_id, legal_form, address, city, phone, email
    …
    # end of want list — 70 of 70 block(s) printed

"no picture" is a hole like any other — any actor can carry an image, and a
site that shows the subject is where it comes from. It reads the export's
`pictures:` block, so an export written before that block says nothing about
pictures rather than guessing.

Names only is the default because the whole layer has to survive **one** read:
a want list long enough to be cut has a silent end, and the half nobody saw
reads afterwards exactly like a half the site had nothing for. The numbering
and the closing line are there to be checked — a listing that does not end on
`# end of want list` was truncated, and the rest of the layer was never asked
about. Narrow it with `--under` per branch and read the parts.

    --type    only nodes of that type slug
    --under   only nodes whose full path contains TEXT (case-insensitive)
    --empty   only nodes holding no values at all
    --no-picture  only nodes that do not carry an image yet
    --all     also count provenance fields and read-only ones as missing
    --addr    print the shortest unique suffix (what goes in `at:`) instead of
              the full path
    --titles  print the title under each field name — what goes in it. Costs
              roughly six times the output, so it belongs on a narrowed
              listing, which is where a borderline field gets decided anyway.

Read-only fields are left out by default: the form computes them and no ops
file can write one, so listing them as holes invents work. So are the four
provenance fields every filled record carries — they are the run's own
bookkeeping, not something a source is asked for.

Titles are cut at 140 characters; the full title, with the allowed values of
an enum-by-convention field, is in `schema.py <types.schema.yaml> <slug>`.
"""

import pathlib
import sys
import textwrap

# The tree and schema parsers belong to the dto-fill skill next door, and this
# plugin ships both skills together — one parser per file format is the point.
# Importing them beats a second copy that drifts the first time an export
# changes shape.
SIBLING = pathlib.Path(__file__).resolve().parent.parent.parent / "dto-fill" / "scripts"
sys.path.insert(0, str(SIBLING))
try:
    from nodes import addresses, parse as parse_tree
    from schema import parse as parse_types
except ImportError as exc:  # pragma: no cover - a broken install, not a run
    sys.exit(f"{exc}: expected the dto-fill skill's scripts in {SIBLING}")

# What a run writes about itself rather than about the subject. A node missing
# only these is a filled node, and counting them as holes would send the agent
# back to the site for a confidence score.
PROVENANCE = {"source", "evidence_quote", "confidence", "gaps"}

SEP = " > "
TITLE_CUT = 140
NAMES_WRAP = 92


def pictured(path):
    """Full paths of the nodes that already carry an image.

    None when the export has no `pictures:` block — an older export, which is
    not the same as a layer where no node has one, so the listing then leaves
    pictures out entirely.
    """
    lines = open(path, encoding="utf-8").read().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.rstrip() == "pictures: |") + 1
    except StopIteration:
        return None

    out = set()
    for line in lines[start:]:
        if not line.strip():
            continue
        if not line.startswith("  "):  # the block ended
            break
        text = line.strip()
        if text.startswith("#"):
            continue
        out.add(text)
    return out


def missing(node, types, keep_all):
    """The fields of the node's type that the node does not hold."""
    t = types.get(node["type"])
    if t is None:
        return None  # a type the export did not describe — say so, do not guess
    held = {name for name, _ in node["fields"]}
    out = []
    for name, _ftype, title, writable in t["fields"]:
        if name in held:
            continue
        if not keep_all and (name in PROVENANCE or writable != "true"):
            continue
        out.append((name, title))
    return out


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    values_path, schema_path, opts = args[0], args[1], args[2:]

    def opt(name):
        if name not in opts:
            return None
        i = opts.index(name) + 1
        if i >= len(opts) or opts[i].startswith("--"):
            sys.exit(f"{name} needs a value")
        return opts[i]

    want_type, under = opt("--type"), opt("--under")
    keep_all, short = "--all" in opts, "--addr" in opts
    with_titles = "--titles" in opts

    nodes = parse_tree(values_path)
    types = parse_types(schema_path)
    addrs = addresses(nodes)
    faces = pictured(values_path)

    # Collected before anything is printed: the header carries how many blocks
    # follow, and a reader who counts them knows whether the listing arrived
    # whole. That number cannot be written after the fact.
    blocks, untyped, wanted, faceless = [], [], 0, 0
    for node, addr in zip(nodes, addrs):
        full = SEP.join(node["path"])
        if want_type and node["type"] != want_type:
            continue
        if under and under.lower() not in full.lower():
            continue
        if "--empty" in opts and node["fields"]:
            continue

        # A node the export says carries no image is a want in its own right,
        # so it is listed even when every field of its type is filled.
        no_picture = faces is not None and full not in faces
        if "--no-picture" in opts and not no_picture:
            continue

        holes = missing(node, types, keep_all)
        path = addr if short else full
        if holes is None:
            untyped.append(f'{path} [{node["type"]}]')
            continue
        if not holes and not no_picture:
            continue

        wanted += len(holes)
        faceless += 1 if no_picture else 0
        blocks.append((path, node["type"], len(node["fields"]), holes, no_picture))

    total = len(blocks)
    narrowed = [f"{k} {v}" for k, v in (("--type", want_type), ("--under", under)) if v]
    for flag in ("--empty", "--no-picture"):
        if flag in opts:
            narrowed.append(flag)
    scope = f" ({', '.join(narrowed)})" if narrowed else ""
    faces_note = f", {faceless} without a picture" if faces is not None else ""
    print(f"# want list{scope} — {len(nodes)} node(s) on the layer, "
          f"{total} with holes, {wanted} field(s) missing{faces_note}")
    print(f"# blocks are numbered [n/{total}]"
          + ("" if with_titles else "; --titles adds what goes in each field"))

    for i, (path, slug, held, holes, no_picture) in enumerate(blocks, 1):
        face = ", no picture" if no_picture else ""
        print(f"[{i}/{total}] {path}\t[{slug}]\t{held}/{held + len(holes)} filled{face}")
        if not holes:
            continue
        if with_titles:
            for name, title in holes:
                cut = title if len(title) <= TITLE_CUT else title[:TITLE_CUT] + "…"
                print(f"    {name:<24}{cut}")
            continue
        for line in textwrap.wrap(", ".join(name for name, _ in holes),
                                  width=NAMES_WRAP, initial_indent="    ",
                                  subsequent_indent="    ", break_long_words=False):
            print(line)

    if untyped:
        print(f"# {len(untyped)} node(s) of a type the schema does not describe — "
              "no writable fields, so not holes:")
        for line in untyped:
            print(f"#   {line}")
    print(f"# end of want list — {total} of {total} block(s) printed")


if __name__ == "__main__":
    main()
