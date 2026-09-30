---
name: dto-fill-loop
description: Repeats dto-fill against one company's layer, pass after pass, for as long as a pass creates an actor, fills a hole or posts a transaction — and stops the moment a pass does none of the three, with nothing left queued. Handed several documents for that one layer at once, reads and routes them in parallel — one delegate_task per document — and only serializes the actual apply_graph writes, one file at a time, because that is the one step that cannot safely race. Also unpacks an archive or clones a GitHub/git URL into queueable documents first, and — handed a website address instead of a document — switches to looping dto-fill-via-web the same way, by its own filled/created counts. The calling agent never runs a pass itself; it only reads back the one machine-parseable line each subagent ends with and decides what to launch next. Use when the user wants dto-fill or dto-fill-via-web run to a fixed point rather than once, on one document, an archive, a repo, several files, or a site — "прогоняй, пока не перестанет находить", "запускай итерациями до нуля", "разбей документы на агентов и прогони параллельно", "распакуй архив и залей", "стяни репозиторий и прогони", "loop dto-fill until nothing new", "run these documents in parallel until it stops finding things", "loop dto-fill-via-web on this site".
---

# dto-fill-loop — one round, read the counts, decide, repeat

`dto-fill` fills a graph from one source in one pass and reports what that pass did. This skill is the layer
above it, not a bigger version of it: it never reads `graph.values.yaml`, never writes an op, never calls
`export_graph`, `find_records`, `apply_graph` or `post_statement` itself. Every one of those calls happens
inside a subagent this skill launches with `delegate_task`; this skill only ever reads back the line(s) those
subagents printed and decides what to launch next.

One run is always one company, one `layer_id`, one `dir` — never several at once. Handed a single document, one
subagent does the whole pass, exactly as `dto-fill` alone would. Handed several documents for that same company,
reading and routing them is parallel — one subagent per document — but writing them is not: every write goes
through exactly one `apply_graph` call at a time, because that is the one step two parallel passes on the same
target cannot safely race.

## The call

    /dto-fill-loop <file> [<file2> ...] layer_id <uuid> [gateway mw|sim] [dir <path>] [max_iterations <n>]

`layer_id` is required, same as in `dto-fill` — ask for it when it is not given, never guess it. `<file>`(s) are
whatever's queued to start; more can be handed over later in the same run the same way, and they join the same
queue (see "The queue" below).

`dir` is where this skill stops being casual about defaults. `dto-fill` is happy to default `dir` call by call
because a single run only ever calls it once; this skill calls `dto-fill`'s machinery many times over the same
run, each in its own subagent that may not share a default with the last one, and the whole scheme below — a
pass reading an earlier pass's files — only works if the path is fixed once and never re-defaulted. Resolve it
before round 1, print it in the first line of the report, and never let a later round re-derive it.

`max_iterations` defaults to **20**, counted in rounds. It is a safety cap, not a target — see "The runaway
cases" below.

## The queue

Every document this skill is handed — at the start, or added while it's still running — goes on one queue, for
the one `layer_id`/`dir` this run resolved once, at the start. There is no splitting or grouping decision to
make: nothing this skill processes ever targets a different company than the one this run is about.

## What the input actually is

Not everything handed to this skill is already a queueable document. Work out which of these four it is before
touching the queue:

- **A document file** (or several) — join the queue as-is; nothing here changes.
- **An archive** (`.zip`, `.tar`, `.tar.gz`/`.tgz`, `.tar.bz2`, `.7z`, `.rar`, …) — unpack it first (below), then
  every file that comes out joins the queue exactly like a document handed over directly.
- **A GitHub (or other git) URL** — clone the whole thing first (below), then its files join the queue the same
  way. This is specifically a URL shaped like a repository — `github.com/<owner>/<repo>`, the same with `.git`
  or `git@github.com:<owner>/<repo>.git`, the same shapes on gitlab.com or bitbucket.org.
- **A website address** (anything else that resolves to a page, not a file) — this is not a queue item at all.
  Nothing below in this file runs for it; see "Looping a website instead of documents" for the whole different
  shape that takes. This includes a page that merely *lives* on a git host without being a repository to clone
  — `<owner>.github.io`, a GitHub Pages project site, a wiki page: read as a website, never cloned.

