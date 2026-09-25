# migration-factory-plugin

A plugin for filling a Digital Twin graph layer from documents and from the
web, and for turning bank statements into JSONL. It installs into Claude Code
and into Hermes Agent, from the one directory: the skills and the server are
the same, only the manifest each host reads differs.

Seven parts — two skills that work in opposite directions over the same export,
a quick pass of each, one that does not touch the graph at all, one that drives
three of the others at once, and the server underneath them:

- **the `dto-fill` skill** — reads whatever the user hands over (pdf, docx,
  xlsx, screenshot, saved page, email…), decides *whose* facts they are, routes
  them against the layer's own types, and writes a replayable `graph.ops.yaml`;
- **the `dto-fill-via-web` skill** — the other direction: it reads the *layer*
  first, lists every field still empty, keeps the ones a website could
  plausibly answer, and then reads only the pages that would carry them. It
  fills holes and never overwrites; a subject whose slot on the canvas is
  already taken — the second client a site names — becomes a record of its
  own, after `find_records` says there is none;
- **the `dto-fill-lite` and `dto-fill-via-web-lite` skills** — the same two
  runs with everything expensive removed: two pages of input (the head of a
  document, or the page handed over plus at most one more), at most five nodes
  of a layer that starts empty, the company node first and always, one round
  and no record lookups. They exist for the runs where a rough company card now
  is worth more than a complete routing later, and they are the ones to reach
  for on a small model at low effort;
- **the `bank-statement-to-jsonl` skill** — the odd one out: it writes no ops
  and never touches a layer. It turns a bank statement (pdf, xlsx, csv, text)
  into one JSON object per transaction, by measuring which column each number
  sits in rather than by reading the statement — so a four-hundred-page file
  costs what a four-page one costs. It ships its own parsing library and two
  gates, the second of which checks the result against the totals the statement
  prints about itself;
- **the `bank-statement-to-dto` skill** — the orchestrator: it starts a second
  agent parsing the statement's rows to JSONL and, while that runs, reads only
  the header to settle which bank issued the statement and which client it was
  issued for, puts the bank on the layer as the company, resolves the client
  against the client *form* rather than against the canvas — an occupied
  placeholder means a record of its own, not an overwrite — and then collects
  the parser and posts every row onto the client's accounts. The main run never
  reads past the header, so the size of the statement stops mattering;
- **the `migration-factory-plugin` MCP server** — five tools: three that talk to Simulator, so
  the skills no longer need this repo's `make` targets or their working
  directory, and one that reads a web page, for when the source is an address
  rather than a file.

|  | `dto-fill` | `dto-fill-via-web` | the two `-lite` skills |
|---|---|---|---|
| starts from | a source the user hands over | the layer's empty fields | the company node of an empty layer |
| asks | where does this fact belong | which page answers this hole | what fills the company card |
| may create a record | yes, after `find_records` | yes, when the canvas slot for that type is taken and `find_records` found none | never |
| may overwrite | yes, it is an overwrite model | never — a filled field stays as it is | never — they write empty nodes only |
| reads | files, and pages when a source is a URL | one site, the pages the want list picks | two pages, and nothing else |
| writes | as much as the source carries | as much as the want list answers | at most five nodes |
| rounds | up to four | until the want list is answered | exactly one |

## Tools

| tool | what it does |
|---|---|
| `export_graph(layer, dir?)` | writes `graph.values.yaml`, `graph.ids.json`, `types.schema.yaml` into `dir` (default: the current directory, no per-layer subdirectory) |
| `apply_graph(ops, write?, layer?, partial?, keep_export?)` | plans an ops file and returns the diff; `write: true` applies it and refreshes the export beside it |
| `find_records(type, values, fields?, dir?)` | answers, per value, whether a record of that type already carries that identity — the check before a create |
| `read_page(url, images?)` | renders one web page to Markdown — one address, one page, nothing followed — with an inventory of the pictures on it unless `images: false` |

