---
name: bank-statement-to-dto
description: Load a bank statement into a Digital Twin layer end to end — a parallel agent parses every row to JSONL while the main run reads only the header, settles which bank issued the statement and which client it was issued for, puts the bank on the layer as the company, resolves the client against the client form rather than against the canvas, then posts every transaction onto the client's accounts with post_statement. Statements of any size — the header is all the main run ever reads. Use when the user hands over a bank statement and asks to load it into the graph, the twin, the DTO or Simulator, to record its turnover on a client, or to do both. Triggers on "залей выписку в граф", "загрузи выписку в DTO", "выписку в граф и транзакции", "проведи выписку по клиенту", "заведи банк и клиента из выписки", "разнеси выписку по счетам", "load this bank statement into the twin", "import the statement into the graph and post its transactions", "book this statement against the client", "/bank-statement-to-dto".
---

# bank-statement-to-dto — the header builds the graph, the rows build the ledger

A bank statement says two different kinds of thing, and they are read by two
different readers. Its **header** names a bank and a client and a period: a
handful of facts that belong on the layer. Its **rows** are the money, and
there can be forty thousand of them. Reading the rows to find out who the
client is would be reading a phone book to learn the name on its cover.

So this run splits at the first step and never merges until the last. A second
agent starts parsing the whole file to JSONL while this one reads page one,
settles the bank and the client on the graph, and writes them. Only the final
posting needs both halves. **The main run never reads past the header** — that
is what makes the size of the statement stop mattering.

Two things here cannot be undone by another ops file, and they are the same two
as always: a record created for a client who already existed under a name you
did not check, and a transaction posted against the wrong actor. The first is
prevented by asking the register instead of the canvas; the second by taking
the actor id from the apply rather than from memory.

## The call

    /bank-statement-to-dto <file> layer_id <uuid> [account_id <name>] [gateway mw|sim]

- `<file>` — the statement, in the working directory: pdf, xlsx, csv, txt.
- `layer_id` — the layer to fill. Ask for it when you do not have it.
- `account_id` — the account-name category the transactions are recorded
  under. Defaults to `Bank Transaction`. It is a name, not an id.
- `gateway` — which Simulator deployment the layer lives on, `mw` or `sim`.

## Pass 0 — start the parser before anything else

