---
name: bank-statement-to-jsonl
description: Turn a bank statement into JSONL, one object per transaction, without reading it — sample one page, work out from the x-coordinates which column each number is in, declare a ColumnSpec, write a thin parser on scripts/statement_lib.py, stream the file through it, and prove it against the totals the statement prints about itself. Handles pdf, xlsx, csv and text exports, hundreds of pages, us (1,163.14) and eu (1.238,92) formats. Use when the user hands over a bank statement, an account extract, a card statement or a transaction export and asks to parse it, turn it into jsonl or json or rows, or pull the transactions out of it. Triggers on "распарси выписку", "выписка банка в jsonl", "сделай парсер выписки", "разбери банковскую выписку", "вытащи транзакции из выписки", "конвертируй выписку в json", "выписка из pdf в jsonl", "parse this bank statement", "bank statement to jsonl", "extract the transactions from this statement", "write a parser for this statement", "/bank-statement-to-jsonl".
---

# bank-statement-to-jsonl — the column is the meaning

A statement is a table that was printed and then lost its grid. The text layer
keeps every character and throws away the one thing that says what a number
means: which column it sat in. `01/04/2026 Pachet IZI 29.00` is a debit or it
is a credit, and the line does not say — only `x1≈474.5` against `x1≈573.7`
says. Read a statement as prose and you will guess, and the guess is silent:
the file parses, the rows look right, and the side is inverted on every one.

So this run never reads the file to understand it. It samples one page to
**measure** it, writes a spec that measures the same way, runs that over the
whole file, and lets the statement's own printed totals say whether the
measurement held. **The file is read once, by a script, and never by you** — a
four-hundred-page statement then costs what a four-page one costs, and a run
that reads pages to be thorough runs out of context before it writes any code.

Nothing here is destructive, and one thing is unrecoverable anyway: a row the
parser never saw. It leaves no trace in the output, none in the report, and
nobody opens the PDF again to look for it. That is what the second gate is
for, and it is why a run does not end without it.

## The call

    /bank-statement-to-jsonl <file> [out <path.jsonl>] [pages <a-b>]

- `<file>` — the statement: `.pdf`, `.xlsx`, `.csv`, `.txt`, or anything else
  with a text layer. Size is not a reason to refuse and not a reason to sample
  the *output*. Hundreds of pages is the case this is built for.
- `out` — where the JSONL lands. Defaults to the statement's stem with a
  `.jsonl` suffix, beside it.
- `pages` — a page range, **only when the caller asks for one**. A range you
  chose yourself is a silent truncation that then fails the totals gate for a
  reason looking exactly like a parsing bug.

## The record

One JSON object per line, and nothing else in the file:

```json
{"transaction_date":"2026-04-07","debit_sum":"0.00","credit_sum":"302.50","description":"Incasare Instant Factura PMN0051;..."}
```

| key | rule |
|---|---|
| `transaction_date` | always present, `yyyy-mm-dd` |
| `transaction_time` | **only** when that row prints a time; key omitted otherwise |
| `debit_sum` / `credit_sum` | strings, dot decimal, exactly 2 places, unsigned, `"0.00"` on the empty side |
| `description` | this row's text with its continuation lines joined in |

Amounts are strings so nothing downstream re-floats them, and `Decimal`
inside so that summing forty thousand rows is exact.

**`transaction_time` comes from the transaction's own row, never from the
page.** The only time-like tokens in the three reference statements are the
document's print stamp in the page header — BT's `Tiparit: 2026-05-15
11:55:22`, Unicredit's `14:28:26`. A "find a time on the page" rule stamps
every transaction with the moment somebody hit print. All three emit no
`transaction_time` at all, and that is the correct answer.

## Pass 0 — take your own copy of the toolkit

    python3 <skill-dir>/scripts/init_workspace.py <file>

This drops **copies** of `statement_lib.py`, `probe.py` and `validate.py` into
the folder you are in, beside the statement, plus a `parse_<name>.py` already
filled in with what the probe measured and a `NOTES_<name>.md`. From here on,
every path below is the local copy — `python3 probe.py`, not the skill's.