`apply_graph` plans and writes in one call: a `write: true` run returns the same
diff a plan does, which is why the `dto-fill` skill applies straight away rather
than stopping on a dry run. Omitting `write` plans only, for when you want to
look first. Ops are overwrites, so replaying an unchanged file writes nothing
and reports `already applied`, and a wrong write is undone by another ops file.

An op addresses its subject one of two ways:

| address | what it writes |
|---|---|
| `at: "<path suffix>"` | a node on the layer |
| `type: <slug>` + `ref: <business key>` | a record of that type, created when the ref finds none |

An op writes fields with `set:`, a title with `rename:`, a description with
`describe:` — and the node's image with `picture:`, the address of a picture
on the source:

```yaml
  - at: "КОМАНДА > Іванов Іван"
    picture: "https://acme.example/team/ivanov.jpg"
```

The image is **copied, not linked**: an actor's picture is a path in the
workspace's storage and never a URL, so applying fetches the address, checks
the bytes and uploads them. Four rules are the server's and need no judgement
from the caller — a node that already carries a picture keeps it; one image
belongs to one subject, so the second op naming it is refused; PNG, JPEG, GIF
and WebP are taken and nothing else (the canvas does not draw an SVG from
storage); and anything under 32×32, shaped like a rule by its proportions, or
over 20 MB is not a picture of a subject. The limits are loose on purpose: a
real site serves the portrait its CMS was given (a bank's board page came back
with 7 MB JPEGs), a company wordmark is wide by nature (one was 1260×198), and
a favicon — often the only raster picture of a site's owner, since logos are
frequently inline SVG — is 48×48 at best. An image that cannot be taken is reported and the rest
of the op still lands: the values are the substance of a write, the face is
what makes the node recognisable.

Uploads go to the workspace in `SIM_WORKSPACE_ID`, or to the one the actor's
own form names when that is unset.

The second address is for a subject the canvas has no slot for: the layer's nodes of
that type all hold different records. The type itself has to be on the layer —
the dictionary in `types.schema.yaml` is derived from the forms of the nodes
placed there, so a slug with no node on the layer is not a type an op can name.
The record is created as an actor of its form and is **not placed on the
canvas** — linking a node to its parent
needs the layer edge endpoint, which is not wired, and a node on the canvas
with no edge is worse than none. So a created record does not appear in the
refreshed `graph.values.yaml`; its `ref:` and the uuid stamped back into the
ops file are the handles on it, and someone places it on the layer in
Simulator if it belongs there. Being off the layer also means the layer's
share does not reach it, so the record is shared to the session's group
(`SIM_GROUP_ID`) the moment it exists — view and modify, that actor only. A
share that failed is a warning in the report and the record stands, the same
way a picture that could not be taken is. `ref:` is required, and it is what keeps the
same document from creating the record twice: the ref is read first and only a
miss creates. A create is the one thing here another ops file cannot undo.

### Records, and the check before a create

A layer is a view, not the register. It carries the nodes somebody placed on
it — typically one empty placeholder per type per branch — while the form
behind the type carries every record: the ones earlier runs created off the
canvas, the ones other documents brought in, the ones a person typed into
Simulator. A document naming fifteen counterparties cannot be routed against
the canvas alone.

`find_records` answers the one question that matters before writing a record:

```
find_records(type: "suppliers", values: [
  "RO43RNCB0175148248250001", "IULIUS MALL CLUJ SRL", "RO99NOSUCH0000001",
])

suppliers (form 670083) — probed tax_id, iban, title

  RO43RNCB0175148248250001     FOUND by iban
      IULIUS MALL CLUJ SRL   ref: iban-RO43RNCB0175148248250001  [78969c80-…]
  IULIUS MALL CLUJ SRL         FOUND by title
      IULIUS MALL CLUJ SRL   ref: iban-RO43RNCB0175148248250001  [78969c80-…]
  RO99NOSUCH0000001            not found

2 found, 1 not found
```

