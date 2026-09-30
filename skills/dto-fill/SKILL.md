---
name: dto-fill
description: Read any source the user hands over — pdf, docx, doc, xlsx, xls, pptx, ppt, odt, ods, odp, rtf, csv, md, txt, json, email, .msg, html, screenshot, a saved web page or a pasted URL, a GitHub/git repo URL, a prompt naming several sources of different kinds at once, one file or several — work out what kind of document it is first, hand a bank statement straight to bank-statement-to-dto, hand too many files at once to dto-fill-loop, and otherwise decide whose facts the rest are, route them against a Digital Twin layer's own nodes and types, emit a replayable graph.ops.yaml of the fields to update, AND separately look for any repeating log of dated values against a subject — meter readings, inspection or assessment scores, sensor logs, anything a parser can turn into rows — parse it the same way a bank statement's rows are parsed and post it onto that actor's accounts. No format is refused — a reader that needs a package not already installed says the exact line to run, and this session may install it. Use whenever the user hands over a document, statement, website, repo or export and asks to load, import, extract, fill, map, enrich or route it into the graph / DTO / twin / layer / Simulator, to record readings or a series against an actor, or asks what from a file fits the graph. Triggers on "залей документ в граф", "заполни DTO из файла", "наполни компанию из сайта", "что из этого файла можно внести в граф", "сформируй ops по документу", "занеси показания на актора", "стяни репозиторий и разбери", "import this doc into the twin", "fill the graph from this file", "enrich the company from this source", "make an ops file from this", "post these readings onto the actor", "pull this repo and go through its files".
---

# dto-fill — sources → graph ops

Turn what the user hands over into `graph.ops.yaml` — edits addressed by path — and apply it in the same run. Routing is
the hard half; the write is the last step, not a question for the user.

This is the universal entry point: mf-api and a manual `/dto-fill` call alike always land here for a document, never
on a specialised skill directly. What kind of document it turns out to be is this skill's own first decision (Pass 0)
— a bank statement is handed to `bank-statement-to-dto` from there; everything else is read and routed below. Filling
the graph is not the only thing a source can be worth: whether it also carries a postable series of dated values
against a subject — not only a statement's transactions — is a separate question this skill answers on every run
(Pass 6).

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

`<skill-dir>` is this skill's own directory: `skill_view` returns it as
`skill_dir`, and the sibling skills sit beside it. Do not guess it — a
guessed path that happens to exist is how a run reads a half-copied tree
and concludes the script was never shipped.

`.pdf` — pull the text layer yourself: `pdftotext -layout <file> -`, else
`pdfplumber` (keeps a label and its value on one line better than PyMuPDF), plus
`extract_tables()` when a table holds what the text layer loses. A scan has no text layer and there is no OCR engine
here: render its pages and read them as pictures — `pdftoppm -png -r 150 -f 1 -l 4 <file> page` — and say in `gaps`
that the document was transcribed from images.

`.docx .xlsx .pptx .doc .xls .ppt .odt .ods .odp .rtf .msg .html` →

    python3 <skill-dir>/scripts/office.py <file> [--notes]

Text formats are not the problem — `cat` reads `.json .csv .md .txt .eml` and a saved page, Read opens an image — but
everything in that list is a binary container of some kind, and `cat` on one prints garbage or nothing. `office.py`
prints what a reader would see: `.docx` and `.pptx` on the standard library alone (they are zip archives of XML), `.xlsx`
through `openpyxl`, a `.docx` with its headers and footers around the body because the letterhead and the registry
footer are what Pass 1 judges the issuer by, `.msg` through `extract-msg`, and raw `.html` with the standard library.
Slide notes come with `--notes`. The legacy binaries `.doc .xls .ppt` and the OpenDocument siblings `.odt .ods .odp`
and `.rtf` have no reader of their own in this repo — `office.py` converts each with LibreOffice into the OOXML sibling
it already knows, and if that tool or a pip package a reader needs is missing, the script names the exact install line
rather than half-working or refusing the format. Run that line and retry; this session is allowed to install what a
source needs.

