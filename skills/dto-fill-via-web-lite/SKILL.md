---
name: dto-fill-via-web-lite
description: The quick pass of dto-fill-via-web. Reads at most two pages of a company's website — the one it was handed, and one more chosen for the company card — and writes at most five empty nodes of a Digital Twin layer, the company node first and always, with one picture for its face. No want list, no record lookups, no creates, one round. Use when the user wants a fast or cheap first fill of a layer from a site, a company card off the front page, or says lite, quick, draft, preview, "хотя бы компанию с сайта", "по-быстрому", "черновой прогон". Triggers on "быстро заполни граф с сайта", "лайт прогон по сайту", "вытащи компанию с сайта", "черновик DTO по сайту", "quick fill the twin from its site", "lite fill from the web", "just get the company off the front page", "/dto-fill-via-web-lite".
---

# dto-fill-via-web-lite — two pages, five nodes, company first

`dto-fill-via-web` builds a want list of every hole on the layer and reads the
site until the list is answered. This reads two pages and fills at most five
nodes. It runs on a small model at low effort, so everything is fixed in
advance: how many pages are read, how many nodes are written, which node is
written first, and how many rounds there are (one).

What it gives up is coverage, and it gives it up on purpose. Skip nothing
listed below and add nothing that is not — the passes the full skill makes are
what this one trades for speed, and reaching for one of them by hand is how a
lite run becomes a slow run that is still only half a `dto-fill-via-web`.

**Two hard limits, and they are the point of the skill**: at most two pages
read, at most five nodes written. Both are counted, and both are reported.

## The call

    /dto-fill-via-web-lite <url> [<file>] layer_id <uuid> [source_scope page] [gateway mw|sim]

- `<url>` — the site, normally the twin's own front page, and the boundary:
  pages of this site, nothing beyond it.
- `<file>` — that address already read, sitting in the working directory.
  Markdown (`.md`) is the provider's own rendering — read it directly, fetching
  the address again returns the same text. Raw HTML is read with `page.py`
  below, never with `cat`. Absent, `read_page(url)` fetches the address and
  that counts as page one.
- `source_scope page` — the caller pointed at this one address: read it and
  fetch nothing further. The second page is then not spent.
- `layer_id` — the layer to fill. Required; ask when it is not given.
- `gateway` — `mw` or `sim`, which Simulator the layer lives on. The MCP
  server is already pointed at it; the label only tells you what a link you
  write down means.

## 1 — export, and read the tree

    export_graph(layer: "<layer-uuid>", dir: "<dir>")

`dir` defaults to the server's working directory; leave it there unless the
user names one. Read `graph.values.yaml` whole — an empty layer's tree is
small, one call, and it is both the list of addresses you may write to and
what tells you whose site you are holding. `layer:` at its top goes into the
ops file.

Do not run `holes.py`, do not read `types.schema.yaml` (~100 KB), do not open
`graph.ids.json`. The want list is the machinery this run trades away: on an
empty layer it is seventy blocks long, and five of its nodes is all this run
can spend anyway.

## 2 — whose site is this

If the `[company]` node already holds a `legal_name` or `registration_id`, the
site must be that company's — check the front page and the footer against those
keys. A mismatch is the wrong pair of inputs, not a routing problem: stop, name
the two companies, and ask.

If the company node is empty — the normal case here — the site's owner is the
twin. That is the premise of the call, and the footer, the legal pages and the
JSON-LD are where the owner states itself, never the hero banner, which carries
the brand and not the registered name.

## 3 — pick at most five nodes, company first

From `graph.values.yaml`, in this order:

1. The `[company]` node — **always**, and it is op one. It is the whole reason
   the run exists: a layer with a filled company card is usable, and a layer
   with four filled side nodes and an empty company is not.
2. Up to four more, taken only from what the pages actually say, nearest the
   company first — its contacts and addresses, then what it sells, then people.

A node qualifies only if it is **empty** — no values under it in the tree. A
node that already holds values is left alone: lite never overwrites, because a
stored value came from a run that read more than two pages, and a marketing
page does not outrank a document. A node with children and a generic title
(`Clients [party]` over `Contract #1`) is a group label, never a target —
writing to it renames the group.

Nothing else is written. No `find_records`, no `type:`/`ref:` ops and so no
creates: a create is the one thing another ops file cannot undo, and it is
exactly the step that needs the checking this run does not do. A second client
or service the site names has no empty node left and goes unwritten, into the
report. A counterparty is never the twin, and a `[company]` record is never
created here.

## 4 — read the pages, at most two

**Page one is the one you were handed.** A `.md` — read it. A saved HTML page:

    python3 <skill-dir>/scripts/page.py <file> --base <url> --max 8000