Each value is probed field by field and the first hit ends that value's search.
Which fields those are comes from the form, not from this server: by default
the ones whose title marks them a *strong identity key*, and then the actor's
title. Nothing here knows what an IBAN or a tax number is — when a source
identifies its subjects by a field the type does not mark, name those fields in
`fields` and they are probed in that order (`"title"` included, if you want it
among them).

The gateway matches a field exactly and case-sensitively, so a value shaped
like an identifier is also tried uppercased and stripped of spaces — an account
number read off a document rarely matches the stored one otherwise. A value
shaped like a name is not: stripping the spaces out of one produces a string no
register has ever held.

The title probe earns its keep. A record created from a source that carried no
registration number has none stored, so probing the marked key alone would miss
it and duplicate it on the next document. It matches the whole title, case and
spacing aside: a record whose title merely *contains* the value ("Alfa" against
"Alfa-Bank JSC") is listed as *similar*, and similar is not found — a lead to
check by eye, never a ref to write to.

FOUND means write to that record by **its** ref. A hit with no ref was made by
hand and no ref lookup reaches it — carry its id in the op's `id:`. Only *not
found* licenses a create, and a value whose probe failed is unknown rather than
absent.

There is deliberately no listing and no file. An earlier version dumped a
form's records into JSON for the caller to search; the caller searched it
wrong, read "no match" out of its own bug, and created fifteen records without
having checked one. A verdict the tool computes cannot be misread that way.

### What a write leaves behind

Besides refreshing the export, a `write: true` run keeps `result.json` beside
the ops file — the running tally of what this document has done to the graph:

```json
{
    "количество заполненных дырок": 10,
    "количество обновленных акторов": 5,
    "количество созданных акторов": 5,
    "заполненные дырки": ["<uuid>", "..."],
    "обновленные акторы": ["<uuid>", "..."],
    "созданные акторы": ["<uuid>", "..."]
}
```

It is cumulative and keyed by uuid, because applying the same file more than
once is normal — after a partial run, after an edit, or just to be sure. Each
node is counted the first time it is written and never again, and it stays in
the list it first landed in: a hole filled today is an ordinary node tomorrow,
and writing to it again must not make it both a filled hole and an updated
actor. The lists are the record; the three counts are derived from them.

Pictures are not tallied here. A picture that was taken is on the node for
anyone to see, and one that was not is already in the run's output as the line
saying why.

### Reading a page

`read_page(url)` is the one tool here that has nothing to do with the layer:
it renders one web page to Markdown through Firecrawl and hands the text back
whole. It needs no Simulator key, and the graph tools need no Firecrawl one.

It exists because a source is not always a file. A website source reaches the
agent as the Markdown of **one** address — mf-api renders it with this same
provider and hands it over as the document — so when the address was a site
root, what the agent holds is the front page and nothing else. Whether the
rest of the site is worth reading is then its judgement, made with the layer
in front of it, and `read_page` is what it makes that judgement with: an
"about" page for a twin whose `[company]` node is empty, a "contacts" page for
the address fields, one call per page it decided on.

There is no crawl, here or in mf-api. Four hundred product listings are not
what an empty `legal_name` needs, and a page fetched here renders exactly like
the document the agent already has, so the two compare like for like.

Every read brings the page's pictures with it: every image address, each with
the alt text or caption the page gives it, plus the page's own `og:image` — on
a front page almost always the logo, and stated as data rather than inferred
from a banner. That text is the only thing that ties a photograph to a
subject, and the Markdown does not carry it: an image survives into the text
only where it sits in the text, and the logo in a header the reader already
stripped is not there at all. It is on by default because a picture nobody was
shown is one no node ever gets; `images: false` turns it off for a read that
is only about what the page says.

The provider returns main content only — navigation, ads and cookie banners
dropped. A page it could not read at all (a login wall, a bot check, a dead
link) comes back as an error saying whether asking again is worth anything,
and a page that renders to no text is an error rather than an empty answer:
"the reader could not see it" and "the site says nothing about it" license
different next moves, and only the second licenses writing that down.