**Nothing here is refused for lack of a tool.** This session has permission to install any package or system tool
this run needs — a library to read an unfamiliar format, `git` itself to clone a repo, whatever else a step below
calls for and does not find already here. A format `office.py` does not recognise at all is not a dead end: run
`file <path>` to see what the bytes actually are, find what reads that format, install it (see "Where an install
goes" just below) and read the file directly; a short one-off script is a normal answer here, not a failure. Only
report a source as unreadable, or a tool as unavailable, after actually trying and having it still not work — never
because the extension or the tool was simply not one this repo already had a name for.

**Where an install goes.** `PYTHONPATH` answers that, and it is worth a look before installing anything.
A runtime that names a writable directory there — `/opt/data/py-deps` on the Hermes gateway — is one whose
container is rebuilt from its image at every restart: a package installed into the container is gone by the next
run, so it goes on the volume instead (`uv pip install --target /opt/data/py-deps <package>` — `uv`, because that
image ships no pip at all). A system tool goes there too, unpacked under `/opt/data` with a wrapper on
`/opt/data/bin`, which is already on PATH. `apt-get install` is not the answer there twice over — the run is not
root, and the package would not survive the restart anyway — but `apt-get download` and `dpkg-deb -x` both work
unprivileged, so unpacking one is something this run can do by itself; only a whole missing dependency tree needs
an operator. `/opt/data/DEPENDENCIES.md` says how the tools already on that volume were put there. Nothing
writable on `PYTHONPATH` means an ordinary machine, and there it is `pip install` and whichever package manager
that machine actually has — the scripts name the one they find rather than guessing, since a `brew` line on a
Debian image and an `apt-get` line on a Mac are equally unrunnable. The scripts beside this file already print the right line for
whichever host they are on — this is the rule they follow, and the one to follow when installing something they
do not know about.

A workbook of thousands of rows is a dataset rather than a document. A bank statement among them was already sent
elsewhere in Pass 0, below; anything else that turns out to be mostly structured, repeating rows — a register, a
ledger, a subscriber list, a CRM export, a large table export of any kind — is not read whole either. Sample one page
or a handful of rows to work out the column layout the same geometric way `bank-statement-to-jsonl` works out a
statement's (x-coordinate bands for a PDF, column index for a sheet or CSV), write a short throwaway parser, stream
it into a working list instead of holding the source in context, and — where the source prints any totals or row
counts of its own — reconcile against them before trusting a single row. What the parser extracts then goes through
Pass 2 onward exactly like any other extracted fact; nothing changes because the reading was done by a script.
`<skill-dir>/../bank-statement-to-jsonl/SKILL.md` is the reference for the technique — the probe, the column
detection and the reconciliation gates — not for its bank-specific record shape. A pasted URL →

    read_page(url: "<address>")

one address per call, that page as Markdown, nothing followed — reading a site is you choosing pages and asking for
each. A `.md` handed over with
`source_url` is a web page already rendered by the same provider: read it as the page, and with `source_scope site`
treat it as the door to the site, not the site (see *The call*). Read every source whole, in chunks if long — never
sample. A number you never saw cannot be routed.

A page that comes back refused is not a page that said nothing. The message says whether asking again is worth anything;
either way what goes in `gaps` is that the page was unreadable, never that the site is silent on what it might have
held.

**A repo URL is cloned, not read as a page.** `github.com/<owner>/<repo>`, the same shape on gitlab.com or
bitbucket.org, with `.git` — clone the whole repository (`--depth 1`, no history needed) into a subdirectory of
the working directory — `git` missing is the same case "Where an install goes" already covers, not a reason to
stop. Read what comes out of the clone the same way anything else handed over is read:
`office.py`, `pdftotext`, `cat`, whichever the file's own extension calls for. Skip `.git/` itself and a
project's own tooling artifacts (`node_modules`, `vendor`, `dist`, `build`, `.venv`) — dependencies and build
output are never documents, whatever else a repo might also hold. A clone that fails (private, bad URL, network)
is said in the report — not read as the source having nothing to say. Everything that comes out of it continues
below, in this same pass, the same way several files handed over directly already would.

**A prompt can be the source list itself** — several addresses of different kinds, pasted together in one
message rather than named one call at a time. Work out each address's own kind before reading any of it: a repo
URL clones (above), a plain address is `read_page`'d, a direct download link is fetched and then read by
whatever format it turns out to be — never assume every link in one message is the same kind because the first
one was.

Several thin sources are normal. When two disagree, prefer the one closer to the registry (registry > contract >
statement > invoice > website) and say so in
`gaps`. Do not hunt for sources the user did not give you — enrichment is opt-in, and enriched fields carry
`source: website` and a lower `confidence`.

## Pass 0 — what kind of document is this

Before the export, before anything else: decide what you are holding. Sample the head the same cheap way the readers
above already would — one page of `pdftotext`/`pdfplumber`, the head `office.py` prints, the first screen of a text
file — and answer one question from that alone, per source when there are several (a cloned repo, a multi-link
prompt): is this a bank statement, a ledger of dated rows each debiting or crediting an account, however many rows,
cover page or not? A header, an account block and one row of the table settle it; you do not need to read further to
find out.

**A bank statement is not this skill's job, and reading the whole thing here to decide that would be the mistake
`bank-statement-to-dto` exists to avoid.** That skill reads only the header — never the rows — to place the bank and
the client on the graph, while a second agent turns the rows into a ledger in parallel; deciding "is this a
statement" by reading the statement throws away exactly the split it is built to keep.

- **Bank statement** → hand that source to `<skill-dir>/../bank-statement-to-dto/SKILL.md`, from its own Pass 0,
  carrying `layer_id` and `gateway` straight over — its `account_name` is optional; leave it unset and its default
  applies. When several sources arrived together and only some of them are statements, this splits per source: the
  statements go to `bank-statement-to-dto`, the rest continue below in this same pass — a mixed batch is not a
  reason to send the whole run to either skill alone.
- **Anything else** — a contract, an invoice, an offer or HR order, a register, a report, a website capture already
  rendered to a file, a CRM export — continue below, in this skill.

This is the only point at which a source's own shape sends the run to a different skill entirely. A large
structured dataset that is not a bank statement does not leave here — see "The sources" above for how it is read
without being read whole.

## Pass 1 — whose facts are these

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

## Passes 2–5, repeated

Carry what each pass decides in your reasoning and report the per-round counts. Do not write a scratch file of the
facts: within one run you already remember what you settled a minute ago, the ops file is where a decision becomes
durable, and a document of quotes and values written before the ops file is that file drafted twice.

**2 — extract.** Facts out of the sources, graph unseen: side (`own`/`counterparty`/`neither`), subject, attribute,
value, exact quote. Do not normalize — record what the source says, not what a form wants.

A source built of rows — a ledger, a register, an export (parsed in Pass 0's "large dataset" case rather than read
raw) — is not its header. Every distinct **subject** the rows name is a fact of pass 2: the counterparties a ledger
pays, with the account and registration number each row carries. A pass that lists the account and stops has read 5%
of the file and will converge anyway, because the rounds below only ask whether the last round wrote something. Close
that hole here: list the subjects first, then their attributes.

**3 — type.** Identify candidate types from pass 2, then query only those field definitions — `types.schema.yaml` is ~
100 KB, never read it whole:

    python3 <skill-dir>/scripts/schema.py <export>/types.schema.yaml <type1> <type2>

Avoid `--list` (dumps all 50 types); 2–4 types per call. A field title carries its allowed values
(`one of: draft, signed, archived`) — the planner does not enforce them, so a value outside the list is a silent wrong
write.

**4 — route.** The question is *does this record already exist*, and the canvas cannot answer it. Ask `find_records` —
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

**5 — verify.** Challenge every value: is it in the source, or did the field title suggest it? Is it on the side pass 1
assigned? Does it obey the enum and format? Anything that fails is dropped, never patched into a guess.

**Repeat** until a round adds no op and changes no value, or after four rounds. Before calling it converged, answer one
more question: which subjects named in the sources appear in neither the ops file nor `unrouted`? A round that adds
nothing because pass 2 stopped early looks exactly like a round that adds nothing because the source is exhausted. That
list is the next round's work.

## Pass 6 — transactions on an actor's accounts, if the source has any

Filling the graph and creating actors is not the whole job when the source itself is, in whole or in part, a
repeating log of dated values against a subject — not only a bank statement (routed away in Pass 0), but a utility
bill's meter readings, a series of inspection or assessment scores, a sensor or temperature log, a rent ledger,
anything that is naturally many rows of *"on this date, this subject had this value"* rather than a handful of facts
about it. Look for this on every source, not only ones that look financial: a register of dated numeric readings
against a subject qualifies exactly as a statement does, whatever the values mean.

**Two questions, in order, and either one answered no ends this pass:**

1. **Is there anything here to post at all?** Most sources have nothing — a contract, an invoice header, an HR order
   name facts, not a series. Skip this pass entirely when the source is not a repeating log of dated values against a
   subject; do not force a single fact (a one-off invoice total, a single reading) into a posting of one row.
2. **Can it be parsed?** The rows have to be structured enough to band — a table, a sheet, a delimited export, fixed
   columns in text. A few numbers scattered through prose is not this; if there is anything worth keeping from those,
   it is a fact for Pass 2, not a transaction here.

When both answer yes, write a throwaway parser the same way `bank-statement-to-jsonl` writes one for a statement:
sample a page or a handful of rows to find the column layout (x-coordinate bands for a PDF, column index for a sheet
or CSV), declare what each column means, stream the whole file into JSONL rather than reading it, and validate against
whatever the source checks itself with — a printed total, a row count, a checksum, used exactly like a statement's own
totals. A source with nothing to check against is still read once in full and reported as unverified, never silently
trusted because nothing contradicted it. **Read `<skill-dir>/../bank-statement-to-jsonl/SKILL.md` before writing the
parser** — it is the reference for the technique in full (the probe, the column bands, the streaming, the
reconciliation gates), not only for statements.

**The record shape is the one `post_statement` reads**, whatever the source actually holds:

    {"transaction_date":"2026-04-07","debit_sum":"0.00","credit_sum":"302.50","description":"…"}

A row that is not naturally a debit and a credit — one meter reading, one temperature, one score — still needs one
side non-zero: put the value on `credit_sum` (or `debit_sum` — pick one side and hold it for the whole file) and leave
the other `"0.00"`, and say plainly in the report that the number is a reading, not money. `currency` is the source's
own unit when it names one; when the source is not money at all, leave `currency` off the row and pass nothing for
`currency_name` either — the posting then falls back to `XXX`, ISO 4217's own "no currency", which says *not a
currency* honestly rather than inventing one.

Post it once the parser's rows are written and validated:

    post_statement(account_name: "<what this is>", actor_id: "<the subject's actor uuid, from Pass 4>", path: "<the jsonl>")

`account_name` names the kind of reading, not always `"Bank Statement"` — `"Meter Reading"`, `"Temperature Log"`,
`"Inspection Score"`, whatever the source is actually a series of; it is created on first use, the same as any other
account-name category. `actor_id` is the uuid the routing above (Pass 4) resolved or created for this subject — never
one remembered from a search or an earlier run: posting against the wrong actor is the one mistake here nothing
downstream can undo. **Run it once with `dry_run: true` first** on anything unfamiliar — the same call plus that one
flag — read back the per-currency (or per-reading) totals it reports, and only then run the call above for real; it is
idempotent by each row's own ref, so a rerun of the same file posts nothing twice. Pass `timezone` when the source
names one and its rows carry no offset of their own, the same reason a statement needs it.

**A source can be a log against many subjects, not one** — a shared meter log for a block of properties, a combined
rent ledger for several tenants, a batch of readings across a fleet. When it is, give every row a
`uniq_actor_field_value` instead (whatever the source uses to tell its subjects apart — a meter id, a unit number, a
tax id), and pass `actor_field`+`actor_type` to `post_statement` instead of one `actor_id`: each row then resolves its
own subject from its own value, rather than every reading landing on whichever one subject Pass 4 resolved. See
`bank-statement-to-jsonl`'s "One actor, or many" for how to tell which case this is.

Report this pass **separately** from the graph ops — what was found, whether it was parsed and validated, and the
posting's own counts (written, duplicates, per-side totals) — never folded into "the ops applied" as though a posting
were the same kind of write. A source with nothing to post here says so in one line; that is the common case, not an
omission.

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
do not edit the file by hand. `post_statement` (Pass 6, when the source had anything to post) writes its own half of
the same `result.json` — the tally is then the whole run, not the graph half alone.

Whoever asked for the run reads that file back by path and cannot list a directory, so leave the export where it
defaults to — the working directory — unless the user names a directory themselves.

Report: the twin and the evidence for it on the first line, then the applied ops by node with their side, the per-round
counts, and a bare list of what you did not write — never silently imply the whole source landed. Build that list from
the subjects the source names, not from the ops that failed: a subject pass 2 never extracted leaves no trace anywhere
else. A wrong write is undone by another ops file.

List any **created records separately**, with their `ref:` and the uuid the apply reports: they are records of their
form and are on no graph, so a reader who goes looking for them on the canvas will not find them, and a create is the
one thing another ops file cannot undo.

**Then, separately, Pass 6's own report** — never merged into the ops report above, since a posting is not the same
kind of write: what was found (or that nothing was), whether it parsed and validated, and the posting's own counts —
rows written, duplicates skipped, per-side totals — quoted from `post_statement`'s own answer, never from memory of
what the parser produced.
