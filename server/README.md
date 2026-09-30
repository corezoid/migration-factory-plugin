# migration-factory-plugin-mcp

The MCP server behind the `migration-factory-plugin`: five tools over stdio —
`export_graph`, `apply_graph`, `find_records`, `read_page` and
`post_statement`. Originally a Go module; rewritten in Python on the official
[`mcp` SDK](https://github.com/modelcontextprotocol/python-sdk) to add a
batch mode to `post_statement` (see below) without changing any of the five
tools' contracts — a skill written against the Go version needs no changes.

## Tools

| Tool | Arguments |
|---|---|
| `export_graph` | `layer` (required), `dir` |
| `apply_graph` | `ops` (required), `write`, `layer`, `partial`, `keep_export` |
| `find_records` | `type` (required), `values` (required), `fields`, `dir` |
| `read_page` | `url` (required), `images` |
| `post_statement` | `account_name` (required), then either `actor_id`+`path` (one actor), `actor_field`+`actor_type`+`path` (many actors, resolved per row), or `statements` (a batch — see below) |

Every tool also takes an optional `sim` object (`base_url`, `api_key`,
`workspace_id`, `group_id`) — the Simulator workspace THAT call writes to,
when the caller names one. Pass it unchanged on every call; leaving it out on
one call sends that call to the server's own workspace (its environment)
instead.

## post_statement: single file vs. batch

```
post_statement(account_name: "Bank Statement", actor_id: "<uuid>", path: "<file>.jsonl")
```
posts one file. For more than a handful of actors — a monthly usage export
with 200 accounts, say — pass a batch instead of looping the tool call
yourself:
```
post_statement(
  account_name: "State Changes",
  statements: [
    {actor_id: "<uuid-1>", path: "<file-1>.jsonl"},
    {actor_id: "<uuid-2>", path: "<file-2>.jsonl", currency_name: "UAH"},
    ...
  ],
)
```
Both forms post through the exact same `ledger.post()` — the same
idempotency-ref algorithm (`ref_for`, in `migration_factory_plugin_mcp/ledger.py`),
the same account-pair bootstrap, the same retried-then-fatal group-share (see
below). The batch form is not a different implementation; it is a loop over
the single-file one, run server-side, with results aggregated per actor plus
a grand total, and a consolidated `result.json` update.

A newly bootstrapped pair is shared with `sim.group_id`'s group so more than
the run's own key can see what it posted. A pair just created can briefly
403 its own creator sharing it — confirmed live: the exact same share call,
replayed minutes later with no code change, succeeded, which is an
eventual-consistency window on Simulator's side rather than a real
permissions gap. So a refused share is retried three times (5s, 10s, 15s)
before it is believed, and if it still fails after that, `post()` raises and
the run stops — rows nobody but this run's key could see are worse than
rows not posted at all.

A file's rows do not have to name one fixed actor. When they don't — a
card-processor export, a combined ledger — the `.jsonl` gives each row a
`uniq_actor_field_value` (see `bank-statement-to-jsonl`) instead of the file
naming one actor:
```
post_statement(account_name: "Bank Statement", actor_field: "iban",
               actor_type: "client", path: "<file>.jsonl", dir: "<export dir>")
```
`actor_type` is a slug from that directory's `types.schema.yaml` and
`actor_field` a field name on it; each row resolves its own actor by filtering
Simulator's actors of that type for `actor_field` = the row's own
`uniq_actor_field_value`. More than one match is not an error — the most
recently created actor wins. A row with no `uniq_actor_field_value` still
falls back to `actor_id` when one was also given, so a file can mix a few
unattributed rows into an otherwise single-actor run. `actor_field` and
`actor_type` go together and either can be set per job in a `statements`
batch, in place of that job's `actor_id`.

**Do not reimplement statement posting outside this tool.** Idempotency
depends on `ref_for` producing byte-for-byte the same ref for the same row on
every run; a hand-rolled re-derivation of that hash (even one that looks
equivalent) will not match, and duplicate detection silently stops working
for that data. If a call needs more throughput than the batch form gives you,
that is a reason to improve this tool, not to call the Simulator API
directly.

## Configuration

| Variable | Purpose |
|---|---|
| `SIM_BASE_URL` | Simulator gateway. A bare host works — normalized to `https://<host>/papi/1.0`. Unset uses the client's own default. |
| `SIM_API_KEY` | Workspace API key. Required (here or via `sim.api_key` per call). |
| `DEFAULT_SIM_API_KEY` | Fallback for `SIM_API_KEY`, meant to be pinned by a deployer; a caller-supplied `SIM_API_KEY` always wins. |
| `SIM_WORKSPACE_ID` | Workspace (accId) file uploads go into. Needed by `apply_graph` (pictures) and `post_statement`. |
| `SIM_GROUP_ID` | Single Account group every created record / posted pair is shared to. Unset shares nothing (and says so once, in the log). |
| `FIRECRAWL_BASE_URL` | Page-reader instance. Unset uses the shared default. |
| `FIRECRAWL_API_KEY` | Required for `read_page` only — the other four tools work with no Firecrawl key. |
| `DEFAULT_FIRECRAWL_API_KEY` | Fallback for `FIRECRAWL_API_KEY`, same precedence rule as the Simulator pair. |
| `MIGRATION_FACTORY_PLUGIN_CWD` | Set by `launch-mcp`: the directory the MCP client actually started in, so a relative `dir`/`path`/`ops` in a tool call resolves against the user's project. |

On a host that filters the process environment (Hermes: a portable Agent
Plugins v1 host), credentials instead come from a `.env` file in
`PLUGIN_DATA` (the package's own writable directory) — see
`migration_factory_plugin_mcp/envfile.py`. The process environment always
wins; the file only fills a gap.

## Running it

Development:
```
make venv     # creates .venv, installs the package editable + pytest/ruff
make check    # format-check + lint + test
```
A client (Claude Code, Hermes) launches the server via `../launch-mcp`, which
finds a `python3` on the host and runs `python3 -m migration_factory_plugin_mcp`.
A host with no `pip`/network at runtime (Hermes) instead gets a vendored
wheel bundle under `../vendor/<platform>/`, rebuilt with `make vendor` and
committed the same way the Go version committed prebuilt binaries under
`bin/`.

## Development

- `make test` — the unit suite (no network).
- `make live-export` / `live-apply` / `live-find` / `live-read` / `live-post`
  — opt-in tests against a real Simulator/Firecrawl workspace, gated by
  `SIM_LIVE=1` and the usual `SIM_LAYER`/`SIM_OPS`/`FIRECRAWL_URL`-style env
  vars (see `tests/live/test_live.py`). Never run in CI.
- `make mcp-handshake` — a raw `initialize` + `tools/list` piped through
  `../launch-mcp`, no network, to confirm the stdio protocol itself works.

## Protocol

Standard MCP over stdio, served by the official `mcp` SDK's low-level
`Server` (not `FastMCP` — the five tools' JSON-Schema `inputSchema`s are
hand-written to match the original Go tool definitions exactly, and every
handler builds its own text response). A tool that fails reports through the
result (`isError: true`), never as a bare JSON-RPC protocol error — the model
is meant to read the message and correct itself, the same design the Go
version used.