## Reading a document

Most of what the user hands over needs nothing: `.txt .md .csv .json .eml`, a
saved page and the Markdown mf-api renders a website into are text, and `cat`
is the whole reader. A `.pdf` has `pdftotext -layout` and `pdfplumber` on the
image. What is left is `.docx`, `.xlsx` and `.pptx` — zip archives of XML that
`cat` prints as binary — and `skills/dto-fill/scripts/office.py` opens them:

    python3 <skill-dir>/scripts/office.py <file> [--chars N] [--rows N] [--notes]

`.docx` and `.pptx` cost nothing to open — `zipfile` and the standard library
are the whole dependency, which is the point, because the cc-api image has
neither `python-docx` nor `python-pptx` and a run that stops to install one is
a run that stops. `.xlsx` goes through `openpyxl`, which *is* on the image, and
says the one pip line if it ever is not. A `.docx` comes back with its headers
and footers around the body, in that order: Pass 0 decides whose facts a
document holds from the letterhead and the registry footer, and both live in
their own parts of the archive, so a body-only read would hand the model a
document with no issuer on it. Slide notes are held back until `--notes` asks
for them. A legacy `.doc`/`.xls`/`.ppt` has no reader here and the script names
the format to ask for instead of half-working.

There is no OCR on the image — no tesseract, no ocrmypdf — so a scanned PDF has
no text to pull. The way through is to render its pages and read them as
pictures (`pdftoppm -png -r 150 <file> page`), which recovers the words but not
their coordinates: enough for `dto-fill`, never enough for the banding that
`bank-statement-to-jsonl` is built on, which is why that skill asks for a text
layer instead.

## Filling from the web (`dto-fill-via-web`)

    /dto-fill-via-web <url> [<file>] layer_id <uuid> [source_scope site|page] [gateway mw|sim]

mf-api sends every website source here rather than to `dto-fill`: a source
that carries an address is a site, and the address leads the command because
`<file>` is only that one page, already rendered. `source_scope page` bounds
the run to it; `site`, or nothing said, lets the layer's holes decide which
further pages get read.

The same export, read backwards. `holes.py` joins the tree with the schema and
prints every node that holds fewer values than its type has fields, with the
missing field names and their titles — the want list. A node with no image is
on it too, marked `no picture`: the export writes a `pictures:` block naming
the nodes that already carry one, so a blank face is a hole the listing can
state rather than a question nobody asked. That list, cut down to
what a website could plausibly carry (a legal name, yes; a bank balance, no),
decides which pages get read, and nothing else does: the run reads `/contact`
because an address is missing, not because a menu offered it.

`<file>` is the copy of the front page already on disk, normally `index.html`.
`page.py` reads it: title, meta description, every JSON-LD block whole, then
the text — and `--links` prints the map for choosing the next page. The
JSON-LD is the point of bothering with the saved HTML at all, since an
`Organization` block states the legal name, address and phone as data rather
than as marketing copy. A page whose body is rendered by JavaScript usually
keeps its only static copy inside `<noscript>`, which `page.py` reads and a
`cat` would not have shown. Everything after the saved page comes from
`read_page`, one address per call.

Pictures come off the pages the want list already chose — the run never opens
a page to look for an image. What it takes is what the page ties to a subject:
a JSON-LD `logo`/`image`, an alt text or caption naming the person or product,
a card that holds the image and the name together, or `og:image` for the
twin's own company node. A hero banner and a stock photograph name nobody and
are left; one image is bound to one subject, so a group shot does not become
the portrait of five people.

What it will not do is as much of the design as what it does. A stored value
is never overwritten — and neither is a picture: a site that contradicts one
produces a line in the report, not an op, because the stored value came from a
document and a marketing page does not outrank one — and nothing outside the
given site is read.

