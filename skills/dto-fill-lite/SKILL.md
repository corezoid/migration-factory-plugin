---
name: dto-fill-lite
description: The quick pass of dto-fill. Reads only the head of one document — at most two pages — and writes at most five nodes of an empty Digital Twin layer, the company node first and always. No record lookups, no creates, no second round — one export, one read, one ops file, one apply. Use when the user wants a fast or cheap first fill of a layer, a company card out of a document, or says lite, quick, draft, preview, "хотя бы компанию", "по-быстрому", "черновой прогон". Triggers on "быстро заполни граф из файла", "лайт прогон dto-fill", "вытащи компанию из документа", "черновик DTO по документу", "quick fill the twin from this file", "lite dto-fill", "just get the company from this document", "/dto-fill-lite".
---

# dto-fill-lite — the head of a document, the top of an empty layer

`dto-fill` reads a whole source and routes everything in it. This reads the
head of one document and fills at most five nodes of a layer that starts
empty. It runs on a small model at low effort, so everything it does is fixed
in advance: how much of the file is read, how many nodes are written, which
node is written first, and how many rounds there are (one).

What it gives up is exactness, and it gives it up on purpose. Skip nothing
that is listed below and add nothing that is not — the checks the full skill
makes are what this one trades for speed, and re-introducing one of them by
hand is how a lite run turns into a slow run that is still only half a
`dto-fill`.

**Two hard limits, and they are the point of the skill**: at most two pages of
the file, at most five nodes written. Both are counted, and both are reported.

## The call

    /dto-fill-lite <file> layer_id <uuid> [gateway mw|sim]

`<file>` sits in the working directory; `layer_id` is the layer to fill — ask
for it when it is not given. `gateway` is `mw` or `sim` and names which
Simulator the layer lives on; the MCP server is already pointed at it, so the
label only tells you what a link you write down means.

There is no `source_url` here and no `read_page`: this run reads the file it
was handed and fetches nothing. A website source belongs in
`dto-fill-via-web-lite`, which is this skill's twin and spends its two pages on
the site instead; a source worth reading past page two belongs in `dto-fill`.

## 1 — export

    export_graph(layer: "<layer-uuid>", dir: "<dir>")

`dir` defaults to the server's working directory; leave it there unless the
user names one. Then read `graph.values.yaml` whole — an empty layer's tree is
small, one call, and it is the list of addresses you are allowed to write to.
`layer:` at its top is what goes into the ops file.

Do not read `types.schema.yaml` (~100 KB) and do not open `graph.ids.json`.

## 2 — the head of the document

    python3 <skill-dir>/scripts/top.py <file>

At most two pages, cut the same way every run, with a last line saying how much
of the file was left unread. That number goes in the report: a run that read
two pages of forty must say so, or it reads as a document that had little in it.

Read what comes back, all of it, once. Do not go back to the file for more —
if the head does not name the company, the answer is that it does not, and the
run says so.

`top.py` reads `.docx`, `.xlsx` and `.pptx` as well, through `office.py` beside
it and under this same budget — they are zips of XML, so `cat` on one prints
binary. What it refuses by name is an image and a legacy `.doc`/`.xls`/`.ppt`:
the first goes to Read, the second is a request for a different file. Either
way, take the head of it and come straight back.

## 3 — whose facts are these

A layer describes **one** company, the twin, and an empty layer cannot tell you
which — no stored `legal_name` to match against. So the twin is **the issuer**:
whose letterhead, registry footer and signature block the document carries.
Never the party in the client role, however large its name sits on page one.

| source         | issuer → the twin | client role → not the twin |
|----------------|-------------------|----------------------------|
| bank statement | the bank          | the account holder         |
| invoice, act   | the seller        | the bill-to party          |
| offer, order   | the employer      | the candidate              |

The issuer owns the repeating header and the signature block; the client sits
in an addressed-to field (`Bill to:`, `DENUMIRE COMPANIE:`). The file name
corroborates, it never decides.

Two issuers, or none that the head of the document names — write nothing, say
so, and stop. That is a finished lite run, not a failed one.

## 4 — pick at most five nodes, company first

From `graph.values.yaml`, in this order:

1. The `[company]` node — **always**, and it is op one. It is the whole reason
   the run exists: a layer with a filled company card is usable, and a layer
   with four filled side nodes and an empty company is not.
2. Up to four more nodes, taken only from what the head of the document
   actually said, nearest the company first.

A node qualifies only if it is **empty** — no values under it in the tree. A
node that already holds values is left alone: lite never overwrites, because a
stored value came from a run that read more than two pages. A node with
children and a generic title (`Clients [party]` over `Contract #1`) is a group
label, never a target — writing to it renames the group.

Nothing else is written. No record lookups (`find_records` is not used here),
no `type:`/`ref:` ops, no creates: a create is the one thing another ops file
cannot undo, and it is exactly the step that needs the checking this run does
not do. A fact with no empty node to go in goes unwritten and into the report.

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

- `at:` is any **unique suffix** of the path, separator `" > "`. Quote it — a
  bare ` #` opens a YAML comment. The `[type]` after a node in
  `graph.values.yaml` is a legend, not a path segment: the address is
  `"COMPANY"`, never `"COMPANY [company]"`.
- `set:` keys are fields of **that node's type**; an unknown key fails the run.
- `rename:` for a title, never `title:` in `set:`. `describe:` for the node's
  description, never `description:`.
- No empty values — omit the key. Dates `"YYYY-MM-DD"`, quoted. Numbers bare,
  currency in its own ISO 4217 field.
- Fill `source`, `evidence_quote` and `confidence` when the type has them.
  `evidence_quote` is one span, the shortest that carries the values, under
  about 120 characters. `confidence` is honest: two pages read at low effort is
  not 0.95. Leave `gaps:` to the full skill.
- `picture:` and `under:`/`create:`/`append:` are not used here.

Every value must be a value you saw in the text `top.py` printed. A field title
is not evidence: "the form asks for a VAT number" is not a reason to write one.

## 7 — apply, once

    apply_graph(ops: "<export>/graph.ops.yaml", write: true)

No dry run and no confirmation — the ask for a lite fill *is* the instruction
to write it. All or nothing: one unknown field or ambiguous address stops the
run before anything is sent, so fix the ops file rather than reaching for
`partial`. Every op is an overwrite and the file replays, so a wrong write is
undone by the next ops file. The apply stamps `id:` onto each op, re-exports
into the same directory, and keeps `result.json` beside the ops file with the
counts.

Leave the export where it defaults to: whoever asked for the run reads
`result.json` back by path and cannot list a directory.

## 8 — report, and stop

One round. There is no second pass, no verify pass and no re-read — if the
apply succeeded, the run is over.

Four lines, no more:

- the twin, and the one span of the document that identified it;
- the nodes written, by address, with the fields set on each;
- how much of the file was not read — `top.py`'s last line;
- what the head named and the run did not write, and why (no empty node of that
  type, a counterparty's facts, past the five-node limit).

That last line is not optional. A lite run is a partial run by construction,
and a report that does not say what it left behind reads as a full one.