**Those copies are yours, and editing them is the point.** Statements disagree
in ways no set of options anticipates: a space used as a thousands separator,
a time printed on the row's second line, five numeric columns where three were
expected. Sooner or later one needs the *library* changed and not just the
spec, and when that day comes the answer is to change it. The copy is why that
is cheap — a quirk fixed in your copy cannot reach another bank, and the banks
it would have reached are the ones nobody will reopen to notice.

What the library already handles, so you extend rather than rebuild: row
clustering, claiming numbers by band, continuation merging, both number
grammars, space-split numbers, date order and carry-forward, marker columns,
streaming JSONL, and reconciliation.

**Work outward in this order**, because each step is cheaper and safer than
the next:

| order | where | for what |
|---|---|---|
| 1 | the spec in `parse_<name>.py` | bands, formats, skip/stop — most statements end here |
| 2 | the hooks at the bottom of that file — `cell_map`, `row_filter`, `post` | one bank's oddity, with no library edit at all |
| 3 | your copy of `statement_lib.py` | what the library genuinely cannot express. Change it |
| 4 | `from_scratch.py` — your own parser, owing the library nothing | when the abstraction is the problem, not the gap |

Only one thing is worth saying against editing the library, and it is not
"don't": a fix that turns out to be general belongs back in the skill, and
**that** is the one move that has to be proved safe —

    python3 <skill-dir>/scripts/regress.py

replays every statement the skill has ever parsed against the totals each one
prints about itself. Run it before promoting anything and after. A case that
passed and now does not is the change you just made.

## From scratch, when the library is the wrong shape

`from_scratch.py` is copied into the folder with everything else, and taking
it is a legitimate move rather than a defeat. **A wrong abstraction costs more
than no abstraction**, and `ColumnSpec` is an abstraction with a premise: that
the statement is a grid of columns whose x-coordinates mean something. Where
that premise fails, every field in the spec is a lever attached to nothing.

Take it when you see:

| what you are looking at | why the spec cannot help |
|---|---|
| an HTML, JSON or fixed-width export | there are no coordinates to band; the structure is already explicit and you should read it directly |
| a receipt-style ledger — one transaction per block, not per row | rows are not the unit, so row assembly is answering the wrong question |
| OCR output | coordinates exist but are noise; the text is all you can trust |
| two rounds of spec edits that have not converged | the shape is being forced, and a third round is fitting, not fixing |

Inside `extract()` you are free: pdfplumber, PyMuPDF, an HTML parser, regex
over a text dump, another library entirely. **Two things survive the rewrite**,
and they are what makes any of it trustworthy:

- **the record** — the same five keys, the same string amounts, one side
  non-zero;
- **the second gate** — `validate.py` reads the JSONL and nothing else, so it
  checks a hand-written parser exactly as it checks a spec-driven one. Writing
  the parser yourself does not remove the need to prove it; it is the case
  where proving it matters most, because there is no shared code that other
  statements have already exercised on your behalf.

Keep importing `money`, `parse_date` and `parse_amount` unless you have a
reason not to. They are four lines each and they are where the silent errors
live — `1.238,92` read as 1.24, `01/04/2026` read as the fourth of January, a
float that leaves forty thousand rows a cent short. Replace them knowingly or
not at all.

The regression suite takes such a parser as a first-class case (`script:`
instead of `spec:` in `fixtures/cases.json`), because the check was never
about how the rows were produced.

## Pass 1 — one page, and whether a table is on it

`init_workspace.py` has already run the probe once and pasted its findings
into `NOTES_<name>.md` and the spec. Run it again yourself when you want a
different page or only the totals:

    python3 probe.py <file>
    python3 probe.py <file> --page 6
    python3 probe.py <file> --totals

The probe is the only thing that looks at the statement before the parser
does. It prints one page as rows, the header row it scored highest, the
right-edge clusters numbers stack on, the number and date grammars, the totals
the document prints about itself, and a proposed `ColumnSpec`.

