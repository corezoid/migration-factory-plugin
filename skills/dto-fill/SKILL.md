---
name: dto-fill
description: Read any source the user hands over — pdf, docx, xlsx, csv, md, txt, json, email, screenshot, a saved web page or a pasted URL, one file or several — decide whose facts they are, route them against a Digital Twin layer's own nodes and types, and emit a replayable graph.ops.yaml of the fields to update. Use whenever the user hands over a document, statement, website or export and asks to load, import, extract, fill, map, enrich or route it into the graph / DTO / twin / layer / Simulator, or asks what from a file fits the graph. Triggers on "залей документ в граф", "заполни DTO из файла", "наполни компанию из сайта", "что из этого файла можно внести в граф", "сформируй ops по документу", "import this doc into the twin", "fill the graph from this file", "enrich the company from this source", "make an ops file from this".
---

# dto-fill — sources → graph ops

Turn what the user hands over into `graph.ops.yaml` — edits addressed by path — and apply it in the same run. Routing is
the hard half; the write is the last step, not a question for the user.

`graph.values.yaml` is a read-only projection. Never edit it; the way back is an ops file.

## The call

    /dto-fill <file> layer_id <uuid> [source_url <url> [source_scope site|page]] [gateway mw|sim]

`<file>` sits in the working directory; `layer_id` is the layer to fill. The rest is said only when known:

- `source_url` — the file is a web page rendered to Markdown, and this is the address it came from. Read the file as
  that page and route what it says; `read_page` fetches another address when one is genuinely needed, and
  `source_scope page` says even that is not — the file *is* the whole source. Working a site the other way round, from
  the layer's empty fields outwards and page by page, is `dto-fill-via-web`, and mf-api sends a website source straight
  there: a site arriving here is somebody's manual call.
- `gateway` — which Simulator deployment the layer lives on, `mw` or `sim`. The MCP server is already pointed at it
  (`SIM_BASE_URL`, `SIM_API_KEY` and
  `SIM_WORKSPACE_ID` in the project's environment); the label is there so a link or a name you write down means the
  right instance. Absent on a manual run: the server then uses the gateway its `.mcp.json` pins.

## The export

    export_graph(layer: "<layer-uuid>", dir: "<dir>")

`layer` is required — ask for it when you do not have it. `dir` defaults to the server's working directory, with no
per-layer subdirectory; reuse an export already sitting there unless the user names another layer.

| file                | role                                                           |
|---------------------|----------------------------------------------------------------|
| `graph.values.yaml` | the tree: every node, its type, the values it holds            |
| `types.schema.yaml` | per type: field names, and the title saying what goes in each  |
| `graph.ids.json`    | `path -> uuid`; never read it, but keep the ops file beside it |

Keep the ops file in that directory — `apply_graph` reads the types from beside it, which makes an apply one request per
node instead of sixty.

The export is the **canvas**, not the register. It holds the nodes somebody placed on the layer — usually one empty
placeholder per type per branch — while the form behind a type holds every record of it: the ones earlier runs created
off the canvas, the ones other documents brought in, the ones a person typed into Simulator. So the canvas cannot answer
whether a counterparty already exists, and one tool does:

    find_records(type: "<slug>", values: ["<identity>", …], dir: "<dir>")

Ask it before writing **any** record — the identity of each subject in one call. A value comes back FOUND with the
record's `ref` (write to that), not found (the only answer that licenses a create), or unknown when the probe failed (do
not create on it). A "similar titles" line under a not-found value is a lead to check by eye — the whole title did not
match — never a ref to write to.

By default it probes the fields the type itself marks as identity keys, then the actor's title. What those fields are is
a property of the form, so read them in `types.schema.yaml` rather than assuming: a title saying *identity key* is the
mark. When the source identifies its subjects by something the form does not mark — an account number, a document
number, an email — name those fields in `fields:` and they are probed in the order you give them.

## The sources

`.pdf` — pull the text layer yourself: `pdftotext -layout <file> -`, else
`pdfplumber` (keeps a label and its value on one line better than PyMuPDF), plus
`extract_tables()` when a table holds what the text layer loses. A scan has no text layer and there is no OCR engine
here: render its pages and read them as pictures — `pdftoppm -png -r 150 -f 1 -l 4 <file> page` — and say in `gaps`
that the document was transcribed from images.

`.docx .xlsx .pptx` →

    python3 <skill-dir>/scripts/office.py <file> [--notes]