Launch a second agent **as the first action of the run**, before the export,
before reading a line of the statement:

    Task(subagent_type: "general-purpose", prompt:
      "Use the bank-statement-to-jsonl skill on <file>.
       Write the output to bank_statement_transactions.jsonl in that same
       directory. Do not declare it done until both gates pass — the format
       gate and the reconciliation gate against the totals the statement
       prints about itself. Report the counts, the reconciliation, and the
       absolute path of the JSONL.")

**It goes first because it is the long pole and nothing else waits on it.**
Parsing probes a page, writes a spec, streams the file and reconciles; the
graph work needs none of that, only the header. Started first, it costs the run
nothing. Started after the ops are applied, it costs the run its whole duration.

Do not read its output while it works, and do not poll it. Pick it up in pass
5, which is the first moment its answer is needed.

## Pass 1 — the header, and only the header

    python3 <skill-dir>/../bank-statement-to-jsonl/scripts/probe.py <file>

The probe's first block prints page one as rows — the letterhead, the account
block, the period. That is the whole input to passes 2 and 3.

**Never `cat` the statement, never Read it, never open it whole.** A four-page
statement survives it and a four-hundred-page one ends the run with a full
context and nothing written. Should the header genuinely not be on page 1 —
some banks print a cover sheet — ask the probe for the next page (`--page 2`).
Two pages, three at the outside. The rows are not yours to read at all: another
agent is reading them right now, and it will reconcile them against the
statement's own totals, which is a check no amount of reading here would match.

From that block, take: the **bank** (letterhead, registry footer, BIC/SWIFT,
licence line), the **client** (the addressed-to block — `Client:`, `Titular de
cont`, `Клієнт:` — with its registration number, tax id, account number and
IBAN), the **period**, and the **currency**.

## Pass 2 — the bank is the twin

    export_graph(layer: "<layer-uuid>", dir: "<dir>")

Read `graph.values.yaml` whole — it is small, and it serves every pass.

**On a statement the issuer is the twin and the account holder is the
counterparty.** The bank owns the letterhead and the registry footer; the
client sits in an addressed-to field, however large its name is printed. Get
this backwards and a client's money lands in the bank's treasury.

A stored `legal_name` or `registration_id` in the `[company]` node settles it:
match the header's bank against those keys.

| what the `[company]` node holds | what to do |
|---|---|
| empty | it is the bank's place — fill it and `rename:` it to the bank |
| this same bank | update only the fields the header adds; never rewrite what is there |
| a different company | **stop and ask.** The layer is somebody else's twin, and a statement from another bank does not belong on it |

The last row is not caution for its own sake: overwriting a twin is the one
edit that silently reassigns every node hanging under it.

## Pass 3 — the client, resolved against the register

The canvas shows the nodes somebody placed on the layer — usually one empty
placeholder per type. It cannot tell you whether this client already exists,
because records created by earlier runs and typed in by people are off the
canvas entirely. One tool answers that:

    find_records(type: "<client type slug>", values: ["<the client's identity>"], dir: "<dir>")

Pass the identity a register would hold the client by — its registration number
or tax id when the header prints one, its legal name otherwise, and name the
field in `fields:` when the type does not mark it as an identity key. Read the
type in `types.schema.yaml` first (`python3 <skill-dir>/../dto-fill/scripts/schema.py`)
rather than assuming which field that is.

Then, in order:

1. **FOUND** → use that record: `type:` plus **its** `ref:`, the one the answer
   printed. Never a ref you derived — a derived ref that differs from the
   stored one creates a second copy of the client you just found.
   A hit with no ref was made by hand: carry its `id:` instead.
2. **NOT FOUND, and the client placeholder is empty** → fill it and `rename:`
   it. This is the one that shows on the canvas, so prefer it.
3. **NOT FOUND, and the placeholder already holds a different client** → leave
   that node alone and give this client a record of its own, `type:` plus a
   `ref:` you mint. **This is the normal outcome, not a failure.** The cell is
   one slot on a picture; the form behind it holds every client there is.
   Overwriting the occupant would delete a real client to make room for
   another.
4. **UNKNOWN** — the probe could not run → create nothing, write nothing for
   the client, and say so. An unchecked subject is never created.

Rung 3 is the case the canvas invites you to get wrong, because the empty-
looking slot and the occupied one look equally writable from the values file.

## Pass 4 — write the ops, once

Write `<export>/graph.ops.yaml` from
`<skill-dir>/../dto-fill/templates/graph.ops.template.yaml`, then:

    apply_graph(ops: "<export>/graph.ops.yaml", write: true)

The ops are few — a bank, a client, and the account the header names if the
client's type carries a field for one. The planner's rules are dto-fill's, and
the ones that bite here: `rename:` for a title, never `title:` in `set:`;
`set:` keys are fields of that node's type; dates `"YYYY-MM-DD"`; no empty
values; `ref:` required on any `type:` op.

**The file is the plan — do not draft it twice.** No prose rehearsal of the
fields before writing them. Deciding is the work; the file is where the
decision becomes executable, and `apply_graph` prints the whole diff before a
byte is sent.

**Take the client's actor id from the apply, not from anywhere else.** An apply
stamps `id:` onto every op and lists created records with their uuid in
`result.json`. That uuid is what pass 5 posts against. An id remembered from a
search, or copied from an earlier run, is how a statement lands on a
stranger's accounts — the one error in this run that no later ops file undoes.

## Pass 5 — collect the parser, then post

Now, and not before, read the second agent's report.

| it says | do |
|---|---|
| both gates passed | post |
| gates failed, or it stopped and asked | **do not post.** Report what it said and stop — a JSONL that did not reconcile is a ledger with rows missing or doubled, and posting it is worse than not posting |
| it produced no file | say so; the graph half still stands and is worth reporting on its own |

Then, once:

    post_statement(account_id: "Bank Transaction",
                   actor_id: "<the client's actor uuid from the apply>",
                   path: "bank_statement_transactions.jsonl")

It resolves one (account-name, currency) pair per currency in the file,
attaches both sides to the client, and posts each row's `debit_sum` to the
debit side and `credit_sum` to the credit side. Each transaction is dated by
its own row, not by today. Refs are derived from the rows, so a repeated run
posts nothing twice — which also means a run interrupted here can simply be
run again.

**Pass `timezone` when the statement's country is known** — e.g.
`timezone: "Europe/Kyiv"` for a Ukrainian bank. The rows print a wall clock
with no offset; without this it is read as UTC, which is hours off the times
the statement shows.

Read its per-currency turnovers back against the totals the parser reported. If
the parser reconciled and these match it, the ledger on the client is the
statement. If they differ, say so plainly rather than averaging them in prose:
the two numbers came from the same file and disagreeing is information.

**`dry_run: true` first** when the client was created in this very run, or when
the layer is one you have not posted to before — it resolves the pairs and
totals the file without writing, and it is the cheapest way to see the money is
about to land on the right actor.

## When to stop and ask

Each of these gets a report and no write:

- **The `[company]` node holds a different company.** The layer is another
  twin. Do not rewrite it.
- **The header names no client**, or names two. Posting turnover against a
  guess is not recoverable by an ops file.
- **Two banks in the header** — a correspondent bank is not the issuer, but
  telling them apart from the letterhead alone is not always possible.
- **The client's identity is unknown** (`find_records` could not probe).
- **The parser did not reconcile.** Report its gap; the graph half is still
  worth applying and saying so.
- **No text layer** — the statement is a scan. The parser agent will say this
  itself; the header still needs OCR before pass 1 can run.

## Output

    <export>/graph.ops.yaml            the bank and the client
    <export>/result.json               what the apply changed, cumulative
    bank_statement_transactions.jsonl  the rows, from the parser agent

Report, in this order:

1. **the bank and the client**, each with the header line that identified it,
   and which of the two the layer already held;
2. **how the client resolved** — found in the register, filled into an empty
   placeholder, or created beside an occupied one — and its uuid. Say which
   rung, not just the outcome: "created beside an occupied placeholder" and
   "created because nothing was there" are different facts about the layer;
3. **the applied ops by node**, and a bare list of what the header carried and
   you did not write;
4. **created records separately**, with their `ref:` and uuid — they are on no
   canvas, and a create is the one thing another ops file cannot undo;
5. **the parser's verdict**: rows, and its reconciliation against the
   statement's own totals, quoted as it reported them;
6. **the posting**: transactions written, duplicates skipped, and the
   per-currency debit and credit turnovers, side by side with the parser's —
   never one number standing for both;
7. **the two paths**, and the account name the transactions now sit under.

Never report the run as done on the graph half alone, and never imply the rows
landed without quoting the posting's own counts.