A run mixing a document queue and a website for the *same* `layer_id` is still two tracks against one graph, and
they share the one-writer-at-a-time rule everything else here rests on: alternate rounds between the two rather
than letting a document round's Phase 2 and a website round's apply happen at the same time. This skill does not
build that scheduler on its own initiative — say so and ask, if a run is ever handed both a document backlog and
a site for the same company.

## Turning an archive or a repo into queue items

Neither unpacking nor cloning touches the graph, so this is the one piece of this skill's own work that could
run directly rather than through a leaf subagent — but delegate it anyway, for the same reason everything else
here is delegated: a large archive or repository's own file listing is exactly the kind of output that fills
this skill's own context for no benefit, when all it actually needs back is a short list of paths.

**Nothing a subagent needs here is refused for lack of a tool** — `unzip`, `7z`, `git` itself, whatever a step
below turns out to call for and does not find already on the host. Install it the way this plugin's own skills
always do: name the exact line, and on a host whose PYTHONPATH points at a writable directory (the Hermes
gateway's `/opt/data/py-deps`), install there rather than into the container, which does not survive a restart.
Both goal texts below say "install what this needs" rather than repeating that mechanism each time — this
paragraph is what that phrase means.

    delegate_task(
      goal: "<the paragraph below>",
      context: "source <archive-path-or-url>, dir <dir>",
      role: "leaf",
      allowed_toolsets: ("terminal", "file"),
    )

This is the one call in this skill that does not need the plugin's own MCP toolset — it never touches
`export_graph`/`apply_graph`, only the filesystem and a shell.

For an archive, the `goal` text:

    Unpack <archive-path> into <dir>/sources/ (create it if it does not exist). Install what this needs and
    does not have.

    List every extracted file whose extension is one of: pdf, docx, xlsx, pptx, doc, xls, ppt, odt, ods, odp,
    rtf, csv, md, txt, json, eml, msg, html, png, jpg, jpeg — dto-fill's own supported list. A nested archive
    inside this one is unpacked the same way, one level; a second archive nested inside that one is not —
    report it instead of recursing further.

    End your reply with exactly:
    FILES: <path1>|<path2>|...
    SKIPPED: <n>

For a git URL, the `goal` text:

    Clone <url> into <dir>/sources/<repo-name>/ — shallow, depth 1, no history needed, the whole repository.
    Install what this needs and does not have; only a clone that still fails after that is private, a bad URL,
    or a network problem, not a missing tool.

    If the clone fails for one of those real reasons, do not report an empty file list, which reads as "nothing
    here" rather than "could not look." End your reply with exactly:
    CLONE_FAILED: <reason>
    and stop there.

    Otherwise, list every file whose extension is one of: pdf, docx, xlsx, pptx, doc, xls, ppt, odt, ods, odp,
    rtf, csv, md, txt, json, eml, msg, html, png, jpg, jpeg. Skip .git/ entirely, and skip node_modules, vendor,
    dist, build and .venv — a repository's own source and dependencies are never documents.

    End your reply with exactly:
    FILES: <path1>|<path2>|...
    SKIPPED: <n>

Whatever `FILES:` lists is the queue this run starts from — fed into "The queue" above exactly the way documents
handed over directly would be. `SKIPPED:`'s count goes in the first line of the report, next to the resolved
`dir`; it is not itemized, the same way `dto-fill-via-web`'s own report counts what was out of the web's reach
rather than listing it.

**No `FILES:`/`SKIPPED:` line, or a `CLONE_FAILED:` line, is read the same way a missing `RESULT:` is read
everywhere else in this skill: stop and report it, never as an empty queue that quietly converges.** An empty
queue and a clone that never happened print identically if this distinction is skipped, and only one of them
means the run is actually done.

## Looping a website instead of documents

A website address does not join the queue — it replaces the whole document-processing machinery below with a
single-pass loop around `dto-fill-via-web` instead of `dto-fill`. Nothing about the queue, or the two-phase split
below, applies here: one site, one `layer_id`, one subagent per round, exactly like "a round with one document"
further down, just running a different skill.

    delegate_task(
      goal: "<the paragraph below>",
      context: "layer_id <uuid>, gateway <mw|sim>, dir <dir>, url <site-address>",
      role: "leaf",
      allowed_toolsets: ("<the toolset this plugin's MCP tools are registered under>",),
    )

The `goal` text:

    Follow <skill-dir>/../dto-fill-via-web/SKILL.md on <site-address>, layer_id <uuid>, gateway <mw|sim>. Read
    the want list fresh this round — export_graph and holes.py again, even if an earlier round already ran
    them — the graph has changed since then and a stale want list is the same mistake this skill already avoids
    for dto-fill itself.

    Do the pass exactly as dto-fill-via-web/SKILL.md says otherwise. Then, as the last line of your reply and
    nothing after it, print exactly:
    RESULT: filled=<N> created=<N>
    counting only what THIS round did — nodes or fields filled, records created — never a cumulative total.

Two counts here, not three: this skill never posts a transaction, so there is nothing to sum for that column.
Positive is `filled > 0 OR created > 0`, the same OR-not-weighted-sum rule as the document side.

**Why a second round can find anything a thorough first one did not.** `dto-fill-via-web`'s own Pass 4 runs on a
page budget (about ten pages, stopping early on its own heuristics) against the want list *that round* started
with. A round that filled the twin's identity and contacts first may never have reached the catalogue or
locations pages within its budget — a fresh want list next round is shorter, and the budget that round spends
can reach pages the first round's budget never got to. This is not the same page read twice; it is a genuinely
incomplete first crawl, budgeted on purpose, given another pass.

The same two runaway shapes apply here, in this skill's own terms: a non-deterministic `ref:` (rung 4's own rule,
word for word) makes a round report `created > 0` forever on the same subject, indistinguishable in one round's
report from real progress. `max_iterations` is the same cap, for the same reason — a healthy site converges in a
few rounds, because there are only so many pages worth reading at all.