Creating is the one irreversible half, and it is reached only down a ladder:
an empty node of the right type gets filled; a node already carrying that
identity gets its remaining holes filled; a record `find_records` reports gets
written to by **its** ref; and only a subject that comes back NOT FOUND with
every node of its type taken becomes a record of its own. That is the normal
outcome for the second and every further client, supplier or service a site
lists, and an UNKNOWN probe never licenses it.

The two scripts live in `skills/dto-fill-via-web/scripts/`; `holes.py` imports
the tree and schema parsers from `skills/dto-fill/scripts/`, because one
parser per file format is the point and both skills always ship together.

## The quick pass (`dto-fill-lite`, `dto-fill-via-web-lite`)

    /dto-fill-lite <file> layer_id <uuid> [gateway mw|sim]
    /dto-fill-via-web-lite <url> [<file>] layer_id <uuid> [source_scope page] [gateway mw|sim]

Same export, same ops file, same apply — and a fixed budget instead of a
judgement about how much to read. Both read two pages and write at most five
nodes, the `[company]` node first and always. A layer with a filled company
card is usable; a layer with four filled side nodes and an empty company is
not, so the one node that is never traded away is that one.

What they remove is everything that costs a round trip or a second thought:
no `find_records`, no `type:`/`ref:` ops and therefore no creates, no second
round and no verify pass. Only empty nodes already on the canvas are written,
so a lite run cannot overwrite what a fuller one put there, and the plugin's
one irreversible operation is out of its reach entirely. That is what makes
them safe on a small model at low effort — the checks they skip are the checks
a create would need.

`dto-fill-lite` takes the document path: `top.py` cuts the head of the file to
two pages the same way every run and prints how much it left, and the run
fetches nothing at all. A document worth reading past page two belongs in
`dto-fill`. The report is required to state what was left unread and unwritten:
a lite run is partial by construction, and one that does not say so reads like
a full one.

The web one spends its two pages differently: page one is the address it was
handed, page two is chosen for whatever the company card is still missing — a
`/terms` for the registration id, a `/contact` for the address — and it is not
spent at all when page one already answered, or under `source_scope page`. It
writes one picture at most, on the company node, from the `og:image` or the
favicon that came listed with a page it had already read; portraits and product
shots cost judgement about what ties an image to a subject, and that is the
full skill's to spend. It runs no want list: on an empty layer `holes.py`
prints seventy blocks, and five nodes is the whole budget anyway.

`top.py` lives in `skills/dto-fill-lite/scripts/`, beside that skill's own copy
of `schema.py` and of `office.py`; a `.docx`/`.xlsx`/`.pptx` goes through
`office.py` under the same budget and comes back with the same "left unread"
line, and what `top.py` refuses by name is an image and a legacy
`.doc`/`.xls`/`.ppt`. `dto-fill-via-web-lite` carries copies of `page.py` and
`schema.py` and no `holes.py` — the want list is the thing it does not build.

## Install

This repository **is** the plugin, for both hosts: `.claude-plugin/plugin.json`
and `.mcp.json` for Claude Code, `plugin.json` and `mcp.json` for an Agent
Plugins v1 host. Each reads its own pair and steps over the other's — Hermes
skips `.claude-plugin/` by name, Claude Code never looks at the root
`plugin.json`. One `skills/` directory and one `launch-mcp` serve both.

### Claude Code

```
/plugin marketplace add <marketplace-listing-this-repo>
/plugin install migration-factory-plugin@<that-marketplace>
```

The repository carries no `marketplace.json` of its own, so `/plugin install`
needs a marketplace that lists it. Without one, point a project's `.mcp.json`
at `launch-mcp` directly, as below.

There is no build step: `.mcp.json` runs `launch-mcp`, which runs the server
with `go run`. A Go toolchain is the only requirement, compilation is cached,
and there is no artifact to forget after a clone or rebuild after a pull.

Installing **copies** the plugin into `~/.claude/plugins/cache/`, so editing
this directory changes nothing until you refresh that copy:

```
/plugin marketplace update <that-marketplace>
/plugin install migration-factory-plugin@<that-marketplace>
```

While working on the server itself, skip the plugin machinery and point the
project at the launcher directly — a `.mcp.json` next to your project runs the
working tree, so a restart is enough to pick up an edit:

```json
{
  "mcpServers": {
    "migration-factory-plugin": {
      "command": "/path/to/migration-factory-plugin/launch-mcp",
      "env": {
        "SIM_BASE_URL": "${SIM_BASE_URL:-}"
      }
    }
  }
}
```

### Hermes Agent

The same directory is also a portable **Agent Plugins v1** package — `plugin.json`
and `mcp.json` at the root, beside the `.claude-plugin/` manifest Claude Code
reads and ignored by it — so Hermes installs it straight from the repository:

```bash
hermes plugins install corezoid/migration-factory-plugin --no-enable
hermes plugins enable migration-factory-plugin
```

A portable package is disabled on install; enabling one registers all six
skills and the MCP server. The skills are namespaced — `skills_list` shows them
under `agent-plugin-migration-factory-plugin-7ec05b64` — and the tools arrive
as `mcp__migration-factory-plugin__export_graph` and the rest.

Two things differ from Claude Code, both of them the host's doing:

- **the working directory.** Hermes starts a portable package's server inside
  the package rather than inside the user's project, so a relative path in a
  tool call would resolve against the plugin's own directory. `mcp.json` pins
  the cwd to `${PLUGIN_DATA}` instead, which is at least writable and at least
  the same place every time. Hand the tools absolute paths, or point
  `MIGRATION_FACTORY_PLUGIN_CWD` at the project — `launch-mcp` keeps a value
  that is already set rather than replacing it with its own;
- **credentials** — the environment does not reach the server at all. See
  below.

One prerequisite is easy to miss on a server install: `launch-mcp` runs the
server with `go run`, so the **Go toolchain has to exist wherever Hermes runs**.
On a desktop that is the machine you already build on; in a container it is the
image, which usually has no Go in it. Without one the skills still load and
every tool call fails, so check it where Hermes itself runs, not where you
cloned the repository.

## Credentials

The server is configured purely from the environment. The `.mcp.json` shipped
at the root of this repository spells `SIM_BASE_URL` as
`${SIM_BASE_URL:-https://mw.simulator.company/papi/1.0}` — the caller's value
when there is one, the dev gateway otherwise — and **pins no key at all**:
this repository is public, so both keys come from the environment the client
is started in, and an install that exports neither has no credentials.

| variable | what it is |
|---|---|
| `SIM_BASE_URL` | the gateway; a bare host is enough (`mw.simulator.company`). mf-api sets it from the session's workspace (`cfg.Gateway`) |
| `SIM_API_KEY` | a workspace API key, scoped to one workspace on one gateway |
| `SIM_WORKSPACE_ID` | the workspace that key is scoped to. Read for the one thing the key alone cannot say: a file upload names its workspace in the path, and a node's picture is a file. Unset is not fatal — the form behind the actor is asked instead |
| `SIM_GROUP_ID` | the Single Account group the session's people are in. Every record a run creates is shared to it (view and modify, that actor only): a record is off the layer, so the layer's own share never reaches it. It also names the account pairs `post_statement` bootstraps — `Bank Transaction 131107` — because a pair is workspace-level and the workspace outlives the session: without the suffix the next session of the same person asks for a pair it did not create and is refused 403. mf-api sets it from the session's group. Unset or not a positive integer — a run mf-api did not start — shares nothing, names nothing, and the server says so once on its stderr |
| `DEFAULT_SIM_API_KEY` | the key to fall back on when `SIM_API_KEY` is unset. Nothing sets it here — it is for a deployer who wants one built into its own environment |
| `FIRECRAWL_BASE_URL` | the Firecrawl instance `read_page` renders through; unset means the shared dev one, which is where mf-api renders website sources |
| `FIRECRAWL_API_KEY` | that instance's key |
| `DEFAULT_FIRECRAWL_API_KEY` | the key to fall back on when `FIRECRAWL_API_KEY` is unset; the same split as the Simulator pair, and likewise unset here |