Text formats are not the problem — `cat` reads `.json .csv .md .txt .eml` and a saved page, Read opens an image — but
these three are zip archives of XML, and `cat` on one prints binary. `office.py` prints what a reader would see:
`.docx` and `.pptx` on the standard library alone, `.xlsx` through `openpyxl`, and a `.docx` with its headers and
footers around the body, because the letterhead and the registry footer are what Pass 0 judges the issuer by. Slide
notes come with `--notes`. A legacy `.doc .xls .ppt` has no reader here and the script names the format to ask for
instead of half-working; a workbook of thousands of rows is a dataset rather than a document, and a statement among
them belongs to `bank-statement-to-jsonl`. A pasted URL →

    read_page(url: "<address>")

one address per call, that page as Markdown, nothing followed — reading a site is you choosing pages and asking for
each. A `.md` handed over with
`source_url` is a web page already rendered by the same provider: read it as the page, and with `source_scope site`
treat it as the door to the site, not the site (see *The call*). Read every source whole, in chunks if long — never
sample. A number you never saw cannot be routed.

A page that comes back refused is not a page that said nothing. The message says whether asking again is worth anything;
either way what goes in `gaps` is that the page was unreadable, never that the site is silent on what it might have
held.

Several thin sources are normal. When two disagree, prefer the one closer to the registry (registry > contract >
statement > invoice > website) and say so in
`gaps`. Do not hunt for sources the user did not give you — enrichment is opt-in, and enriched fields carry
`source: website` and a lower `confidence`.

## Pass 0 — whose facts are these

A layer describes **one** company, the twin. `CORPORATION / HOLDING`, `COMPANY`,
`ORG STRUCTURE`, `FINANCE`, `HRS`, `ADOC` hold its own facts; `CRM`,
`ERP > Suppliers` and `…[party]` hold everyone else. Get this wrong and a counterparty's money lands in the twin's
treasury.

Read `graph.values.yaml` whole — ~10 KB, one call, and it serves every pass. A stored `legal_name` / `registration_id`
in the `[company]` node settles the twin: match the sources against those keys. If it is empty, the twin is **the
issuer** — whose letterhead, registry footer and signature block the source carries — never the party in the client
role, however large its name sits on page 1.

| source          | issuer → twin    | client role → counterparty    |
|-----------------|------------------|-------------------------------|
| bank statement  | the bank         | the account holder            |
| invoice, act    | the seller       | the bill-to party             |
| offer, HR order | the employer     | the candidate                 |
| a website       | the site's owner | anyone it names as a customer |

Judge by where a name sits, not how often it appears: the issuer owns the repeating footer of registry data and the
signature block, the client sits in an addressed-to field (`DENUMIRE COMPANIE:`, `Bill to:`). The file name
corroborates, it never decides. Two issuers or none — ask before routing anything.

