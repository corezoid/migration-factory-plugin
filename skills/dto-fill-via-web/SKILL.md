---
name: dto-fill-via-web
description: Fill the empty nodes of a Digital Twin layer from a company's own website. Reads the layer first, works out which of its unfilled fields a website could answer at all, reads only the pages that would carry them (read_page), and writes a replayable graph.ops.yaml. It also puts the pictures the site shows — a logo, a person, a product, an office — on the nodes they belong to. It fills holes and never overwrites; a subject whose slot on the canvas is already taken — the second client or service a site names — becomes a record of its own, after find_records reports there is none. Use when the user hands over a site address plus a layer and asks to fill, complete, top up or enrich the twin from the web, or asks what the site has for the graph's empty fields. Triggers on "заполни граф с сайта", "наполни DTO из сайта", "дозаполни пустые акторы", "что на сайте есть для пустых полей", "собери данные с сайта в граф", "fill the twin from its website", "complete the empty nodes from the web", "enrich the layer from the site", "/dto-fill-via-web".
---

# dto-fill-via-web — the graph asks, the site answers

`dto-fill` starts from a source and looks for a slot. This starts from the
empty slot and looks for a page that answers it. Same export, same ops file,
same apply; the order of the two questions is the whole difference, and it
decides everything downstream. The site is not read to be understood — it is
read to be interrogated against a list.

Two rules hold the run together. **Nothing already written is overwritten** —
a stored value came from somewhere, and a marketing page does not outrank a
document. And **a subject whose slot on the canvas is taken gets a record of
its own**: the first client the site names fills the empty `[party]`
placeholder, the second becomes a record of that type. That create is the one
irreversible thing here, which is why `find_records` runs before every one of
them and an unchecked subject is never created.

## The call

    /dto-fill-via-web <url> [<file>] layer_id <uuid> [source_scope site|page] [gateway mw|sim]

- `<url>` — the site to interrogate, normally the twin's own front page. It is
  also the boundary: pages of this site, nothing beyond it.
- `<file>` — that address, already read, sitting in the working directory. It
  comes in one of two shapes and they are read differently:
  - **Markdown** (`.md`) — the page as the pipeline already rendered it,
    through the same provider `read_page` uses. Read it directly; fetching the
    address again would return the same text.
  - **raw HTML** (`index.html`, a page someone saved) — read it with
    `page.py` below, never with `cat`.

  Absent, or thin because the page renders itself with JavaScript,
  `read_page(url)` fetches the address instead.
- `source_scope` — how much of the site is in play. `site`, or nothing said at
  all, means `<url>` is a door: read on into the pages the layer's holes point
  at. `page` means the caller pointed at this one address — fill what it
  answers and wander nowhere, however tempting the menu.
- `layer_id` — the layer to fill. Required; ask when it is not given.
- `gateway` — `mw` or `sim`, which Simulator the layer lives on. The MCP
  server is already pointed at it; the label only tells you what a link you
  write down means.

## Pass 1 — what the graph is missing

    export_graph(layer: "<layer-uuid>", dir: "<dir>")
    python3 <skill-dir>/scripts/holes.py <export>/graph.values.yaml <export>/types.schema.yaml

One call, and it is the want list: every node that holds fewer values than its
type has fields, with the names of the fields it is missing. Read-only fields
and the four provenance fields are left out — no ops file can write the first
and the run writes the second about itself.

This is the pass that must be **complete**. A hole nobody listed is a hole
nobody looks for, and the report then reads as though the site had nothing to
say about a field that was never asked about. Do not sample the list, and do
not rebuild it from memory later: re-run the script.

**Complete is a thing you check, not a thing you assume.** The listing states
how many blocks follow, numbers every one of them `[n/N]`, and closes with
`# end of want list — N of N block(s) printed`. A read that does not end on
that line was cut somewhere in the middle, and everything past the cut is a
branch of the layer this run will never ask about — the failure that looks
exactly like a site with nothing to say. When it happens, do not carry on from
the part you got: re-read the rest with `--under <branch>` per branch of the
tree, each part checked for its own closing line. Never pipe the want list
through `head`, and never read "the first part to get started".

A node with no image is a hole too: the listing marks it `no picture`, from
the export's own `pictures:` block, and `--no-picture` narrows to those. A node
whose every field is filled still appears when the face is what it is missing.

