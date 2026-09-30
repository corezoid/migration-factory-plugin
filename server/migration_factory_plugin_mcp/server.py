"""MCP tool wiring: the five tools this plugin ships, over stdio via the
official `mcp` SDK's low-level Server. A tool that fails reports through the
result (isError: true), not a protocol error — the SDK's own `call_tool`
decorator already converts a raised exception into that shape, so a handler
here just raises on failure and returns text on success, mirroring the Go
original's `(text, error)` return convention.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import config
from .envfile import load_env_file
from .firecrawl.images import Image
from .graph import records as records_mod
from .graph import result as result_mod
from .graph.export import ExportOptions, export_layer
from .graph.records import FindRecordsOptions
from .graph.schema import TypeSet, load_types_schema
from .ledger import BatchResult, Options, Result, StatementJob, post, post_batch

MAX_IMAGES_LISTED = 60

# Fixed per-tool timeouts matching the Go original are not enforced here
# directly — the MCP client is expected to apply its own call timeout; the
# HTTP clients underneath (Simulator: 60s, Firecrawl: 120s) already bound the
# slowest single network call.


def _fmt_duration(seconds: float) -> str:
    ms_total = round(seconds * 1000)
    if ms_total < 1000:
        return f"{ms_total}ms"
    if ms_total < 60000:
        return f"{ms_total / 1000:g}s"
    minutes, rem_ms = divmod(ms_total, 60000)
    return f"{minutes}m{rem_ms / 1000:g}s"


def _append_warnings(lines: list, warnings: list) -> None:
    for w in warnings:
        lines.append(f"warning: {w}")


def sim_schema() -> dict:
    return config.sim_schema()


def tool_defs() -> "list[types.Tool]":
    return [
        types.Tool(
            name="export_graph",
            description=(
                "Export a Digital Twin layer into the three files the dto-fill skill reads: "
                "graph.values.yaml (the tree with every stored value), graph.ids.json (path -> uuid) and "
                "types.schema.yaml (the field dictionary). They land in `dir`, which defaults to the current "
                "directory — no per-layer subdirectory. Run this before planning an ops file, and note that "
                "apply_graph refreshes the same three files after a successful write."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sim": sim_schema(),
                    "layer": {"type": "string", "description": "Layer UUID to export."},
                    "dir": {
                        "type": "string",
                        "description": "Directory the three files are written into, created if missing. "
                        "Defaults to the current directory.",
                    },
                },
                "required": ["layer"],
            },
        ),
        types.Tool(
            name="apply_graph",
            description=(
                "Plan a graph.ops.yaml against the live layer and, when write is true, apply it. "
                "A write returns the same full diff a plan does, so one call with write: true is the normal "
                "way to apply — planning first and applying second only costs a round trip. Omitting write "
                "plans and writes nothing, for when the caller genuinely wants to look before touching the "
                "layer. The type dictionary is read from the export sitting next to the ops file, so "
                "keep graph.ops.yaml in the same directory as graph.ids.json. Every op is an overwrite, so an "
                "unchanged file replays as 'already applied'. A successful write re-exports the layer into that "
                "same directory.\n\n"
                "A write also keeps result.json beside the ops file: how many placeholder holes were filled, "
                "actors updated and records created, with the uuid of each. It is cumulative and deduplicated "
                "by uuid, so applying the same file again does not count the same node twice — the numbers are "
                "the totals for the document, not for the last call.\n\n"
                "Applying also stamps each op with the uuid of the node it resolved to, writing it back into "
                "the ops file before anything is sent. From then on the op is addressed by that id and its "
                "`at:` path is ignored, so the file still replays after a `rename:` has moved the paths.\n\n"
                "An op can also carry `picture:` — the absolute address of an image on the source, "
                "written only when the source ties that image to this subject (the alt text, the "
                "caption, the JSON-LD key). The image is COPIED: it is fetched, checked and uploaded into the "
                "workspace's storage, because an actor's picture is a path there and never a URL. A "
                "node that already carries a picture keeps it. An image that cannot be taken — "
                "refused, too small to be a picture of anything, or already bound to another node in "
                "this run — is reported and the rest of the op still applies.\n\n"
                "An op that carries `type:` (a slug from types.schema.yaml) and `ref:` (a business key) "
                "owns a record instead of addressing a node: the ref is looked up first, and the record is "
                "created when there is none. It is created as an actor of its form and is NOT placed on the "
                "layer, so it does not appear in the refreshed graph.values.yaml — the stamped id and the "
                "ref are the handles on it. It is shared to the session's group when the server was given "
                "one; a share that failed is reported and the record stands. This is the only thing "
                "apply_graph does that another ops file cannot undo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sim": sim_schema(),
                    "ops": {"type": "string", "description": "Path to the ops file to plan or apply."},
                    "write": {
                        "type": "boolean",
                        "description": "false (default) plans and writes nothing. true applies the ops and "
                        "returns the plan alongside the result — pass it whenever the ask was to get "
                        "the source into the graph, rather than to preview what would land there.",
                    },
                    "layer": {
                        "type": "string",
                        "description": "Layer UUID. Optional: the ops file's own `layer:` key is used, and they must agree.",
                    },
                    "partial": {
                        "type": "boolean",
                        "description": "Apply the ops that validate even when others do not. Off by default — "
                        "half an import is worse than none.",
                    },
                    "keep_export": {
                        "type": "boolean",
                        "description": "Leave the export beside the ops file untouched after a write, instead "
                        "of refreshing it.",
                    },
                },
                "required": ["ops"],
            },
        ),
        types.Tool(
            name="find_records",
            description=(
                "Answer, for each value, whether a record of this type already carries that "
                "identity — the check to run BEFORE writing a record, every time. A layer shows the "
                "nodes somebody placed on it, usually one empty placeholder per type per branch; the "
                "form behind the type holds every record, including the ones earlier runs created off "
                "the canvas and the ones a person typed into Simulator. Skipping this check is how the "
                "same counterparty ends up in the register three times under three different refs.\n\n"
                "Pass the identity of each subject about to be written — several in one call, one per "
                "record. By default a value is probed against every field the type marks as an identity "
                "key, then against the actor's title, and the first probe that matches wins. When the "
                "source identifies its subjects by something the type does not mark — an account number, "
                "a document number, an email — name those fields in `fields` after reading the type in "
                "types.schema.yaml. The title probe is what catches a record whose identity field was "
                "never filled, which is most of what a source like that leaves behind. It matches the "
                "whole title, case and spacing aside; a record whose title merely contains the value is "
                "listed as similar, and similar is not found.\n\n"
                "FOUND means write to that record — with `type:` plus ITS `ref:`, the one reported here, "
                "not one you derived. A found record with an empty ref was made by hand and no ref "
                "lookup reaches it: put its id in the op's `id:`. NOT FOUND is the only answer that "
                "licenses a create. A value whose probe failed is unknown, not absent — do not create "
                "on it.\n\n"
                "The slug is resolved against types.schema.yaml in `dir`, so export the layer first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sim": sim_schema(),
                    "type": {
                        "type": "string",
                        "description": "Type slug to look in — the name in [square brackets] in graph.values.yaml.",
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The identities to check, one per record about to be written.",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Fields to probe, in order, instead of the type's identity keys — the "
                        "names as they appear in types.schema.yaml, plus \"title\" for the actor's own "
                        "title. Naming any field drops the title probe unless \"title\" is among them, so "
                        "a check can be narrowed to exactly the keys that identify a record here.",
                    },
                    "dir": {
                        "type": "string",
                        "description": "Directory holding the export the slug is resolved against. Defaults to the current directory.",
                    },
                },
                "required": ["type", "values"],
            },
        ),
        types.Tool(
            name="read_page",
            description=(
                "Read one web page and return it as Markdown — the same rendering a website "
                "source arrives in, so a page fetched here and a page handed over as a file read alike. "
                "One address, one page: nothing is followed and no site is crawled, so reading a site "
                "means calling this once per page you decided was worth reading.\n\n"
                "Use it when the source IS a URL, and when a website source's front page (`source_scope "
                "site`) points at something the layer wants and lacks — an \"about\", \"contacts\", "
                "\"team\" or \"services\" page for a company twin. Do not walk a site for its own sake, "
                "and say in `gaps` which pages you read and which you left.\n\n"
                "The answer also carries an inventory of the pictures on the page — each with the alt "
                "text or caption the page gives it — and the page's own og:image, which on a front "
                "page is almost always the logo, stated as data rather than inferred from a banner. "
                "That text is what ties a photograph to the person, product or office it shows, and "
                "it is the only thing that licenses putting the image on a node. It is on by default "
                "because a picture nobody was shown is one no node ever gets; images: false turns it "
                "off for a read that is only about what the page says.\n\n"
                "It returns the page's main content: navigation, ads and cookie banners are dropped, so a "
                "page that is mostly chrome comes back thin, and a page behind a login or a bot wall comes "
                "back refused rather than half-read. The refusal says whether reading it again is worth "
                "anything. A page that renders to no text at all is an error too, not an empty answer — "
                "there is nothing in it to route, and nothing should be written from it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The page to read: a full http or https address, no #fragment. "
                        "A site root reads as its front page, nothing deeper.",
                    },
                    "images": {
                        "type": "boolean",
                        "description": "Whether to list the pictures on the page. On by default: a "
                        "picture nobody was told about is one no node ever gets, and the list is "
                        "cheap next to the page itself. Pass false for a read that is only ever "
                        "about what the page says.",
                    },
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="post_statement",
            description=(
                "Record a batch of transactions on an actor's accounts, from a .jsonl of rows — "
                "the step after a source has been turned into that shape and, where the source prints its "
                "own totals, checked against them. A parsed bank statement is the common case and the one "
                "named below, but nothing here is statement-specific: any source that reduces to dated "
                "rows of money in and money out — a POS export, a mobile-money report, a manually typed "
                "ledger — posts the same way.\n\n"
                "Every row is posted onto the (account-name, currency) pair it belongs to. A pair on an "
                "actor is two accounts with their own ids, one debit and one credit, and a transaction "
                "carries no direction of its own — the side is the id it lands on.\n\n"
                "Pass EITHER a single file (`actor_id` + `path`) OR a batch (`statements:` — a list of "
                "{actor_id, path, currency_name?, timezone?, ref_prefix?}, one entry per actor). Use the "
                "batch form for anything with more than a handful of actors: it is one tool call instead of "
                "one per actor, it posts through the exact same idempotency-ref algorithm as the single-file "
                "form (there is exactly one implementation of it, in this server), and a failure on one "
                "actor's file does not stop the rest of the batch from posting — do not loop this tool "
                "yourself, and do not reimplement statement posting outside it; a hand-rolled re-derivation "
                "of the ref scheme will not match this one and duplicate detection silently stops working "
                "for that data.\n\n"
                "A file's rows do not have to name one fixed actor. When a source mixes the transactions of "
                "several actors — a card-processor export, a combined ledger — the bank-statement-to-jsonl "
                "skill (or whatever produced the .jsonl) says so by giving each row a `uniq_actor_field_value`: "
                "the value of whatever field the source itself uses to tell its actors apart (an IBAN, a card "
                "number, a tax id, an email). Pass `actor_field` (the field's name on the actor's type, as "
                "`types.schema.yaml` names it) and `actor_type` (that type's slug) INSTEAD OF `actor_id`, and "
                "this tool resolves each row's own actor from its own `uniq_actor_field_value` by filtering "
                "the Simulator for that type's actors with that field equal to that value — this needs `dir` "
                "(or a `statements[].path` in a directory) pointing at the export holding `types.schema.yaml`. "
                "More than one actor answers the filter is not an error: the most recently created one is "
                "taken, on the theory that an older record sharing the same key is a stale duplicate. A row "
                "with no `uniq_actor_field_value` still falls back to `actor_id` when one was also given, so a "
                "file can mix a handful of unattributed rows into an otherwise single-actor run; a row with "
                "neither is skipped and warned about, never guessed. `actor_id` alone (the common case: one "
                "statement, one owner) needs neither `actor_field` nor `actor_type` and behaves exactly as "
                "before.\n\n"
                "A file may hold several currencies; each gets its own pair per actor, created on first sight "
                "and reused for the rest of the run — one many-actor file still bootstraps one pair per "
                "currency, shared across every actor it resolves to. Each newly bootstrapped pair is shared "
                "with the session's group when the server was given one; a pair just created can briefly "
                "refuse its own creator sharing it (an eventual-consistency window on Simulator's side, not "
                "a real permissions gap), so a refused share is retried three times (5s, 10s, 15s) before "
                "it is believed — and if it still fails, the run stops there rather than posting rows onto "
                "a pair the caller's own group cannot see.\n\n"
                "A row whose own `currency` is empty falls back to `currency_name` when the caller passed "
                "one, and to `XXX` (ISO 4217's \"no currency\") otherwise.\n\n"
                "Each transaction is dated by the row it came from: `transaction_date` (with "
                "`transaction_time` where the row prints one), read as UTC unless `timezone` names the "
                "source's own zone. A row whose date is missing or is not `yyyy-mm-dd` stops that file's "
                "run.\n\n"
                "Idempotent by construction: each transaction's ref is derived from the row it came "
                "from, so re-running the same file posts nothing twice.\n\n"
                "Run it with `dry_run` first on anything unfamiliar. What it posted is added to the run's "
                "result.json beside what apply_graph wrote."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sim": sim_schema(),
                    "account_name": {
                        "type": "string",
                        "description": "The account-name category to record under, BY NAME — e.g. "
                        "\"Bank Statement\". Created if the workspace does not have it.",
                    },
                    "actor_id": {
                        "type": "string",
                        "description": "Actor UUID the accounts hang on — the single-file, single-actor form. "
                        "Omit when passing `statements`, or when passing `actor_field`+`actor_type` instead "
                        "(a row with no `uniq_actor_field_value` still falls back to this one, if given).",
                    },
                    "actor_field": {
                        "type": "string",
                        "description": "The many-actor form, instead of `actor_id`: the name of the field (as "
                        "`types.schema.yaml` names it under `actor_type`) that a row's `uniq_actor_field_value` "
                        "is filtered against to find that row's own actor — e.g. \"iban\", \"card\", \"email\", "
                        "\"inn\". Needs `actor_type` alongside it, and `dir` pointing at the export directory "
                        "that holds `types.schema.yaml`.",
                    },
                    "actor_type": {
                        "type": "string",
                        "description": "The type slug (from `types.schema.yaml`) that `actor_field` belongs "
                        "to — the many-actor form, alongside `actor_field`.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to the .jsonl, one transaction per line — the single-file form. "
                        "Omit when passing `statements`.",
                    },
                    "statements": {
                        "type": "array",
                        "description": "The batch form: one entry per job, each {path, actor_id?, "
                        "actor_field?, actor_type?, currency_name?, timezone?, ref_prefix?} — either "
                        "`actor_id` (single-actor file) or `actor_field`+`actor_type` (many-actor file, "
                        "resolved per row from `uniq_actor_field_value`) is required. Use this instead of "
                        "looping the tool for more than a handful of files.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "actor_id": {"type": "string"},
                                "actor_field": {"type": "string"},
                                "actor_type": {"type": "string"},
                                "path": {"type": "string"},
                                "currency_name": {"type": "string"},
                                "timezone": {"type": "string"},
                                "ref_prefix": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    },
                    "currency_name": {
                        "type": "string",
                        "description": "Currency for a row whose own `currency` is empty — the single-file form.",
                    },
                    "dir": {
                        "type": "string",
                        "description": "Where result.json is kept, and, when `actor_field` is in use, the "
                        "directory `types.schema.yaml` is read from. Defaults to the directory `path` is in "
                        "(single-file form) or is read per job from each `statements[].path` (batch form).",
                    },
                    "ref_prefix": {
                        "type": "string",
                        "description": "Namespaces the idempotency refs (default \"stmt\") — the single-file form.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA zone the rows' wall clock is read in — the single-file form.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Resolve the pairs and total the file(s) per currency, posting nothing.",
                    },
                },
                "required": ["account_name"],
            },
        ),
    ]


# ------------------------------------------------------------------ export_graph

def run_export(args: dict) -> str:
    layer_id = str(args.get("layer") or "").strip()
    if not layer_id:
        raise ValueError("no layer to export: pass `layer` with the layer UUID")
    cfg = config.load_config(config.SimOverride.from_dict(args.get("sim")))
    dir_path = config.resolve_path(str(args.get("dir") or "") or ".")

    sim = cfg.client()
    start = time.monotonic()
    res = export_layer(sim, ExportOptions(layer_id=layer_id, dir=dir_path))
    elapsed = time.monotonic() - start

    lines = [f"exported layer {layer_id} into {dir_path} in {_fmt_duration(elapsed)}"]
    lines.append(
        f"  {res.nodes} nodes, {res.edges} edges, {res.types} types, {res.nodes_with_values} nodes with values"
    )
    for path in (res.values_path, res.ids_path, res.types_path):
        try:
            size = os.path.getsize(path)
            lines.append(f"  {size:>8} bytes  {os.path.basename(path)}")
        except OSError:
            pass
    _append_warnings(lines, res.warnings)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ find_records

def _render_similar(records: list) -> str:
    return ", ".join(f'"{r.title}"' for r in records)


def _render_fields(fields: dict) -> str:
    return "  ".join(f"{k}={fields[k]}" for k in sorted(fields.keys()))


def render_find_result(res) -> str:
    lines = [f"{res.type_slug} (form {res.form_id}) — probed {', '.join(res.probes)}", ""]
    for c in res.checks:
        if c.err is not None:
            lines.append(f"  {c.value:<44} UNKNOWN — {c.err}")
        elif not c.found():
            lines.append(f"  {c.value:<44} not found")
            if c.similar:
                lines.append(f"      {'':<38} similar titles, not this record: {_render_similar(c.similar)}")
        else:
            lines.append(f"  {c.value:<44} FOUND by {c.matched_by}")
            for r in c.records:
                addr = f"ref: {r.ref}" if r.ref else "NO REF — address it by id:"
                lines.append(f"      {r.title:<38} {addr}  [{r.id}]")
                if r.fields:
                    lines.append(f"      {'':<38} {_render_fields(r.fields)}")

    found, missing, failed = res.tally()
    lines.append("")
    tally_line = f"{found} found, {missing} not found"
    if failed:
        tally_line += f", {failed} could not be checked"
    lines.append(tally_line)
    if found:
        lines.append(
            "Write to a found record with `type:` plus the ref reported above — not a ref of "
            "your own, or you create a second copy of it."
        )
    if missing:
        lines.append("Only the not-found values license a create.")
    if failed:
        lines.append(
            "A value that could not be checked is unknown, not absent: leave it unwritten "
            "and say so, or check it again."
        )
    _append_warnings(lines, res.warnings)
    return "\n".join(lines) + "\n"


def run_find_records(args: dict) -> str:
    type_slug = str(args.get("type") or "").strip()
    if not type_slug:
        raise ValueError("no type to check: pass `type` with a slug from graph.values.yaml")
    values = list(args.get("values") or [])
    cfg = config.load_config(config.SimOverride.from_dict(args.get("sim")))
    dir_path = config.resolve_path(str(args.get("dir") or "") or ".")

    type_set, found = load_types_schema(dir_path)
    if not found:
        raise ValueError(f"no types.schema.yaml in {dir_path} — export the layer first")

    sim = cfg.client()
    opts = FindRecordsOptions(type=type_slug, values=values, fields=list(args.get("fields") or []), dir=dir_path)
    res = records_mod.find_records(sim, type_set, opts)
    return render_find_result(res)


# ------------------------------------------------------------------ read_page

def _write_images(lines: list, asked: bool, images: "list[Image]") -> None:
    if not asked:
        return
    if not images:
        lines.append("pictures on this page: none")
        return
    lines.append(
        f"pictures on this page — {len(images)}, with what the page says each one is. Only the "
        "text beside an address ties that image to a subject; an image with nothing said about "
        "it identifies nobody:"
    )
    shown = 0
    for img in images:
        if not img.url or img.url.startswith("data:"):
            continue
        if shown == MAX_IMAGES_LISTED:
            lines.append(
                f"  … and {len(images) - shown} more, not listed — this page is a gallery; read it for its "
                "text and take a picture from a page that is about one subject"
            )
            break
        line = "  " + img.url
        if img.alt.strip():
            line += "   — " + " ".join(img.alt.split())
        lines.append(line)
        shown += 1


def run_read_page(args: dict) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ValueError("no page to read: pass `url` with the address of the page")
    fc_cfg = config.load_firecrawl_config()
    images_arg = args.get("images")
    with_images = images_arg is None or bool(images_arg)

    client = fc_cfg.client()
    start = time.monotonic()
    page = client.scrape_with_images(url) if with_images else client.scrape(url)
    elapsed = time.monotonic() - start

    text = page.markdown.strip()
    if not text:
        raise ValueError(
            f"{url} was read but rendered to no text — nothing in it to route, and that is "
            "a page this reader could not see, not a page with nothing on it"
        )

    lines = [f"read {url} — {len(text.encode('utf-8'))} bytes of markdown in {_fmt_duration(elapsed)}"]
    if page.metadata.title.strip():
        lines.append(f"title: {page.metadata.title.strip()}")
    addr = page.address()
    if addr and addr != url:
        lines.append(f"redirected to: {addr}")
    if page.metadata.og_image.strip():
        lines.append(
            f"og:image (the page's own picture of itself, usually the logo): {page.metadata.og_image.strip()}"
        )
    if page.metadata.favicon.strip() and with_images:
        lines.append(
            "favicon (the site's own icon — its owner's logo when nothing else on "
            f"the page is one): {page.metadata.favicon.strip()}"
        )
    _write_images(lines, with_images, page.images)
    lines.append("")
    lines.append(text)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ post_statement

def _render_currency_lines(lines: list, currencies: list) -> None:
    for c in currencies:
        lines.append("")
        lines.append(f"{c.currency}  {c.rows} rows")
        lines.append(f"  debit  {c.debit:14.2f}   -> {c.debit_id}")
        lines.append(f"  credit {c.credit:14.2f}   -> {c.credit_id}")
        lines.append(f"  net    {c.credit - c.debit:14.2f}   (credit - debit, what the card shows)")


def _render_actor_count(lines: list, res: Result) -> None:
    if len(res.actors) > 1:
        lines.append(f"  across {len(res.actors)} actors, resolved per row from uniq_actor_field_value")


def _render_post_statement(res: Result, account_name: str, dry_run: bool, elapsed: float) -> str:
    what = "would post" if dry_run else "posted"
    line = f"{account_name}: {res.records} rows, {what} {res.posted} transactions"
    if res.duplicate:
        line += f", {res.duplicate} already there"
    if res.skipped:
        line += f", {res.skipped} failed"
    line += f" ({_fmt_duration(elapsed)})"
    lines = [line]
    _render_actor_count(lines, res)

    _render_currency_lines(lines, res.currencies)
    if len(res.currencies) > 1:
        lines.append("")
        lines.append("More than one currency: each has its own pair, and only the")
        lines.append("per-currency turnovers above are comparable with the source.")
    if dry_run:
        lines.append("")
        lines.append("Nothing was written. Check these turnovers against the totals the")
        lines.append("source prints about itself, if it prints any, then run again without dry_run.")
    _append_warnings(lines, res.warnings)
    return "\n".join(lines) + "\n"


def _render_post_batch(batch: BatchResult, dry_run: bool, elapsed: float) -> str:
    what = "would post" if dry_run else "posted"
    ok = sum(1 for o in batch.outcomes if o.error is None)
    failed_outright = len(batch.outcomes) - ok

    lines = [f"{batch.account_name}: {len(batch.outcomes)} statement(s) ({_fmt_duration(elapsed)})"]
    summary = f"  {ok} {what}"
    if failed_outright:
        summary += f", {failed_outright} failed outright"
    lines.append(summary)
    lines.append("")

    for o in batch.outcomes:
        if o.error is not None:
            lines.append(f"  {o.job.label()}  ERROR — {o.error}")
            continue
        r = o.result
        line = f"  {o.job.label()}  {r.records} rows, {what} {r.posted}"
        if r.duplicate:
            line += f", {r.duplicate} already there"
        if r.skipped:
            line += f", {r.skipped} failed"
        if len(r.actors) > 1:
            line += f", across {len(r.actors)} actors"
        lines.append(line)

    totals = batch.totals()
    lines.append("")
    total_line = f"total: {totals.records} rows, {what} {totals.posted} transactions"
    if totals.duplicate:
        total_line += f", {totals.duplicate} already there"
    if totals.skipped:
        total_line += f", {totals.skipped} failed"
    lines.append(total_line)
    _render_actor_count(lines, totals)
    for c in totals.currencies:
        lines.append(f"  {c.currency}  debit {c.debit:.2f}  credit {c.credit:.2f}  net {c.credit - c.debit:.2f}")

    if dry_run:
        lines.append("")
        lines.append("Nothing was written. Run again without dry_run to post.")
    _append_warnings(lines, totals.warnings)
    return "\n".join(lines) + "\n"


def _record_one_statement(
    dir_arg: str, ref_prefix: str, account_name: str, actor_label: str, path: str, res: Result, dry_run: bool,
) -> str:
    if dry_run or res.posted + res.duplicate == 0:
        return ""
    dir_path = config.resolve_path(dir_arg) if dir_arg else os.path.dirname(path)
    result_path = os.path.join(dir_path, result_mod.RESULT_FILE_NAME)
    try:
        tally = result_mod.load_result(result_path)
        rec = result_mod.StatementRecord(
            ref=result_mod.statement_ref(ref_prefix, actor_label, account_name, path),
            file=os.path.basename(path),
            actor_id=actor_label,
            account=account_name,
            transactions=res.posted + res.duplicate,
            failed=res.skipped,
            turnover=[
                result_mod.StatementTurnover(currency=c.currency, debit=c.debit, credit=c.credit)
                for c in res.currencies
            ],
            actors=sorted(res.actors) if len(res.actors) > 1 else [],
        )
        tally.add_statement(rec)
        result_mod.write_result(result_path, tally)
    except OSError as exc:
        return f"note: the posting is not in result.json — {exc}"
    return ""


def _load_types_for(dir_path: str, cache: dict) -> Optional[TypeSet]:
    """Loads types.schema.yaml once per directory a run touches — the many-
    actor form reads it once (or once per distinct directory in a batch) no
    matter how many rows resolve an actor against it."""
    if dir_path not in cache:
        found_types, found = load_types_schema(dir_path)
        cache[dir_path] = found_types if found else None
    return cache[dir_path]


def run_post_statement(args: dict) -> str:
    account_name = str(args.get("account_name") or "").strip()
    if not account_name:
        raise ValueError(
            "no account name: pass `account_name` with the account-name category to record under, "
            'e.g. "Bank Statement"'
        )
    cfg = config.load_config(config.SimOverride.from_dict(args.get("sim")))
    if not cfg.workspace_id:
        raise ValueError(
            "no workspace: an account pair is workspace-level, so pass `sim.workspace_id` with the "
            "call or set SIM_WORKSPACE_ID in the MCP server's env"
        )
    sim = cfg.client()
    dry_run = bool(args.get("dry_run") or False)
    dir_arg = str(args.get("dir") or "").strip()
    types_cache: dict = {}

    statements_arg = args.get("statements")
    if statements_arg:
        jobs = []
        for item in statements_arg:
            actor_id = str(item.get("actor_id") or "").strip()
            actor_field = str(item.get("actor_field") or "").strip()
            actor_type = str(item.get("actor_type") or "").strip()
            path = str(item.get("path") or "").strip()
            if not path:
                raise ValueError("every entry in `statements` needs `path`")
            if not actor_id and not (actor_field and actor_type):
                raise ValueError(
                    "every entry in `statements` needs either `actor_id`, or both `actor_field` "
                    "and `actor_type`"
                )
            jobs.append(
                StatementJob(
                    actor_id=actor_id,
                    actor_field=actor_field,
                    actor_type=actor_type,
                    path=config.resolve_path(path),
                    currency_name=str(item.get("currency_name") or ""),
                    timezone=str(item.get("timezone") or ""),
                    ref_prefix=str(item.get("ref_prefix") or ""),
                )
            )
        types_dir = config.resolve_path(dir_arg) if dir_arg else os.path.dirname(jobs[0].path)
        job_types = _load_types_for(types_dir, types_cache)
        start = time.monotonic()
        batch = post_batch(
            sim, jobs, account_name=account_name, workspace_id=cfg.workspace_id,
            group_id=cfg.group_id, dry_run=dry_run, types=job_types,
        )
        elapsed = time.monotonic() - start
        for o in batch.outcomes:
            if o.result is not None:
                _record_one_statement(
                    dir_arg, o.job.ref_prefix, account_name, o.job.label(), o.job.path, o.result, dry_run
                )
        return _render_post_batch(batch, dry_run, elapsed)

    actor_id = str(args.get("actor_id") or "").strip()
    actor_field = str(args.get("actor_field") or "").strip()
    actor_type = str(args.get("actor_type") or "").strip()
    if not actor_id and not (actor_field and actor_type):
        raise ValueError(
            "no actor: pass `actor_id` with the UUID of the actor the accounts belong to, "
            "`actor_field`+`actor_type` so each row resolves its own actor from its own "
            "uniq_actor_field_value, or use `statements` for more than one file"
        )
    path_arg = str(args.get("path") or "").strip()
    if not path_arg:
        raise ValueError("no input: pass `path` with the .jsonl of transactions to post")
    path = config.resolve_path(path_arg)
    ref_prefix = str(args.get("ref_prefix") or "")

    single_types = None
    if actor_field:
        types_dir = config.resolve_path(dir_arg) if dir_arg else os.path.dirname(path)
        single_types = _load_types_for(types_dir, types_cache)

    opts = Options(
        account_name=account_name, actor_id=actor_id, actor_field=actor_field, actor_type=actor_type,
        path=path, workspace_id=cfg.workspace_id, group_id=cfg.group_id,
        ref_prefix=ref_prefix, timezone=str(args.get("timezone") or ""),
        default_currency=str(args.get("currency_name") or ""), dry_run=dry_run,
    )
    start = time.monotonic()
    res = post(sim, opts, single_types)
    elapsed = time.monotonic() - start

    actor_label = actor_id or f"field:{actor_type}.{actor_field}"
    text = _render_post_statement(res, account_name, dry_run, elapsed)
    warn = _record_one_statement(dir_arg, ref_prefix, account_name, actor_label, path, res, dry_run)
    if warn:
        text += "\n" + warn
    return text


# ------------------------------------------------------------------ apply_graph

def run_apply(args: dict) -> str:
    # Deferred import: apply.py is being written by a sibling task; importing
    # it lazily here keeps this module loadable before that file lands, and
    # matches the same lazy-import pattern plan.py/create.py use to avoid a
    # circular dependency.
    from .graph import apply as apply_mod

    ops_arg = str(args.get("ops") or "").strip()
    if not ops_arg:
        raise ValueError("no ops file: pass `ops` with the path to a graph.ops.yaml")
    ops_path = config.resolve_path(ops_arg)
    if not os.path.isfile(ops_path):
        raise ValueError(f"ops file {ops_path}: not found")

    cfg = config.load_config(config.SimOverride.from_dict(args.get("sim")))
    write = bool(args.get("write") or False)

    start = time.monotonic()
    try:
        res = apply_mod.apply_ops_file(
            cfg.client(), ops_path,
            apply_mod.ApplyOptions(
                layer_id=str(args.get("layer") or ""),
                dry_run=not write,
                partial=bool(args.get("partial") or False),
                keep_export=bool(args.get("keep_export") or False),
                workspace_id=cfg.workspace_id,
                group_id=cfg.group_id,
            ),
        )
    except apply_mod.ApplyError as exc:
        # The plan is the useful half even when the run failed — render it
        # alongside the error, the same as a successful call would.
        header = f"{'applying' if write else 'planning'} {ops_path}\n\n"
        plan_text = exc.result.plan.render() + "\n" if exc.result.plan is not None else ""
        raise ValueError(f"{header}{plan_text}\n{exc}") from exc
    elapsed = time.monotonic() - start
    return apply_mod.render_apply_result(res, ops_path, write, elapsed)


# ------------------------------------------------------------------ dispatch

SERVER = Server(config.SERVER_NAME, version=config.SERVER_VERSION)


@SERVER.list_tools()
async def _list_tools() -> "list[types.Tool]":
    return tool_defs()


@SERVER.call_tool()
async def _call_tool(name: str, arguments: "dict[str, Any]") -> "list[types.TextContent]":
    handlers = {
        "export_graph": run_export,
        "apply_graph": run_apply,
        "find_records": run_find_records,
        "read_page": run_read_page,
        "post_statement": run_post_statement,
    }
    handler = handlers.get(name)
    if handler is None:
        raise ValueError(f"unknown tool: {name}")
    text = handler(arguments or {})
    return [types.TextContent(type="text", text=text)]


async def run_stdio() -> None:
    load_env_file()
    async with stdio_server() as (read_stream, write_stream):
        await SERVER.run(read_stream, write_stream, SERVER.create_initialization_options())