**Ownership propagates.** A record takes the side of its subject, and a bank account's subject is its holder, not
whoever printed the statement. A client's account never goes under `FINANCE`; it goes on the counterparty record when
that type has a field for an account — check its schema, do not assume, since some counterparty types carry one and some
do not — and otherwise it is not written at all. Same for people (`HRS` is the twin's own staff) and for sites. When the
twin flips, re-run the routing — do not patch it.

## Passes 1–4, repeated

Carry what each pass decides in your reasoning and report the per-round counts. Do not write a scratch file of the
facts: within one run you already remember what you settled a minute ago, the ops file is where a decision becomes
durable, and a document of quotes and values written before the ops file is that file drafted twice.

**1 — extract.** Facts out of the sources, graph unseen: side (`own`/`counterparty`/`neither`), subject, attribute,
value, exact quote. Do not normalize — record what the source says, not what a form wants.

A source built of rows — a statement, a ledger, a register, an export — is not its header. Every distinct **subject**
the rows name is a fact of pass 1: the counterparties a statement pays, with the account and registration number each
row carries. A pass that lists the account and stops has read 5% of the file and will converge anyway, because the
rounds below only ask whether the last round wrote something. Close that hole here: list the subjects first, then their
attributes.

**2 — type.** Identify candidate types from pass 1, then query only those field definitions — `types.schema.yaml` is ~
100 KB, never read it whole:

    python3 <skill-dir>/scripts/schema.py <export>/types.schema.yaml <type1> <type2>

Avoid `--list` (dumps all 50 types); 2–4 types per call. A field title carries its allowed values
(`one of: draft, signed, archived`) — the planner does not enforce them, so a value outside the list is a silent wrong
write.

**3 — route.** The question is *does this record already exist*, and the canvas cannot answer it. Ask `find_records` —
one call per type, every subject of that type in `values:`, each given as the identity a register would hold it by. Take
that from the type: the field its schema marks as an identity key is the value to pass, and where the source does not
carry it, pass what the source does carry and name that field in `fields:`. Then, per subject, in order:

1. **FOUND** → write to that record with `type:` plus **its** `ref:`, the one the answer printed. Never a ref of your
   own: a derived ref that differs from the stored one creates a second copy of a record you just found. A hit with no
   ref was made by hand and no ref reaches it — carry its `id:` instead.
2. **A node on the layer matches** the same identity → update it with `at:`, naming what you overwrite. Prefer this
   address when the record is on the canvas: the path is legible and the plan shows what it sits under.
3. **NOT FOUND, and an empty placeholder** of the right type sits under the right parent → fill it and `rename:` it
   readable. Worth preferring for the *first* subject of a type, because a filled placeholder is the one that shows on
   the canvas.
4. **NOT FOUND and no placeholder left** → give the subject a record of its own, `type:` plus `ref:` (below). This is
   the normal outcome for the second and every further subject of a repeating type, not a failure.
5. **UNKNOWN** (the check could not run) or **no type fits at all** → leave it unwritten and say so in `unrouted`. An
   unchecked subject is never created:
   that is the one mistake here nothing can undo.

Never conclude "it is not there" from your own reading of anything else. The answer to that question is the tool's
output and nothing else — no file you parsed, no list you searched, no memory of an earlier run.

Only when a type boundary is ambiguous:

    python3 <skill-dir>/scripts/nodes.py <export>/graph.values.yaml --type document --empty

Never overwrite a node or a record holding *different* identity keys — that is a second record, not this one. A node
with children and a generic title (`Clinets [party]` over `Contract #1`) is a group label: writing to it renames the
group, so it is never the target.

**A record is off the canvas, not out of the graph.** It is created as an actor of its form and appears in no layer
until someone places it there by hand, so it will not be in the next `graph.values.yaml` — list created records
separately when you report, and never as though they landed on the picture. What a record must not be is a way around a
singleton: there is one company per layer, and a second `[company]` record is always a routing mistake. Group labels are
not records either. If the subject fits no type, leave it in
`unrouted` and say why — that list is read.

`ref:` is the business key of a record you are creating, and it is what stops the same document creating it twice. Build
it from the subject's own identity, deterministically, so the next run of the same source produces the same string:
`contract-57-2026`, `iban-md24aG0000…`, `emp-koval-olena`. A ref you invent freshly each run (timestamps, counters,
anything from the conversation) creates a second record every time the document is loaded.

**One scheme per type per run.** Pick the key the type identifies records by — the field whose title says *identity
key* — and use it for every record of that type; fall back to the normalised name only where that field is genuinely
absent from the source, and say so in `gaps`. Mixing one key's prefix for some subjects and another's for the rest is
how the same company ends up in the register three times: the next document carries the other key and finds nothing.

Carry each settled subject as the op it will become, not as a paragraph about the op it will become — the file is
written once, at the end, from these decisions (see Output).

**4 — verify.** Challenge every value: is it in the source, or did the field title suggest it? Is it on the side pass 0
assigned? Does it obey the enum and format? Anything that fails is dropped, never patched into a guess.

**Repeat** until a round adds no op and changes no value, or after four rounds. Before calling it converged, answer one
more question: which subjects named in the sources appear in neither the ops file nor `unrouted`? A round that adds
nothing because pass 1 stopped early looks exactly like a round that adds nothing because the source is exhausted. That
list is the next round's work.

## Rules the planner enforces

- `at:` is any **unique suffix** of the path, separator `" > "`. Quote it — a bare ` #` opens a YAML comment. The
  `[type]` printed after every node in
  `graph.values.yaml` is a legend, not a path segment: the address is
  `"COMPANY"`, never `"COMPANY [company]"`.
- `type:` + `ref:` is the other address, and the two are exclusive with `at:`:
  the slug of a type on the layer (the name in `[brackets]` in
  `graph.values.yaml`) and a business key. The record is read by that key first and created only when nothing answers to
  it, so the file replays; `set:` keys are fields of that type, exactly as with `at:`. `ref:` is required — a create
  without one is refused, not guessed at.
- Adding `id:` to a `type:` op reads the record by that uuid instead of by the ref, which is how a record with no ref of
  its own (made by hand in Simulator, reported by `find_records` as NO REF) is updated rather than duplicated. An apply
  stamps `id:` onto every op anyway — leave the stamps where they land.
- `rename:` for a title, never `title:` in `set:`. `describe:` for the node's description, never `description:` in
  `set:` — 22 of 50 types have a field by that name, and setting both writes the description twice.
- `set:` keys are fields of **that node's type**; an unknown key fails the run.
- `picture:` puts an image on the node — the absolute http address of a picture the source names (a page, a link in a
  document); a document with no image addresses in it has no pictures to give. Write it only when the source ties that
  image to this subject, and say what tied them in the report. The image is copied into the workspace's storage, a node
  that already carries one keeps it, and one image belongs to one subject.
- `under:`, `create:`, `append:` do not exist — placing a node on the canvas is done in Simulator, not from here. A
  `type:` op still creates the record; what it cannot do is put it on the picture. No type fits at all → the fact goes
  unwritten.
- No empty values; omit the key instead. Dates `"YYYY-MM-DD"`, quoted. Numbers bare, currency in its own ISO 4217 field.
- Every op is an overwrite, so the file replays: an unchanged run writes nothing and reports "already applied".

Fill the provenance fields when the type has them: `source`, `evidence_quote`,
`confidence` (honest — inferred is not 0.95) and `gaps`. The last two carry a cost per record and earn it only when kept
short.

`evidence_quote` is **one span, the shortest that carries the values** — the line the record came from, not every line
it touched. Stitching five fragments together with slashes copies the document into the graph a second time and tells a
reader no more than the first fragment did. Keep it under about 120 characters; where the values genuinely come from two
places, quote the one that identifies the record.

`gaps` is **what a reader would expect to find and will not** — never a roll call of every empty field. "registration
number not printed on a statement" is worth a line because somebody will go looking for it; "contact_email not in a bank
statement" is not, because nobody expected an email there. A value the source carried but you deliberately did not write
belongs here too, with the reason. If nothing meets that bar, omit the key.

## Output

Write `<export>/graph.ops.yaml` from
`<skill-dir>/templates/graph.ops.template.yaml` — `layer:` from
`graph.values.yaml`, `source_doc:` the user's sources. Ops only, no `#`
comments: the planner strips them. What must survive into the graph goes in
`describe:` or `gaps:`.

**The file is the plan — do not draft it twice.** Decide subject by subject, then write the file once, straight from
those decisions: no prose version of it first, no list of the ops to come, and never a restatement of an op's fields or
values before writing them. Deciding is the work and belongs in your reasoning — *"NETOPIA is a payment processor, so it
is a supplier"* — but rehearsing `set: supplier_name, iban, category, country…` for each of fifteen records writes the
file once in prose and once for real, and on a long statement that is the single most expensive thing a run does.
Nothing is riskier for skipping it: `apply_graph` prints the whole diff before a byte is sent, every op is an overwrite,
and a wrong file is undone by the next one.

Apply it in one call, no dry run and no confirmation — the ask to load a source *is* the instruction to write it:

    apply_graph(ops: "<export>/graph.ops.yaml", write: true)

All or nothing: one unknown field or ambiguous address stops the run before anything is sent. Leave `partial` off and
fix the ops file instead. A write stamps each op with the uuid it resolved to — leave those stamps in place and address
new ops by `at:`; the one `id:` you write yourself is the uuid of a ref-less record `find_records` reported. It
re-exports into the same directory.

It also keeps `result.json` beside the ops file: holes filled, actors updated and records created, with the uuid of
each. It is cumulative and deduplicated by uuid, so applying the same file again — after a fix, after a partial run —
does not count a node twice. Its numbers are the totals for the document, not for the last call; quote them as such, and
do not edit the file by hand.

Whoever asked for the run reads that file back by path and cannot list a directory, so leave the export where it
defaults to — the working directory — unless the user names a directory themselves.

Report: the twin and the evidence for it on the first line, then the applied ops by node with their side, the per-round
counts, and a bare list of what you did not write — never silently imply the whole source landed. Build that list from
the subjects the source names, not from the ops that failed: a subject pass 1 never extracted leaves no trace anywhere
else. A wrong write is undone by another ops file.

List any **created records separately**, with their `ref:` and the uuid the apply reports: they are records of their
form and are on no graph, so a reader who goes looking for them on the canvas will not find them, and a create is the
one thing another ops file cannot undo.