The two pairs are read apart. The graph tools want the Simulator pair,
`read_page` wants the Firecrawl one, and neither missing half disables the
other — a `.mcp.json` with no Firecrawl key still exports and applies layers.

The two names per key are a split, not a duplicate. mf-api starts every
dto-fill project with the migration session's own workspace key in
`SIM_API_KEY`, the workspace's gateway in `SIM_BASE_URL`, the workspace in
`SIM_WORKSPACE_ID` and the session's group in `SIM_GROUP_ID` (`build.RunDTOFill`
→ the project's `env_vars`), and an inherited variable cannot displace one
`.mcp.json` spells out — so a deployer that wants a key built in puts it under
the fallback name, `DEFAULT_SIM_API_KEY`, leaving `SIM_API_KEY` free for the
session's own. The `.mcp.json` here fills in neither: only the gateway carries
a `:-` default, and a run that exports no key is refused by name when a tool is
called. The agent is told which gateway it is on by the command's
`gateway mw|sim` label (see the skill).

That is the whole list: the key carries the workspace, and everything a run
decides — which layer, which directory, which address, dry run or write — is
an argument of the tool call. To run against your own workspace, export it wherever your
shell starts Claude Code:

```bash
export SIM_BASE_URL=mw.simulator.company
export SIM_API_KEY=...
```

`SIM_API_KEY` is absent from that block on purpose: left out, it is inherited
from the shell — and from the project environment, which is how mf-api hands
over a session's key. Each entry that *is* listed carries a `:-` fallback,
also on purpose. A `${VAR}` that is unset and has no default is passed through
**literally**, and the server would be handed the string `${SIM_BASE_URL}` as
its gateway — it drops such a placeholder rather than dialling it, and falls
back to the gateway the client would have used anyway.

A missing variable surfaces when a tool is called, not at startup: the client
launches the server long before anyone asks it for anything.

### On a host that filters the environment (Hermes)

Hermes does not hand its own environment to an MCP server: it builds one from a
fixed safe list — `PATH`, `HOME`, `TMPDIR` and a few more — plus whatever the
package's `mcp.json` spells out, and nothing else. An exported `SIM_API_KEY`
therefore never arrives. Nor can it be written into `mcp.json`: that file ships
with the package, and the v1 specification says in as many words that its `env`
is visible package data and not a place for a credential.

So the keys go where the host *does* point — `$PLUGIN_DATA/.env`, which under
Hermes is one directory per package inside the profile's own home:

```
<HERMES_HOME>/plugin-data/agent-plugin-migration-factory-plugin-7ec05b64/.env
```

`HERMES_HOME` is `~/.hermes` for the default profile, `~/.hermes/profiles/<name>`
for a named one, and whatever the image mounts for a containerised install
(`/opt/data/profiles/<name>` is a common one) — `hermes doctor` prints the
resolved path. So, for the default profile:

```bash
cat > ~/.hermes/plugin-data/agent-plugin-migration-factory-plugin-7ec05b64/.env <<'EOF'
SIM_BASE_URL=mw.simulator.company
SIM_API_KEY=...
EOF
```

`MIGRATION_FACTORY_PLUGIN_ENV_FILE` names a file somewhere else, for an install
that keeps its credentials elsewhere. Either way it is read once at startup and
only ever **fills gaps**: a variable that already carries a value — mf-api's
key, say — is never displaced by one on disk. The format is the small one
everybody writes: `NAME=VALUE` a line, `#` comments, a tolerated `export`
prefix, optional quotes, and no expansion of anything, because a `$` in a
secret is part of the secret.

## The server

It is a separate Go module under [`server/`](server/), sharing no code with
this repository — see [server/README.md](server/README.md) for its layout, its
`make check` / `make live-export` targets and the standalone MCP config. The
whole repository can be copied out as a unit.