It prints the title, the meta description, every JSON-LD block whole, then the
text. Read the JSON-LD first: an `Organization` block states the legal name,
the address, the phone and the socials as data rather than as copy, and on a
lite run that is most of the company card in one place. Add `--links` for the
map of what page two could be. No file at all → `read_page(url: "<url>")`.

**Page two, only if the company card is still short**, and only one:

    read_page(url: "<address>")

Choose it from what `[company]` is still missing, never from the menu:

| still missing | read |
|---|---|
| legal name, registration / VAT id | `/terms`, `/privacy`, `/legal`, `/impressum` |
| address, phone, email | `/contact`, `/contacts` |
| founding year, history | `/about`, `/company` |

Under `source_scope page`, skip this — the run is one page. Skip it too when
page one already answered the company node: an unspent read is a faster run,
not a missed one.

A page that comes back refused is not a page that said nothing. Record that it
was unreadable; do not spend another read on a different address to make up for
it.

## 5 — the fields of those types, one call

    python3 <skill-dir>/scripts/schema.py <export>/types.schema.yaml company <type2> <type3>

Name only the types you picked — never `--list`. A field title carries its
allowed values (`one of: draft, signed, archived`); the planner does not
enforce them, so a value outside the list is a silent wrong write.

## 6 — write the ops file

Write `<export>/graph.ops.yaml` from
`<skill-dir>/templates/graph.ops.template.yaml`, straight from the nodes you
picked — no draft, no prose list of the ops first. Ops only, no `#` comments:
the planner strips them.

A website is marketing copy with a few facts in it, and the default is **not
written**:

- "Founded in 1998" is a fact; "the leading provider in Europe" is not, and no
  field wants it.
- Every value carries `evidence_quote` — one span from the page, the shortest
  that carries it, under about 120 characters. **No quote, no write.**
- `confidence`: JSON-LD or a legal page, 0.9; body copy, 0.7; anything you had
  to reason to, leave unwritten rather than scored low.
- `source: website` where the type has the field — check its title for the
  allowed values first. Leave `gaps:` to the full skill.
- Never fill a field because its title suggests a plausible value.

Planner rules:

- `at:` is any **unique suffix** of the path, separator `" > "`. Quote it — a
  bare ` #` opens a YAML comment. The `[type]` after a node in
  `graph.values.yaml` is a legend, not a path segment: the address is
  `"COMPANY"`, never `"COMPANY [company]"`.
- `set:` keys are fields of **that node's type**; an unknown key fails the run.
- `rename:` for a title, never `title:` in `set:`; and only over a placeholder
  title (`Service #1`, `Untitled`), never over a name a person wrote.
  `describe:` for the description, never `description:`.
- No empty values — omit the key. Dates `"YYYY-MM-DD"`, quoted. Numbers bare,
  currency in its own ISO 4217 field.
- `under:`, `create:`, `append:` do not exist.

### One picture, and only the company's

`picture:` takes the absolute address of an image on the site; the apply copies
it into the workspace's storage. This run writes **at most one**, on the
`[company]` node, from the `og:image` or the `favicon` that came listed with
the page you already read — no page is opened to look for an image, and the
favicon is often the only raster picture of the site's owner, since a logo is
usually inline SVG.

Everything else about pictures belongs to the full skill: portraits, product
shots and branches cost judgement about what ties an image to a subject, and
this run does not spend it. A node that already carries a picture keeps it, an
SVG is refused, and an image the server cannot take is a warning while the rest
of the op lands.

## 7 — apply, once

    apply_graph(ops: "<export>/graph.ops.yaml", write: true)

No dry run and no confirmation — the ask for a lite fill *is* the instruction
to write it. All or nothing: one unknown field or ambiguous address stops the
run before anything is sent, so fix the ops file rather than reaching for
`partial`. Every op is an overwrite and the file replays, so a wrong write is
undone by the next ops file. The apply stamps `id:` onto each op, re-exports
into the same directory, and keeps `result.json` beside the ops file.

Do not re-run a want list afterwards to check the work. Leave the export where
it defaults to: whoever asked for the run reads `result.json` back by path and
cannot list a directory.

## 8 — report, and stop

One round. There is no second pass, no verify pass and no closing listing — if
the apply succeeded, the run is over.

Five lines, no more:

- the twin, and the one span of the site that identified it;
- the nodes written, by address, with the fields set and which page each came
  from;
- the picture, if one was written, and what tied it to the company;
- the pages read, in order — one or two, and whether the second was skipped and
  why;
- what the pages named and the run did not write, and why (no empty node of
  that type, a counterparty, past the five-node limit, a page that refused).

That last line is not optional. A lite run is a partial run by construction,
and a report that does not say what it left behind reads as a full one — and
here it would read as a site that had nothing to say.