`--under <text>` and `--type <slug>` narrow it while you work; `--titles`
prints under each field name the title that says what goes in it — six times
the output, so it belongs on a narrowed listing; `--addr` prints the shortest
unique suffix, which is what an `at:` op wants; `--empty` keeps only the nodes
holding nothing at all. Those come first when you get to writing: an empty
node is a placeholder somebody put on the canvas for exactly this, and a
filled placeholder is what a person actually sees on the graph. A node whose
type the schema does not describe is listed apart, after the blocks — it has
no writable fields here, so it is not a hole.

Read `graph.values.yaml` whole as well (~10 KB). The want list says what is
missing; the tree says what the layer already knows, which is what tells you
whose site you are holding.

## Pass 2 — which of those holes a website can answer

Now cut the want list down, before reading a single page. Most of a twin is
not on the web, and a run that goes looking for it burns twenty pages to fill
nothing.

| a website answers | a website does not |
|---|---|
| a picture of the subject: the logo, a person, a product, an office | a photograph of anything it does not show |
| legal and trading name, brand | balances, turnover, anything from a ledger |
| registration / VAT id (footer, terms, privacy, impressum) | contract numbers, dates, parties |
| addresses, offices, phone, email, socials | bank accounts, payment details |
| what it sells: services, products, price list | headcount, salaries, internal org structure |
| founding year, history, certifications | named staff below the public leadership page |
| public leadership, sometimes the team | anything about a counterparty's internals |

The kept rows are the wants. Order them: the twin's own identity first
(`[company]`, `[corporation]`), then its contacts and addresses, then what it
sells, then people. Pictures are never a reason to read a page: they are taken
from the pages the want list already chose. A field from a dropped row is **not** a gap the site left —
it was never the web's to fill, and the report says so in one line rather than
listing it per node.

The field titles decide the borderline cases, so read them — `holes.py
--titles` on the branch in question prints them next to the names, cut at 140
characters, and `schema.py <export>/types.schema.yaml <slug>` prints one whole,
with the allowed values of an enum-by-convention field.

Wants are fields, but they are not the only thing to look for. A site also
names **subjects** — clients, suppliers, services, offices — and a repeating
type whose nodes are all taken is not a closed question: the second and every
further subject of it becomes a record in pass 5. So read for subjects in the
same pass you read for values, and carry them on the list. A subject nobody
extracted is a record nobody creates, and it leaves no trace anywhere in the
report.

## Pass 3 — whose site is this

A layer describes **one** company. If `[company]` already holds a
`legal_name` / `registration_id`, the site must be that company's: check the
front page and the footer against those keys before writing anything. A
mismatch is not a routing problem to solve — it is the wrong pair of inputs.
Stop, say which two names you are looking at, and ask.

If the company node is empty, the site's owner is the twin. That is the
premise of the call, and the footer, the legal pages and the JSON-LD are where
the owner states itself.

Counterparty nodes (`[party]`, suppliers, clients) carry only what the page
**states about that party** — the full spelling of its name, its own address,
a link to its site. A logo wall is not evidence of a relationship, a case
study is not a contract, and neither licenses a fact about the counterparty's
internals. A site that says "500+ clients" has named nobody, and a count
creates nothing. The twin's own holes come first regardless; the parties the
site does name are routed in pass 5, where the second one of a type gets a
record rather than somebody else's node.

## Pass 4 — read, one page at a time

**The page you were handed first.** A `.md` is already the provider's
rendering — read it and move on. A saved HTML page costs nothing to read and
is often the richest thing on the site:

    python3 <skill-dir>/scripts/page.py index.html --base <url>
    python3 <skill-dir>/scripts/page.py index.html --base <url> --links

The first prints the title, the meta description, every JSON-LD block whole,
then the text (`--max N` cuts the text and says by how much — raise it or read
the address with `read_page`, never work from the head of a page). Read the JSON-LD first: an `Organization` block states the
legal name, the postal address, the phone and the social profiles as data
rather than as copy, and that is the difference between reading a fact and
inferring one from a banner. The second prints the links — the map for what to
read next. A page whose content is rendered by JavaScript may hold its only
static copy inside `<noscript>`; `page.py` reads that, and a `cat` would have
shown neither.

**Then the live page when the saved one is thin or stale:**

    read_page(url: "<address>")

One address per call, that page as Markdown, nothing followed — it renders
exactly the way the pipeline renders a website source, so a page read here and
a document handed over compare like for like. A page that comes back refused
is not a page that said nothing: the message says whether asking again is
worth anything, and either way what you record is that the page was unreadable.