**Read `CANDIDATE AMOUNT COLUMNS` before anything else.** For each column it
prints the sum of its negatives and of its positives, and marks the ones that
match a total the statement prints about itself. That mark is evidence, not a
heuristic — on a card statement with five numeric columns (amount, amount in
the operation currency, fee, cashback, running balance) it is what separates
the one you want from four that look exactly as plausible. A column whose
values are all identical is dropped: that is a fee column, not an amount.
Where nothing is confirmed, the proposal falls back to position and is a
guess — treat it as one.

**Never `cat` the file, never Read a PDF, never dump the text layer.** A
four-page statement survives it; a four-hundred-page one ends the run with a
full context and no parser written. One page carries every fact the spec
needs.

**Which page.** Page 1 is the worst page in the file and the one everybody
samples: BT spends twenty-five lines on fee schedules and deposit-guarantee
law before the word `Data` appears, and Exim's table header sits under a
thirty-line account block that itself prints `Rulaj debit: 478,805.56` — a
summary figure that is not a transaction and parses beautifully as one. Take
the column *names* from page 1 and the *rails* from a middle page, which has
no summary block above it and no totals footer below. Two probes, three if
they disagree. Not ten.

**Then answer one question: is there a transaction table here at all?** There
is when numbers stack on a shared right edge — two or more rows whose amounts
share an `x1` within a point or two — *and* a date sits at the left of the
rows carrying them. The test is geometric on purpose. A summary block of
label/value pairs, a fee schedule and an address block all have numbers and
alignment; what they lack is a repeating date down the left of a run of rows.

**Never conclude a table is there from the file's name, from `EXTRAS CONT` in
a heading, or from the caller calling it a statement.** They are right about
the document and saying nothing about the page you sampled.

## Pass 2 — fix the column map

Decide once what every band means. Everything downstream is this decision
applied ten thousand times, so a band ten points wrong is ten thousand wrong
rows and not one of them looks wrong.

**Bands are right edges.** Money is right-aligned, so `x1` is the stable
coordinate: BT's debit column holds `29.00` at x0=451.9 and `11,466.67` at
x0=439.4 — different left edges, the same x1≈474.5.

**The rails come from the data, the names from the header, and the two do not
agree.** A header word is centred or left-set over its column. BT is the kind
case — `Debit` spans 450.7–475.7 and its amounts land inside it. Exim is the
normal one — `Rulaj Debit` spans 361.5–403.3 while its amounts land at
x1≈414.1, eleven points *past* the header. Band from the header alone and
every Exim debit falls outside every band: the file parses to nothing,
cleanly, with no error.

**A third rail on the right is a running balance, and it is a trap.** Exim
prints `… Utilizare transa credit HAPPYCOLOR 178,480.22 178,480.22` — the same
number twice: the credit at x1≈501.9 and the balance at x1≈604.0. "Take the
last number on the line" reads the balance as the amount on every row, and
where the two coincide it even looks right. Declare the balance band so the
library discards it; an undeclared rail becomes an unclaimed-number warning,
which is the same finding arriving the other way round.

**A number can live inside the description.** Exim's `COMS 13.57` sits at
x1≈209, Unicredit writes `RON 3.193,01 @5,16` at x1≈258. These are narrative,
not amounts. Bands keep them out and a regex for "a number" never will.

**A row starts where a number lands in an amount band — not where a line
starts with a date.** Exim continues a transaction onto the next line and
*begins the continuation with the date again*: `02-03-2026 INT SRL 24-CAFTM`
is the tail of the row above. Split on dates there and every transaction is
cut into pieces. BT tells the opposite lie — its same-day transactions after
the first print no date at all and must inherit the day's (`date_carry`).
Two files, two contrary lies from the same signal; which is why the signal is
the amount band and the date is a field, not a delimiter.

**Noise rows carry real numbers in real bands, so geometry cannot drop them.**
BT's `SOLD ANTERIOR 944.64` puts its number at x1=573.7 — squarely in the
credit band, a perfect forgery of a credit. `RULAJ ZI` fills *both* bands at
once. Only the description tells them apart, so `skip_rows` holds a veto.