## What counts as positive

A pass is positive when at least one of three counts is greater than zero: actors created, holes filled,
transactions posted — the OR of them, not a weighted sum. All three at zero, with nothing left queued, is the
only stop condition that means "done"; every other way this run ends is reported as something other than done
(see Report).

## Fixed paths

| file                                    | who writes it                                                    | who reads it next                                              |
|-------------------------------------------|---------------------------------------------------------------------|--------------------------------------------------------------------|
| `<dir>/graph.ops.yaml`                     | every apply, overwritten — `dto-fill`'s own convention               | the next round's reads, before anything is called new               |
| `<dir>/pending/<doc>.ops.yaml`             | one per document, by that document's own extract-phase subagent, this round only | this round's apply-phase subagent; deleted once applied |
| `<dir>/pending/<doc>.transactions.jsonl`   | one per document with anything for Pass 6, this round only          | this round's apply-phase subagent; deleted once posted |
| `<dir>/graph.transactions.jsonl`           | the apply-phase subagent, appended, never overwritten                | the next round's reads                                              |
| `<dir>/result.json`                        | `dto-fill`'s own apply/post calls — cumulative and deduplicated already | you, once, for the final report                              |
| `<dir>/dto-fill-loop.log`                  | you, one line per round                                              | you, and whoever reads the final report                             |

`pending/` is scratch: it exists only between a round's extract phase and its own apply phase, and is empty
again once that round's apply subagent finishes. `graph.transactions.jsonl`'s name, and `pending/`'s layout, are
this skill's own invention — `dto-fill`'s Pass 6 never standardized either because a single run never needed to
hand them to anything.

## A round with one document — unchanged

If the queue holds exactly one document this round, skip the two-phase split below entirely: one
`delegate_task`, one subagent does export, read, route, apply and post itself, exactly as `dto-fill` alone
always worked. The split into an extract phase and an apply phase exists only to make *several* documents safe
to read at once; it buys nothing when there is only one, and costs an extra subagent call for no reason.

The single-document goal text:

    Follow <skill-dir>/../dto-fill/SKILL.md on <file>, layer_id <uuid>, gateway <mw|sim>, dir <dir>.

    Before anything else, call export_graph(layer, dir=<dir>) yourself — do not reuse graph.values.yaml or
    types.schema.yaml already sitting in <dir>, even though dto-fill's own default is to reuse an export
    that's already there. The graph changed since they were written.

    If <dir>/graph.ops.yaml exists, read it first: it is every op an earlier round already applied, so a
    record it already created is FOUND, not new.

    If <dir>/graph.transactions.jsonl exists, read it first too: extend it with only the rows this pass adds
    before calling post_statement, keeping the same file name.

    Do the pass exactly as dto-fill/SKILL.md says otherwise. Then, as the last line of your reply and nothing
    after it, print exactly:
    RESULT: actors_created=<N> holes_filled=<N> transactions_posted=<N>
    counting only what THIS pass did, never dir/result.json's cumulative totals.