Every read also lists the page's pictures — each address with the alt text or
caption the page gives it — plus the page's `og:image` and `favicon`. That
list, not the Markdown, is what a `picture:` is written from: the text carries
an image only where one sits in it. **The file you were handed carries the
same list**, under "Pictures on this page" — it was captured with the page, so
the front page's own logo is already in front of you and re-reading that
address for it buys nothing.

**Choose the next page from the want list, not from the menu.** For each
remaining want, the page that would carry it:

| want | where it lives |
|---|---|
| legal name, registration / VAT id | footer, `/terms`, `/privacy`, `/legal`, `/impressum` |
| address, phone, email, socials | `/contact`, `/contacts`, footer, JSON-LD |
| services, products, prices | `/services`, `/products`, `/solutions`, `/pricing`, `/tariffs` |
| the catalogue itself — which products exist at all | the site's own top navigation, and the section page each entry points at |
| branches, offices, points of presence | `/branches`, `/locations`, `/map`, `/stores`, `/contacts` |
| founding year, history, certifications | `/about`, `/company` |
| leadership | `/team`, `/about`, `/management` |
| a picture of the subject | the page that already answers for it; for the company, the front page's `og:image` or `favicon` |

**What the company sells, and where it stands, are worth a page each.** After
identity and contacts, those are the two branches of a canvas a website
actually answers — the catalogue (`[erp_products]`, services, price list) and
the facilities (`[org_structure_item]`: branches, offices, outlets) — and they
are also the two a run drops first, because neither is named in a footer and
both cost a read. Spend the reads. The front page's top navigation *is* the
catalogue's index: every product the site sells is an entry in it, and the
menu came down with the page you were already handed. A locations page is one
read even when it renders a map — the list under the widget is often static,
and when it is not, that is a finding to report rather than an assumption to
act on. A branch of the canvas left unasked is not a gap the site left.

If the front page's links name a machine-readable index of the site — an
`llms.txt`, an `/index.md`, a sitemap — read that before guessing addresses:
it is the site stating its own map, and it costs one call.

**Budget.** Under `source_scope page` it is one page — the one you were given —
and the rest of this section does not apply. Otherwise about ten pages. The
pages the canvas itself asks for — the twin's identity and contacts, then its
catalogue and its locations — come off that budget first; what is left buys
the opportunistic reads. Stop earlier on any of: the want list is empty, two
pages in a row filled nothing, or the remaining wants are all in the
right-hand column of pass 2. Reading further is how a five-minute run becomes
an hour with the same result. Every page you read goes in the report, in
order; so does every page you decided against.

Where a site has one language per subdomain or path, prefer the one the layer
is already written in, and say when a value came from a translated page.

## Pass 5 — extract, check, route, write

A website is marketing copy with a few facts in it. The default is *not*
written.

- **Fact or claim.** "Founded in 1998" in an about page is a fact. "The
  leading provider in Europe" is not, and no field wants it. A number in a
  headline ("500+ clients") is written only if a field asks for exactly that,
  with the quote and a low confidence.
- **Identity comes from the legal surfaces** — footer, terms, privacy,
  impressum, JSON-LD — never from the hero banner, which carries the brand and
  not the registered name.
- **Verbatim unless the field demands otherwise.** A service is called what
  the price list calls it. Dates become `"YYYY-MM-DD"`, currency goes in its
  own ISO 4217 field, numbers stay bare — the field's title says which.
- **Never fill a field because its title suggests a plausible value.** Every
  value carries `evidence_quote`: one span from the page, the shortest that
  carries it, under about 120 characters. No quote, no write.
- `confidence`: JSON-LD or a legal page, 0.9; body copy, 0.7; anything you
  had to reason to, leave unwritten rather than scored low.
- `source: website` where the type has the field — check the title for the
  allowed values first, a value outside the enum is a silent wrong write.
- `gaps` is what a reader would expect and will not find, one line, and only
  when the field is empty today. Not a roll call of what the site omitted.

### A face for the node

Any node can carry an image — a logo, a portrait, a product shot, a branch —
and `picture:` takes the address of one on the site. It is copied, not linked:
the apply fetches it and uploads it into the workspace's storage.

    - at: "…КОМАНДА > Іванов Іван"
      picture: "https://acme.example/team/ivanov.jpg"

