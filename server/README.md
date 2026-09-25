# migration-factory-plugin-mcp

An MCP server with five tools: three over a Digital Twin layer in Simulator,
and one over the other end of the job — reading the source.

| tool | what it does |
|---|---|
| `export_graph(layer, dir?)` | writes `graph.values.yaml`, `graph.ids.json` and `types.schema.yaml` into `dir` (default: the current directory) |
| `apply_graph(ops, write?, layer?, partial?, keep_export?)` | plans a `graph.ops.yaml` against the live layer and returns the diff; `write: true` applies it and refreshes the export beside it |
| `find_records(type, values, fields?, dir?)` | answers, per value, whether a record of that type already carries that identity — the check to run before creating one |
| `read_page(url)` | renders one web page to Markdown through Firecrawl — one address, one page, no crawl |

`apply_graph` plans and writes in the same call when `write: true` — the diff
comes back either way, so a caller that means to import does not need a
separate dry run. Omitting `write` plans only. Ops are overwrites, so replaying
an unchanged file writes nothing and reports `already applied`.

An op is addressed either by `at:` (a node on the layer) or by `type:` + `ref:`
(a record of that type, read by its business key and created when there is
none). A created record is an actor of its form and is not placed on the
canvas, so it is absent from the next export — the stamped uuid and the ref are
what find it. `ref:` is required precisely because it, not the file, is what
stops a second run of the same document creating a second record.

`find_records` is what makes that second address safe to reach for. The layer
holds the nodes somebody placed on it; the form holds every record of the type,
including the ones no graph shows. Asking the form first turns "the canvas has
no free slot" into "no record for this counterparty exists yet", which are
different questions with different answers.

It probes each value against the fields the type marks as identity keys, then
the actor's title, and reports the verdict with the ref to write to. No field
name is special to this server — a caller whose source identifies subjects by
something the form does not mark names those fields instead. It returns a
verdict rather than a listing on purpose: the version that wrote the form's
records to a file was searched wrong by its caller, which read "no match" out
of its own bug and created fifteen records unchecked.

A write also keeps `result.json` beside the ops file — the running tally of
what this document has done to the graph:

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
actor. The lists are the record; the three counts are derived from them, so a
file trimmed by hand still adds up.

### Reading a page

`read_page` is the odd one out: it touches no layer and needs no Simulator
key. It exists because a source is not always a file. A website source reaches
the agent as the Markdown of **one** address — mf-api renders it with the same
provider and hands that over as the document — and when the address was a site
root, the front page is all of it. Whether the rest of the site is worth
reading is then a judgement the agent makes with the layer in front of it, and
this is what it makes it with: one call, one page, the same rendering as the
document it already has, so a page read here and a page handed over compare
like for like.

There is no crawl here and there will not be one. Reading a site means
deciding which pages matter and asking for those, which is the decision that
keeps a twin's `[company]` node filled from an "about" page instead of from
four hundred product listings.

Main content only: navigation, ads and cookie banners are dropped by the
provider. A page it could not read at all — a login wall, a bot check, a dead
link — comes back as an error whose message says whether asking again is worth
anything, and a page that renders to no text is an error too rather than an
empty answer. "The reader could not see it" and "the site says nothing about
it" license different next moves, and only the second one licenses writing
that down.

This is a self-contained Go module. It shares no code with the repository it
currently sits in: `internal/graph` holds the export, apply and listing logic,
`internal/simulator` is a cut-down client for the REST routes the three graph
tools use — read a layer's nodes and edges, read an actor by id or by ref,
create one, write one back, read a form, list a form's actors — and
`internal/firecrawl` is a one-route client for the page reader. Copy the
`migration-factory-plugin` directory anywhere and it still builds.

## Configuration

Everything comes from the environment. There is no config file to find — an
MCP client starts its servers with a working directory of its own choosing,
usually nowhere near any checkout.