**A table also ends, and a skip list is the wrong tool for the end.** After
BT's last transaction come `RULAJ TOTAL CONT` (both bands), `SOLD FINAL CONT`,
`TOTAL DISPONIBIL` and `Fonduri proprii` — four rows in real bands, worth
~285k, that a three-item skip list sails straight past. `stop_re` ends the
run at the first of them. A skip list must name every trailer label; a
terminator needs to recognise only the first one.

Then read the shape off this table:

| what the sampled page shows | spec shape | how you know |
|---|---|---|
| two amount rails plus a third further right that changes every row | `debit` + `credit` + `balance` (discarded) | the third rail on row *n* is row *n−1*'s moved by the amount |
| two amount rails, nothing to their right | `debit` + `credit` | the header names both sides |
| one rail, some values carrying a leading `-` | `amount` alone | negative is a debit, positive a credit |
| one rail, no signs, a `D`/`C` or `+`/`-` token in its own column | `amount` + `sign_band` + `sign_re_debit`/`sign_re_credit` | that column holds exactly two distinct values |
| a sheet or delimited file — columns are fields | the same fields, with `col(i)` / `cols(a,b)` | bands become column indices; nothing else changes |
| one rail, no signs, no token, direction only in the wording | **stop and ask** | guessing the side is the one error nobody downstream can detect |

The last row is not a failure of nerve. Every other shape is checkable against
the totals; a direction inferred from `Plata` versus `Incasare` is a vocabulary
you invented, and if its mistakes cancel, the totals reconcile anyway.

**Formats are declared, never guessed.** BT and Exim are `us` — `1,163.14`.
Unicredit, from the same country in the same month, is `eu` — `1.238,92`. The
grammars reject each other, so a mis-declared format fails on row 1 instead of
producing plausible money forever. Dates the same way: `01/04/2026`,
`02-03-2026`, `01.04.2026` — take the order from the statement's own period
line, and note that a stray `2026-05-15` print stamp in a page header will
vote for `ymd` if you let it.

**Every row carries a currency, and an amount without one is a number rather
than money.** Most statements name it once in the account header — set
`currency_default`. Some print it per row in a column of its own — band it
with `currency`, and then the statement can legitimately hold more than one,
in which case the totals gate is only meaningful per currency
(`validate.py --currency UAH`). When nothing names it, the record carries
`XXX` — ISO 4217's own code for "no currency" — so a reader can always tell
*unknown* from *assumed*, which a blank or a guessed default would hide.

**A currency column does not necessarily describe your amount column.** A card
statement prints the purchase twice — once in the card's currency and once in
the merchant's — and its `Валюта` column names the *second* one. Band it while
taking the first and three foreign purchases come out as hryvnia labelled USD,
with the totals still reconciling to the cent, because the amounts were never
wrong. Check what the currency column is a column *of*; when the amount
column's own header names the currency (`Сума в валюті картки (UAH)`), that
header is the better source and `currency_default` is the right field.

**Column names never matter.** The parser bands by geometry and does not read
a header at any point — a statement in Ukrainian, Chinese or Arabic parses the
same way, because `x1≈260` means the same thing in every language. The probe
puts names beside clusters only so you can read its output, and its dictionary
covers Latin and Cyrillic; on a statement it cannot name, the clusters print
unnamed and everything still works. **Never make a rule out of a header word.**

**Numbers can be split by their own thousands separator.** `-2 633.00` reaches
you as two tokens, `-2` and `633.00`, because the separator is a real space
and no `x_tolerance` merges across it. Left alone the row is claimed as 633.00
— wrong by two and a half thousand and still shaped like money.
`space_thousands` (on by default) rejoins strict 3-digit groups. It is also
why a probe and a parser must tokenise identically: a probe that reports sums
the parser cannot reproduce is worse than one that reports nothing.

**A time often sits on the row's second line.** Card statements print the date
and the time stacked — `21.09.2026` then `08:20:02` beneath it — so `time_re`
is searched on continuation lines too, and the matched text is taken out of
the description rather than left in it twice.

**A bare integer is not an amount.** `require_decimals` defaults true because
BT repeats `004 0264 30 8028 (BT)` in every page header and `8028` lands in
its debit band. Turn it off only for a statement that genuinely prints whole
amounts; the probe counts the decimal-less tokens so the choice is informed.