**The page has to tie that image to that subject**, and the ops file does not
carry the tie — say it in the report. What counts as one: JSON-LD
(`Organization.logo`, `Person.image`, `Product.image`), an alt text, caption
or file name naming the subject, the card that holds the image and the name
together, and `og:image` or `favicon` for the twin's own `[company]` and
nothing else — the favicon is often the only raster picture of the site's
owner, since a logo is usually inline SVG. A
hero banner, a stock photograph and an image that merely sits on a subject's
page name nobody. **One image, one subject**: a group shot is the portrait of
none of the five people in it, and a second op naming the same image is
refused.

The server does the rest and needs no judgement from here: a node that already
has a picture keeps it, an SVG is refused (the canvas cannot draw one from
storage — take the favicon or the og:image instead), so is anything under
32×32, shaped like a rule by its proportions, or over 20 MB, and an image it
cannot take is reported while the rest of the op lands.

### Ask the register before writing any record

The export is the **canvas**, not the register. It shows the nodes somebody
placed on the layer — usually one empty placeholder per type per branch —
while the form behind a type holds every record of it: the ones other
documents brought in, the ones earlier runs created off the canvas, the ones a
person typed into Simulator. So a filled `[party]` node does not mean this
party is known, and an empty canvas does not mean it is not. One tool answers
that, and nothing else does:

    find_records(type: "<slug>", values: ["<identity>", …], dir: "<export>")

One call per type, every subject of that type in `values:`. Pass the identity
the register would hold it by — the field the type's schema marks as an
identity key — and where the site does not carry that, pass what the site does
carry and name those fields in `fields:`. A "similar titles" line under a
not-found value is a lead to check by eye, never a ref to write to.

Never conclude "it is not there" from your own reading of the export: that
answer is this tool's output and nothing else.

### The ladder

Per subject, the first rung that applies. The register is asked **before** the
canvas is offered a slot, and the order matters more than it looks:

1. **A node already on the canvas carries this same identity** → it *is* the
   subject: fill its empty fields with `at:`, overwrite nothing.
2. **`find_records` says FOUND** → the record exists off the canvas. Write to
   it with `type:` plus **its** printed `ref:` — never a ref of your own, or
   you make a second copy of a record you just found. A hit with no ref was
   made by hand: carry its `id:` instead. This rung outranks the empty
   placeholder below it: a subject the register already holds, stamped onto a
   free slot on the canvas, becomes a second copy of itself — one on the
   picture, one in the register — and the picture is the one that looks right.
3. **NOT FOUND, and an empty node of the right type sits under the right
   parent** → fill it with `at:` and `rename:` it to the real name. This is the
   preferred home for a genuinely new subject, and a filled placeholder is what
   a person sees on the canvas.
4. **NOT FOUND, and every node of that type is taken** → give the subject a
   record of its own: `type:` plus a deterministic `ref:`. This is the normal
   outcome for the second and every further client, supplier or service a site
   lists — not a failure, and the reason the run does not stop at the canvas.
5. **UNKNOWN** (the probe could not run), or no type fits the subject at all →
   write nothing and say so in the report. An unchecked subject is never
   created: that is the one mistake here nothing can undo.

Two answers look like licences to write and are not:

- **Several records for one value.** The register is already split — an
  earlier run, or two, created the same subject under different refs. Picking
  one of them spreads this run's values across copies and hides the split.
  Write nothing, report the subject with every ref and id the tool printed,
  and say it needs merging.
- **A "similar titles" line under a not-found value.** `mono × АТБ` against a
  stored `mono x АТБ` is one character apart and is the same product; creating
  on it makes a near-duplicate that no future lookup will reconcile. Similar
  is not found, and it is also not a licence to create — read the near-miss
  and decide by eye, or leave the subject unwritten and say so.

`ref:` is the business key, and it is what stops the next run of the same site
creating the record a second time. Build it from the subject's own identity,
deterministically — on a website that is usually the name or the party's own
domain: `party-acme-retail`, `site-acme-ro`, `service-onboarding`. **One
scheme per type per run**, so the next document, arriving with the other key,
finds these records instead of duplicating them. Never a timestamp, a counter
or anything from the conversation: a ref invented afresh makes a new record
every run.

A created record is **off the canvas** — an actor of its form, on no layer,
absent from the next export. List it separately when you report, with its ref
and the uuid the apply printed, and never as though it landed on the picture.
`[company]` is a singleton: a second company record is always a routing
mistake, not an overflow.

Then write every decision into the ops file **once**, straight from these
decisions — no prose draft of the ops first, no restatement of fields and
values before writing them. Deciding is the work; the file is where it becomes
durable.