## A round with several documents — extract in parallel, apply in order

When the queue holds more than one document, split the round into two phases. Cap how many documents one round
takes on — read off round 1's own wall-clock cost before fixing that number, the same way you would fix a wait
timeout; there is no honest default to hard-code in advance.

The queue can hold more documents than one round's cap allows. Only that many enter Phase 1 this round; the rest
stay queued for the next round, which re-exports fresh — Phase 1 always reads whatever the previous round's
Phase 2 just applied — and gets its own Phase 2 apply. **Total subagents for the whole run is documents
processed plus rounds taken, never documents alone**: one apply subagent per round, not one per document, and
more than one round whenever the queue outgrows a single round's cap.

**Phase 1 — extract, one `delegate_task` per document, all issued together:**

    delegate_task(
      goal: "<the paragraph below, one call per document, <doc> and <slug> distinct per call>",
      context: "layer_id <uuid>, gateway <mw|sim>, dir <dir>, document <doc>",
      role: "leaf",
      allowed_toolsets: ("<the toolset this plugin's MCP tools are registered under>",),
    )

The `goal` text:

    Follow <skill-dir>/../dto-fill/SKILL.md's Passes 0-5 on <doc> — extract, type, route, verify — for layer_id
    <uuid>, gateway <mw|sim>, dir <dir>. This is a parallel-extract pass: other documents queued for the same
    run are being routed by other subagents right now, against the same starting snapshot, and nothing you
    decide is applied by you.

    Call export_graph(layer, dir=<dir>) yourself first — do not reuse an export already sitting there. Read
    <dir>/graph.ops.yaml if it exists: it is every op an earlier round already applied, so a record it shows is
    FOUND, not new.

    Route every subject exactly as dto-fill/SKILL.md's Pass 4 says, with one exception: never use case 3 (fill
    and rename an empty placeholder because this is "the first subject of a type"). Go straight to case 4
    instead — every new subject gets its own type:/ref: record, even the first one of its type. Cases 1, 2 and
    5 are unchanged. Case 3 is unsafe here only because another subagent's document, read from the same
    snapshot at the same time, could just as easily be "the first subject" of that same type, and neither of
    you can see the other's choice before both of you write.

    Write your ops to <dir>/pending/<slug>.ops.yaml — do not call apply_graph. If this document has anything
    for Pass 6, write its rows to <dir>/pending/<slug>.transactions.jsonl the same way — do not call
    post_statement.

    End your reply with exactly:
    EXTRACTED: ops=<dir>/pending/<slug>.ops.yaml subjects=<N>

**Phase 2 — apply, one `delegate_task` for the whole run, after every Phase 1 call this round has returned:**

    delegate_task(
      goal: "<the paragraph below, with this round's own list of paths substituted in>",
      context: "layer_id <uuid>, gateway <mw|sim>, dir <dir>",
      role: "leaf",
      allowed_toolsets: ("<the toolset this plugin's MCP tools are registered under>",),
    )