| variable | what it is |
|---|---|
| `SIM_BASE_URL` | the gateway. A bare host is enough: `mw.simulator.company` becomes `https://mw.simulator.company/papi/1.0` |
| `SIM_API_KEY` | a workspace API key from account.corezoid.com, scoped to one workspace on one gateway |
| `DEFAULT_SIM_API_KEY` | the key used when `SIM_API_KEY` is unset — see below |
| `SIM_WORKSPACE_ID` | the workspace the key is scoped to, read only for a picture upload, which names its workspace in the path. Unset asks the actor's form |
| `SIM_GROUP_ID` | the group every record `apply_graph` creates is shared to, view and modify on that actor only — a record is off the layer, so the layer's share never reaches it. Optional: unset or not a positive integer shares nothing, said once on stderr |
| `FIRECRAWL_BASE_URL` | the Firecrawl v2 instance `read_page` renders through. Optional: unset means the shared dev instance, which is the one mf-api renders website sources with |
| `FIRECRAWL_API_KEY` | that instance's key |
| `DEFAULT_FIRECRAWL_API_KEY` | the key used when `FIRECRAWL_API_KEY` is unset — same reasoning as the Simulator one, and likewise not filled in by `../.mcp.json` |

Two pairs, and that is the whole list. Each pair says *where* the server
talks, and the two are **loaded apart**: the graph tools ask for the Simulator
pair, `read_page` asks for the Firecrawl one. A server with no Firecrawl key
still exports and applies layers, and a server with no Simulator key still
reads a page — refusing every tool because the half the caller is not using
was left unset is how a working install looks broken. Nothing else is
configurable:

- there is no workspace to set — the key carries it. The one route that wants
  an `accId`, the actor listing behind `find_records`, is given the workspace
  the form itself reports;
- there is no default layer, directory or write flag — every one of those is a
  per-call decision, so it is an argument of the tool call, where the caller
  can see it and change it;
- timeouts and read concurrency are fixed: a Simulator request is capped at
  60s, a scrape at 2 minutes, a tool call at 3, 5 or 10 minutes, and reads run
  8 at a time. If those are wrong for a gateway they are wrong for everyone on
  it, and the fix belongs in the code.

The key and the gateway have to name the same environment — a key issued for
one gateway is refused by another with a 401.

`DEFAULT_SIM_API_KEY` exists because the key is not always the installer's to
pick: a deployer can build one into the environment it launches the server in,
so a plugin nobody configured still works, while a caller that has a better
key — mf-api, which starts each cc-api project with the migration session's
*own* workspace key — exports `SIM_API_KEY`, and that wins. The two are
separate variables rather than one with a default precisely so that can
happen: a value spelled out in `.mcp.json` is not something an inherited
environment can displace. `../.mcp.json` in this repository pins neither key —
it is public — so both are read from the environment. A literal `${...}`,
which is what an unset placeholder without a `:-` fallback expands to, counts
as unset here and falls through to the default.

A missing variable is reported when a tool is called, not at startup: the
client launches the server eagerly, long before anyone asks it for anything,
and a server that exits at launch shows up as a broken plugin rather than as a
missing setting.

## Running it

As a Claude Code plugin there is nothing to do: `../.mcp.json` runs
`../launch-mcp`, which runs this module with `go run` and forwards the two
variables from your shell. Compilation is cached, so it costs a fraction of a
second per session and the code that runs is always the code in the tree.

The launcher is a script and not a `go run` line in the config because of two
things `go run` cannot do on its own: it takes the module from the *current*
directory (`-C` says otherwise, but then leaves the server itself sitting
there, so the real working directory is handed over in `MIGRATION_FACTORY_PLUGIN_CWD` and
relative paths in a tool call resolve against it), and it needs a toolchain on
a `PATH` the client may not have.

In any other MCP client, point it at the launcher the same way:

```json
{
  "mcpServers": {
    "migration-factory-plugin": {
      "command": "/path/to/migration-factory-plugin/launch-mcp",
      "env": {
        "SIM_BASE_URL": "mw.simulator.company",
        "SIM_API_KEY": "...",
        "FIRECRAWL_API_KEY": "..."
      }
    }
  }
}
```

`make build` produces a standalone binary in `../bin` for a host with no Go
toolchain; nothing else needs it.

## Development

```bash
make check          # gofmt, go vet, go test — no network
make mcp-handshake  # initialize + tools/list over stdio, no network
```

The live targets reach a real gateway and skip without `SIM_LIVE=1`. They read
a `.env` beside this Makefile when there is one (it is gitignored):

```bash
make live-export LAYER=<uuid> OUT_DIR=./out
make live-apply OPS=./out/graph.ops.yaml            # dry run: prints the diff
make live-apply OPS=./out/graph.ops.yaml WRITE=1    # writes it
```

## Protocol

MCP over stdio, hand-rolled: newline-delimited JSON-RPC 2.0 with four methods
worth answering (`initialize`, `ping`, `tools/list`, `tools/call`). A server
this small is not worth a dependency. Anything on stdout is protocol; logs go
to stderr.

A tool that fails reports through its result with `isError: true`, not as a
JSON-RPC error — the model is meant to read the message and correct itself.


## post_statement

Records the JSONL a statement parser produced onto an actor.

    post_statement(account_id: "Bank Statement", actor_id: "<uuid>", path: "statement.jsonl")

`account_id` is the account-name category **by name**, not an id: the pair
route resolves names, and the workspace's name register has no lookup by id —
3000 names and only a name query. The name is created if the workspace lacks it.

**Why both sides get used.** A (name, currency) pair on an actor is two
accounts with their own ids, one `incomeType: "debit"` and one `"credit"`, and
a transaction carries no direction of its own — the side is decided by which id
it is posted to. So `debit_sum` lands on the debit id and `credit_sum` on the
credit id. The card then totals the pair as credit minus debit, which is the
net movement, while both turnovers remain readable separately and comparable
with the two columns the statement itself prints. Nothing has to be signed for
that to work, and a zero column is not posted at all.

**Why a pair is bootstrapped for every currency.** `POST /accounts/pair` creates
or returns, so there is nothing to check first — and it is also what grants
access to the pair. Attaching the account alone does not, and a transaction
without that access is refused 403. Resolution is cached per currency for the
run, which is a cost saving and never a correctness one.

**Why each row carries its own date.** The platform stamps a transaction with
the moment of the call and keeps the real one in `originalDate` — so a run that
does not send that field loads a whole statement as if every row of it happened
on import day. The row's `transaction_date`, plus its `transaction_time` where
the statement printed one, is what goes there. The unit is **milliseconds**:
the gateway's own UI multiplies by 1000 on the way in and divides by 1000 on
the way out, and a seconds value lands the row in 1970.

The rows print no offset, so the zone is a decision: it is read as UTC unless
`timezone` names an IANA one, e.g. `Europe/Kyiv` for a statement whose issuing
bank is known. UTC invents nothing, but it is up to a few hours off the times
the statement prints, and a midnight row can read as the day before for a
viewer west of it. A zone that does not resolve is refused rather than quietly
ignored, and a row whose date is missing or is not `yyyy-mm-dd` stops the run
before anything is written — a transaction confidently stamped with today is
afterwards indistinguishable from one that really happened today.

**Idempotency.** Each transaction's ref is derived from the row it came from
plus the side it lands on, so re-running the same file posts nothing twice and
an interrupted run can simply be repeated. The platform refuses a repeated ref
with `400 Not unique ref`; that is counted as a duplicate, not a failure.

Run `dry_run: true` first on anything unfamiliar — it resolves the pairs and
totals the file per currency without writing, which is the cheapest way to check
the turnovers against the statement before any of it lands.