## What this run does not do

- **No overwrites, pictures included.** A field that already holds a value
  keeps it, and so does a node that already carries an image. If the site
  contradicts a stored value, that is a line in the report, not an op: the
  stored value came from a document, and a marketing page does not outrank
  one. (Writing an empty `gaps` or `source` on such a node is still filling a
  hole, and still allowed.)
- **No create the register was not asked about.** Rung 4 is reachable only
  through `find_records`, and only NOT FOUND licenses it.
- **No second twin.** One company per layer; a company the site names that is
  not the twin is a counterparty, and a `[company]` record is never created
  here.
- **No crawl, and no leaving the site.** Not LinkedIn, not a registry, not a
  news article — the address given, its pages, and nothing else. If a hole
  needs a registry, say so.
- **No renaming a node a person named.** `rename:` belongs on a placeholder
  title (`Service #1`, `Untitled`), where the real name is the point of
  filling it.

## Rules the planner enforces

- `at:` is any **unique suffix** of the path, separator `" > "`. Quote it — a
  bare ` #` opens a YAML comment. The `[type]` after a node in
  `graph.values.yaml` is a legend, not a path segment: `"COMPANY"`, never
  `"COMPANY [company]"`.
- `type:` + `ref:` is the other address, exclusive with `at:`: the slug of a
  type on the layer and a business key. The record is read by that key first
  and created only when nothing answers to it, so the file replays. `ref:` is
  required — a create without one is refused, not guessed at.
- Adding `id:` to a `type:` op reads the record by that uuid instead of by the
  ref — the way to update a record made by hand, which `find_records` reports
  as NO REF.
- `set:` keys are fields of **that node's type** (or of that record's type);
  an unknown key fails the run.
- `rename:` for a title, never `title:` in `set:`. `describe:` for the node's
  description, never `description:` in `set:`. `picture:` for the node's
  image, never a field in `set:` — it is a property of the actor, and no field
  of any form reaches it.
- `picture:` is the absolute address of an image on the site: a relative path
  or a `data:` URI is refused, since the image is copied from that address.
- `under:`, `create:`, `append:` do not exist — a node is placed on the canvas
  in Simulator, not from here.
- No empty values; omit the key instead.
- Every op is an overwrite, so the file replays: an unchanged run writes
  nothing and reports "already applied". Applying stamps each op with the uuid
  it resolved to — leave the stamps where they land.

## Output

Write `<export>/graph.ops.yaml` from
`<skill-dir>/templates/graph.ops.template.yaml`: `layer:` from
`graph.values.yaml`, `source_doc:` the site address and the pages read. Ops
only, no `#` comments — the planner strips them.

    apply_graph(ops: "<export>/graph.ops.yaml", write: true)

One call, no dry run and no confirmation: the ask to fill the graph *is* the
instruction to write it, the diff comes back with the result, and every op is
an overwrite. All or nothing — one unknown field stops the run before a byte
is sent; leave `partial` off and fix the file. The apply re-exports into the
same directory, so close the loop with the evidence rather than with memory:

    python3 <skill-dir>/scripts/holes.py <export>/graph.values.yaml <export>/types.schema.yaml

Read that one to its `# end of want list` line as well. A closing check run
through `head` proves nothing about the branches below the cut, and those are
the ones the report is about to call empty.

Report, in this order:

1. the twin, and what identified it;
2. **filled** — node by node, the fields written and the page each came from,
   and the picture where one was taken, with what tied it to that node — the
   ops file does not carry that, so this is where it is checked;
3. **created** — each record separately, with its `ref:` and the uuid the
   apply printed, and the one line that says these are actors of their form
   and are on no layer: a reader who goes looking for them on the canvas will
   not find them, and this is the half of the run another ops file cannot
   undo;
4. **still empty, and the site was asked** — the wants that survived pass 2
   and stayed unfilled, with where you looked — including the nodes still
   without a picture, and whether the page showed none or showed images tied
   to nobody;
5. **out of the web's reach** — the holes pass 2 dropped, counted in one line,
   not listed;
6. **unwritten** — subjects the site names that no type fits, and the ones
   whose `find_records` probe came back UNKNOWN. Both are read; neither was
   created;
7. the pages read, in order, and the ones you decided against.

Leave the export where it defaults to — the working directory — unless the
user names a directory: whoever asked for the run reads these files back by
path and cannot list a directory.