The `goal` text:

    Apply this round's pending work, one file at a time, in exactly this order — never merge them into one
    file first:
    <the ops= paths from this round's EXTRACTED: lines, in the order those calls were made>

    For each: apply_graph(ops: "<path>", write: true), read back what it applied before moving to the next. A
    later file touching the same record an earlier one in this list already touched is expected — that is
    exactly what apply_graph's own read-by-ref-before-create reconciles, not a conflict to pre-merge or skip.

    Then, in the same order, for each pending transactions file that exists: append its rows to
    <dir>/graph.transactions.jsonl, then post_statement as dto-fill/SKILL.md's Pass 6 says (dry_run first on
    anything unfamiliar).

    Once every file in both lists has been applied or posted, delete <dir>/pending/* this round produced —
    <dir>/graph.ops.yaml, <dir>/result.json and <dir>/graph.transactions.jsonl already hold the durable record
    of what happened.

    Then, as the last line of your reply and nothing after it, print exactly:
    RESULT: actors_created=<N> holes_filled=<N> transactions_posted=<N>
    summed across every file just applied or posted this round, never dir/result.json's cumulative totals.

Phase 2 never starts until every Phase 1 call for this round has returned, and Phase 2 itself is exactly one
subagent applying strictly one file at a time — nothing here ever calls `apply_graph` from two places at once.

`<skill-dir>` is this skill's own directory, from `skill_view` — do not guess it, the same rule `dto-fill`
itself follows.

**Confirm `allowed_toolsets` before the first real run.** A subagent missing this plugin's toolset can still
write a fluent report and an `EXTRACTED:`/`RESULT:` line while never having called the tool at all, and nothing
here catches a hallucinated count except reading the actual files afterward.

**Confirm the mechanics of `delegate_task` itself on the first round, too** — nothing here assumes more than
that calling it blocks until its subagent finishes and hands back its final text as the tool result, and that
several such calls issued in the same turn are genuinely dispatched together. If a real round instead runs
noticeably one call at a time, the per-pass mechanics are unaffected — it is just slower than intended — but say
so in the report rather than silently taking credit for concurrency that did not happen.

## Reading a round back

1. **No line matching the expected pattern** (`EXTRACTED: ops=...` for a Phase 1 call, or
   `RESULT: actors_created=\d+ holes_filled=\d+ transactions_posted=\d+` for a single-document round or a
   Phase 2 call) → stop the run. It did not fail silently — it failed to follow the one instruction this whole
   skill depends on, and guessing from its prose report is how a stalled run ends up looking converged.
2. **A round's `RESULT:` has all three numbers 0, and nothing left queued** → stop. This is the normal ending,
   not an error.
3. **Any number > 0, or documents still queued** → append the round's numbers to `dto-fill-loop.log`, check
   `max_iterations`, and — if not capped — run another round.

## No recursive delegation

Every `delegate_task` call in this skill — Phase 1, Phase 2, or the single-document round — is made by the same
top-level agent running this file, never by one subagent calling `delegate_task` in turn. The queue is
bookkeeping this skill keeps about itself, not a standing sub-process. Nothing in Hermes' own documentation
confirms that a `role: "leaf"` subagent is allowed to delegate further, and a flat delegation tree sidesteps the
question rather than guessing at it.

## Why export_graph runs on every single extract

`dto-fill` reuses an export sitting in `dir` on the reasonable assumption that nothing else changed it between
the export and the read. This skill breaks that assumption on purpose, round over round — the entire reason to
run another round at all is that the previous one's applies changed the graph. Every extract subagent in a round
reads the same fresh snapshot (nothing is being applied while Phase 1 is running, by construction — Phase 2 only
starts once Phase 1 is entirely done), so redundant re-exports across a round's documents cost a little work but
never see a torn or half-written state.

## The runaway cases

Two failure modes here do not correct themselves, and neither is about a slow run — both make it report progress
forever without making any.

**A `ref:` that is not deterministic.** `dto-fill` is explicit that a `ref:` has to be built from the subject's
own identity, so the same document produces the same string on every run. If it isn't, the same subject reads
NOT FOUND every round and gets a new record every round, and nothing in a single pass's report tells that apart
from genuine progress.

**Case 3 used anyway.** If an extract subagent ignores the instruction and fills an empty placeholder for "the
first subject of a type" despite running in a parallel round, two documents racing for the same placeholder
silently overwrite each other under one node — not a duplicate, a lost record. This is what makes the
instruction in Phase 1's `goal` text load-bearing rather than a nicety.

Both are what `max_iterations` is actually for. This run is meant to end by a round reporting all zeros with
nothing left queued, and a healthy one usually will, in single digits. Hitting the cap is not "run it again with
a higher number": stop, read `dto-fill-loop.log` and `graph.ops.yaml` across rounds, and check for a subject
with a changing `ref:` or a placeholder that got overwritten before running it again.

## Report

- The resolved `dir`, first, before round 1.
- The per-round table from `dto-fill-loop.log` (a round may cover more than one document), and how it ended —
  **converged**, **capped**, or **aborted**.
- The final totals quoted from `result.json` and the last round's own Pass 6 report — never summed from the
  per-round log by hand.
- How many rounds the whole run took, how many used the two-phase split versus the single-document path, and
  whether concurrency inside a round was noticeably real or effectively sequential (see "Confirm the mechanics"
  above).
- If the input was an archive or a repo, the `SKIPPED:` count from that prep call, next to the resolved `dir`.
- If this run looped a website instead of a document queue, report it the same way — resolved `dir` and site
  address first, the per-round `filled`/`created` table, how it ended — just with two counts instead of three,
  and no Phase 1/Phase 2 split to mention.