## Bank statements (bank-statement-to-jsonl)

    /bank-statement-to-jsonl <file> [out <path.jsonl>] [pages <a-b>]

Turns a statement into JSONL, one object per transaction:

```json
{"transaction_date":"2026-04-07","debit_sum":"0.00","credit_sum":"302.50","currency":"RON","description":"Incasare Instant …"}
```

`transaction_time` appears only when that row prints a time. Amounts are
strings — dot decimal, two places, unsigned, `"0.00"` on the empty side — so
nothing downstream re-floats them. `currency` is always present: a three-letter
code, or `XXX` (ISO 4217's own "no currency") when the statement never names
one, so *unknown* stays distinguishable from *assumed*.

The skill never reads the statement into the run. It samples one page with
`scripts/probe.py`, which clusters the right edges of numeric words into
columns and proposes a `ColumnSpec`; the agent confirms the bands and writes a
thin parser — a spec and one `run()` call — on top of `scripts/statement_lib.py`.

**Why geometry rather than text.** The text layer keeps every character and
throws away the column, which is the only thing that says what a number means.
`01/04/2026 Pachet IZI 29.00` is a debit or a credit and the line does not say.
Worse, the wrong reading is silent: `… HAPPYCOLOR 178,480.22 178,480.22` prints
the amount and the running balance side by side, so "take the last number"
reads the balance on every row and still looks plausible.

**Two gates, because the first cannot see the failures that matter.**
`scripts/validate.py` checks the shape of the first rows, and then checks the
sums against what the statement prints about itself — `Total Rulaje`,
`RULAJ TOTAL CONT`, or Unicredit's totals with row counts. A parser that drops
half the pages or reads the balance column still produces ten flawless opening
rows; only the arithmetic catches it.

| script | what it does |
|---|---|
| `scripts/probe.py` | one page + a bounded sample → column clusters, formats, the document's own totals, a proposed spec |
| `scripts/statement_lib.py` | `ColumnSpec`, row clustering, banding, both number grammars, streaming JSONL, reconciliation |
| `scripts/validate.py` | the two gates over a finished JSONL |
| `templates/parse_pdf.py` | what the agent copies for a PDF |
| `templates/parse_tabular.py` | the same for xlsx/csv — bands become column indices |

Unlike its siblings this skill needs third-party packages: `pdfplumber` for
PDFs and `openpyxl` for `.xlsx`. Both fail with the install line rather than a
traceback. Legacy `.xls` has no reader here and the skill says so instead of
half-working.


## Loading a statement end to end (bank-statement-to-dto)

    /bank-statement-to-dto <file> layer_id <uuid> [account_id <name>]

One statement says two kinds of thing and they need two readers. The header
names a bank, a client and a period — a handful of facts for the layer. The
rows are the money, and there can be forty thousand of them.

So the run splits at once: a second agent parses the whole file to
`bank_statement_transactions.jsonl` with `bank-statement-to-jsonl`, while the
main run reads page one only. They meet at the end, where `post_statement`
records the rows on the client the main run resolved.

**Why the parser starts first.** It is the long pole and nothing else waits on
it. Started before the export, it costs the run nothing; started after the ops
are applied, it costs the run its whole duration.

**Why the client is resolved against the form, not the canvas.** The layer
shows one placeholder per type. Whether *this* client already exists is a
question only `find_records` answers, and the placeholder may already hold a
different client — in which case the answer is a record of its own beside it.
Overwriting the occupant would delete a real client to make room for another.

**Why the actor id comes from the apply.** `apply_graph` stamps the uuid it
resolved onto every op and lists created records in `result.json`. That uuid is
what the transactions are posted against. An id remembered from a search is how
a statement lands on a stranger's accounts — the one error here that no later
ops file undoes.

The run does not post when the parser failed its reconciliation: a JSONL that
did not reconcile is a ledger with rows missing or doubled, and posting it is
worse than not posting.