## Pass 3 — the parser

`parse_<name>.py` already exists and already carries the probe's proposal.
Confirm its bands against the page dump, fill in what the probe left
commented — `description`, `stop_re`, and the `expect_*` totals — and run it.
(For a statement the probe could not pre-fill, `<skill-dir>/templates/` holds
bare skeletons for pdf and for tabular input.)

**The spec is the parser — do not write it twice.** No prose rehearsal of the
bands before declaring them, no *"the debit column appears to be at
approximately 474, so I will now write…"*. Deciding is the work and belongs in
your reasoning; the file is where the decision becomes executable. Restating a
four-band spec before writing it is the most expensive thing this run does and
buys nothing that running it does not buy better.

**One script per bank, not per run.** Name it for the bank and leave it beside
the output: the next statement from that bank is then a re-run rather than
another measurement.

## Pass 4 — the whole file, once

    python3 parse_<name>.py <file> -o <out.jsonl>

It streams — one page in memory, one line out per transaction — and puts a
report on stderr. Read the report; it is twenty lines and the cheapest bug
report in this run. Four of its counters are diagnoses, not statistics:

| counter | what a bad value means |
|---|---|
| `orphan rows` well above zero | `row_start` is cutting continuations into rows of their own |
| `transactions` far above the statement's own count | noise is getting through, or a trailer is past the terminator |
| `two-amount rows` | a band is wrong, or a turnover row got through |
| `unclaimed numbers` | a column is undeclared — the balance rail is the usual answer |

**Do not read the JSONL.** Not with `cat`, not with Read, not "just to check":
that is the file you wrote a script to avoid reading. Pass 5 reads ten lines of
it on purpose; the rest is read arithmetically. Fix the spec and re-run — a
re-run is one tool call and no tokens.

## Pass 5 — the two gates

    python3 validate.py <out.jsonl> --source <file> \
        --expect-debit <as printed> --expect-credit <as printed> --numbers us

**Gate one — the shape of the rows.** Dates really `yyyy-mm-dd`, amounts
strings with two places, exactly one side non-zero, descriptions non-empty,
`transaction_time` only where a time is printed. Then read the first five to
ten rows **against the sampled page**, not against themselves: ten internally
consistent rows are precisely what an inverted debit/credit map produces. The
question is not "is this valid JSON" but "is line 1 the transaction I can see
at the top of page 6".

**Gate two — the statement's own arithmetic.** Every statement prints
something about itself, and it is free ground truth nobody has to be asked for:

| statement | what it prints |
|---|---|
| Exim | `Total Rulaje 478,805.56 478,805.56` |
| Unicredit | `Credit total … (20) 20.706,54`, `Debit total … (17) -20.705,54` — **with row counts** |
| BT | `RULAJ TOTAL CONT 140,265.18 141,597.01`, and per-day `RULAJ ZI` |

A match to the cent on both sides is the strongest evidence this run can
produce — stronger than any number of rows read by eye, because it is the only
check that sees the rows nobody looked at. Where the statement prints counts,
check them and say so: totals can reconcile with rows missing, when a debit
and a credit of equal size both vanish. Counts cannot. Per-day rows like BT's
`RULAJ ZI` are better still — they localise a failure to a date.

**This gate is not optional garnish, and here is the evidence.** A fixed-width
line-binning bug once split `14/04/2026 RULAJ ZI 26,729.67 100,000.00` across
two bins, minting a phantom transaction from the orphaned numbers and zeroing
the baseline that should have caught it. The first ten rows were flawless.
Only the totals saw it. Gate one structurally cannot find a dropped page, a
balance column read as an amount, or a trailer counted as a transaction.

**A mismatch is a bug in the spec, never a number to explain away.** Do not
report "totals differ by 178,480.22, likely a credit counted twice" — the gap
is a lead and that number is a row you can go and find. `validate.py` names
the signature it recognises; the common ones:

| the difference is | look at |
|---|---|
| exactly one row's amount | a noise or totals row emitted, or one row lost at a page break |
| a clean multiple of one side | the balance rail being read as an amount |
| equal on both sides, both over | the signed/sided convention, which `validate.py` reconciles for you |
| a factor of ~1000 on some rows | the number format is the other one |
| small and growing through the file | continuations emitted as rows of their own |

**Two rebuilds.** If it still will not reconcile, stop and report the gap with
the rows you suspect. A third guess at a spec that has been wrong twice is no
longer converging on the statement — it is fitting the totals, and a parser
fitted to a total is wrong in the one way that total can no longer detect.

## When to stop and ask

Stopping is a result. Each of these gets a report and no output file, and none
improves by trying harder. **Refuse with the fix in the same breath** — "it is
a scan" is a dead end; "it is a scan, nothing here does OCR, ask the bank for
the same statement with a text layer and this run re-runs on it" is a next
step.

- **A PDF with no text layer.** The probe returns almost no words: it is a
  scan, so there is neither text nor geometry to band, and there is no OCR on
  this image — no tesseract, no ocrmypdf. Ask for the same statement with a
  text layer, or as `.csv`/`.xlsx`: one request to the bank turns a dead end
  into a re-run. Reading the pages as pictures (`pdftoppm -png -r 150 <file>
  page`, then Read) recovers the words but not their x-coordinates, so it is
  transcription and not parsing — worth it for a handful of pages, and only
  with the totals gate carrying the whole proof.
- **A password-protected PDF.** Ask for the password. Do not try any.
- **A legacy `.xls`.** `xlrd` is not installed and there is no LibreOffice, so
  nothing here opens it. Ask for `.xlsx` or `.csv`; naming the one-line fix is
  the entire value of the refusal.
- **One unsigned amount column with no direction token.** Show the caller two
  rows you cannot tell apart and ask which is which.
- **Two currencies or two accounts in one file.** Summing across them is
  meaningless and the gate can never pass. Ask whether to split.
- **The layout changes mid-file.** Rails that hold on page 6 and not on page
  200 mean two statements concatenated. Report the page where it turns.
- **Two failed reconciliations.** Report the gap, the rows you suspect, the
  spec as it stands and the parser's path.

## Output

    <out.jsonl>          the transactions, one JSON object per line
    parse_<name>.py      the spec that produced them
    statement_lib.py     your copy, with whatever you had to change in it

All of it stays on disk. The JSONL is the answer; the parser and the library
beside it are *why* the answer is that answer, and together they make the next
statement from this bank a re-run rather than another measurement.

Close with the gate, not with memory:

    python3 validate.py <out.jsonl> --source <file> ...

Then report, in this order:

1. **the file and the shape it turned out to be**, with the map on one line:
   `two columns, debit x1≈474.5, credit x1≈573.2, us numbers, %d/%m/%Y`;
2. **the pages you probed**, by number, and why those — never "I examined the
   document";
3. **the counts**: transactions written, pages read, rows skipped as noise with
   the patterns that skipped them, orphan rows, unclaimed numbers;
4. **the reconciliation with both numbers side by side** — what the statement
   prints and what the JSONL sums to, per side, plus row counts wherever the
   statement prints them. Say "reconciles to the cent" only when it does, and
   never round anything to make it true;
5. **the first three rows verbatim**, each against the line on the sampled page
   it came from — the only place a reader can check the column map by eye;
6. **what is not in the file**: rows deliberately dropped that a reader might
   expect (opening and closing balances, daily turnover), and any key the
   statement cannot fill — `transaction_time` where no time is printed — in one
   line, not once per row;
7. **the paths**, and the line saying the parser takes this bank's next
   statement without another probe;
8. **whether you changed `statement_lib.py`**, and if so what and why — a
   changed library is the part a reader would never think to check, and if the
   change looks general it is worth promoting into the skill behind
   `regress.py`.

Never offer a row count as the result. A count is a number the reader cannot
check; the reconciliation is one the statement has already agreed to. And
**never declare done on a gate you did not run** — the whole design of this run
is that you did not read the file, which leaves the arithmetic as the only
thing between a JSONL that is correct and one that is merely confident.
