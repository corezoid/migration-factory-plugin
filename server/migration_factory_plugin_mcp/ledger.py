"""Posts a batch of transactions onto an actor's accounts from a JSONL file,
one object per row:

    {"transaction_date":"2026-09-21","transaction_time":"08:20:02",
     "debit_sum":"236.10","credit_sum":"0.00","currency":"UAH",
     "description":"MAGAZiN 5499"}

One (account-name, currency) pair is two accounts on the actor — a debit id
and a credit id. `debit_sum` goes to the debit side, `credit_sum` to the
credit side, unsigned; the side carries the direction, not a sign. A file may
hold several currencies; each pair is resolved on first sight and cached for
the rest of the run.

Idempotent by construction: each transaction's ref is derived from the row's
own raw text (see ``ref_for``), so re-running the same file posts nothing
twice. THIS IS THE ONE PLACE THAT ALGORITHM LIVES — nothing outside this
module may reimplement statement posting, or its idempotency ref will not
match this one and duplicate detection silently stops working for that data.

``post_batch`` exists so a caller with many actors (a monthly usage export
with 200+ accounts, say) never has reason to loop the MCP tool call itself or
reimplement this against the Simulator API directly — it costs one tool call
instead of one per actor, and it is exactly this module underneath.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .graph.records import is_identity_field, query_candidates
from .graph.schema import FieldSpec, Type, TypeSet
from .simulator import errors as sim_errors
from .simulator.client import SimulatorClient
from .simulator.finance import INCOME_TYPE_CREDIT, INCOME_TYPE_DEBIT, AccountSides

log = logging.getLogger("migration-factory-plugin")

MAX_LINE_BYTES = 1 << 20  # 1 MiB — a pathological JSONL line is a bug, not a huge row

# The projection an actor-by-field lookup needs: enough to pick one actor out
# of a tie by recency, and nothing of the form schema.
ACTOR_FIELD_LOOKUP_FILTER = "id,title,ref,status,data,formId,createdAt"
ACTOR_FIELD_LOOKUP_LIMIT = 50

# How many create_transaction calls run at once. Safe to raise: each ref is a
# stable hash of its own row (see ref_for), so posting out of order changes
# nothing — Simulator's own duplicate-ref check is what makes a rerun
# idempotent, not the order calls land in.
_DEFAULT_CONCURRENCY = 10


@dataclass
class Record:
    date: str = ""
    time: str = ""
    debit: str = ""
    credit: str = ""
    currency: str = ""
    description: str = ""

    @staticmethod
    def from_json(d: dict) -> "Record":
        return Record(
            date=str(d.get("transaction_date", "") or ""),
            time=str(d.get("transaction_time", "") or ""),
            debit=str(d.get("debit_sum", "") or ""),
            credit=str(d.get("credit_sum", "") or ""),
            currency=str(d.get("currency", "") or ""),
            description=str(d.get("description", "") or ""),
        )


@dataclass
class Options:
    account_name: str
    path: str
    workspace_id: str
    # The single-actor form: every row posts to this actor.
    actor_id: str = ""
    # The many-actor form: `actor_type` (a slug in types.schema.yaml) and
    # `actor_field` (a field name on that type) say how to look an actor up;
    # each row then resolves its own actor from its own `uniq_actor_field_value`
    # instead of all rows sharing `actor_id`. A row with no
    # `uniq_actor_field_value` still falls back to `actor_id` when one was
    # given, so a file can mix a few unattributed rows with many attributed
    # ones.
    actor_field: str = ""
    actor_type: str = ""
    group_id: int = 0
    ref_prefix: str = ""
    timezone: str = ""
    default_currency: str = ""
    dry_run: bool = False
    concurrency: int = _DEFAULT_CONCURRENCY


@dataclass
class CurrencyTally:
    currency: str
    actor_id: str = ""
    rows: int = 0
    debit: float = 0.0
    credit: float = 0.0
    debit_id: str = ""
    credit_id: str = ""
    name_id: str = ""
    currency_id: int = 0


@dataclass
class Result:
    records: int = 0
    posted: int = 0
    duplicate: int = 0
    skipped: int = 0
    currencies: list[CurrencyTally] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actors: set = field(default_factory=set)


@dataclass
class _Sides:
    debit: str
    credit: str
    name_id: str
    currency_id: int


def pair_name(name: str, group_id: int) -> str:
    """The name a pair is bootstrapped under. A pair is workspace-level and a
    run is not; appending the group id makes it a physically distinct pair
    per group, so access (enforced on the pair) is effectively "theirs"."""
    name = name.strip()
    if group_id <= 0:
        return name
    return f"{name} {group_id}"


# Waited before retry 1, 2, 3 respectively, in seconds. A pair just
# bootstrapped by ensure_account_pair can briefly 403 its own creator sharing
# it — confirmed live 2026-09-29: the exact same share call, replayed minutes
# later with no code change, succeeded. That is an eventual-consistency
# window on the Simulator side, not a real permissions gap, so it is worth
# outlasting rather than believing on the first try.
_SHARE_RETRY_DELAYS = (5, 10, 15)


def _share_pair(
    sim: SimulatorClient,
    opts: Options,
    currency: str,
    name_id: str,
    currency_id: int,
    res: Result,
) -> None:
    """Not best-effort: rows nobody but this run's key can see are worse than
    rows not posted at all, so a refused share is retried with backoff
    (`_SHARE_RETRY_DELAYS`) before it is believed, and a share that still
    fails after every retry stops the whole run rather than posting
    transactions onto a pair the caller's own group cannot see."""
    if opts.group_id <= 0 or opts.dry_run:
        return
    attempts = 1 + len(_SHARE_RETRY_DELAYS)
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        if attempt > 0:
            time.sleep(_SHARE_RETRY_DELAYS[attempt - 1])
        try:
            sim.share_account_pair_with_group(name_id, currency_id, opts.group_id)
            return
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            last_exc = exc
            log.warning(
                "share pair %s_%s (%s, %s) with group %s: attempt %d/%d: %s",
                name_id, currency_id, opts.account_name, currency, opts.group_id,
                attempt + 1, attempts, exc,
            )
    raise RuntimeError(
        f"pair ({opts.account_name}, {currency}) could not be shared with group {opts.group_id} "
        f"after {attempts} attempts — stopping rather than posting transactions nobody but this "
        f"run's key could see: {last_exc}"
    ) from last_exc


def _resolve_pair(
    sim: SimulatorClient, opts: Options, currency: str, res: Result, pair_cache: dict,
) -> "tuple[str, int]":
    """The (account-name, currency) pair, bootstrapped once per currency and
    reused across however many actors the file resolves to — a many-actor
    file shares one pair per currency, not one per actor."""
    cached = pair_cache.get(currency)
    if cached is not None:
        return cached
    try:
        name_id, currency_id = sim.ensure_account_pair(
            opts.workspace_id, pair_name(opts.account_name, opts.group_id), currency
        )
    except Exception as exc:
        raise RuntimeError(f"ensure account pair ({opts.account_name}, {currency}): {exc}") from exc
    _share_pair(sim, opts, currency, name_id, currency_id, res)
    pair_cache[currency] = (name_id, currency_id)
    return name_id, currency_id


def _resolve(
    sim: SimulatorClient, opts: Options, actor_id: str, currency: str, res: Result, pair_cache: dict,
) -> _Sides:
    name_id, currency_id = _resolve_pair(sim, opts, currency, res, pair_cache)
    try:
        acc = sim.ensure_actor_account(
            actor_id, name_id=name_id, currency_id=currency_id, account_type="fact", search=True
        )
    except Exception as exc:
        raise RuntimeError(f"attach account to actor: {exc}") from exc
    return _Sides(debit=acc.debit, credit=acc.credit, name_id=name_id, currency_id=currency_id)


def _actor_field_spec(t: Type, field_name: str) -> FieldSpec:
    match = next((f for f in t.fields if f.name == field_name), None)
    if match is None:
        raise ValueError(f"field {field_name!r} not found on type {t.slug!r} — check types.schema.yaml")
    return match


def _find_actor_by_field(sim: SimulatorClient, t: Type, field: FieldSpec, value: str, workspace_id: str) -> str:
    """One row's actor, from the value it carries in `uniq_actor_field_value`.

    Tries the value as printed first, then — for an identifier-shaped value
    (letters/digits/spaces with at least one digit, the IBAN/tax-id/card-number
    shape this feature is for) — a whitespace-stripped, uppercased retry, the
    same `query_candidates` normalization `find_records` already applies. A
    JSONL value that isn't byte-identical to how Simulator stores it would
    otherwise come back "not found" with no sign of why.

    More than one match is not an error — the record most recently created
    wins, on the theory that an older one sharing the same key is a stale
    duplicate rather than the row's intended target."""
    actors: list = []
    for candidate in query_candidates(value):
        actors = sim.list_actors(
            t.form_id, workspace_id=workspace_id, filter=ACTOR_FIELD_LOOKUP_FILTER,
            query=f"{field.id}={candidate}", limit=ACTOR_FIELD_LOOKUP_LIMIT,
        )
        if actors:
            break
    if not actors:
        raise ValueError(f"no actor with {field.name}={value!r} (type {t.slug!r})")
    if len(actors) == 1:
        return actors[0].id
    return max(actors, key=lambda a: a.created_at).id


def resolve_actor_by_field(
    sim: SimulatorClient, types: TypeSet, actor_type: str, actor_field: str, value: str, workspace_id: str,
) -> str:
    t = types.by_slug(actor_type)
    if t is None:
        raise ValueError(f"unknown actor_type {actor_type!r} — check types.schema.yaml")
    field = _actor_field_spec(t, actor_field)
    return _find_actor_by_field(sim, t, field, value, workspace_id)


def _amount(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    try:
        value = float(s)
    except ValueError:
        raise ValueError(f"{s!r} is not a number") from None
    if value < 0:
        raise ValueError(f"{s!r} is negative — debit_sum and credit_sum are unsigned, the side carries the direction")
    return value


def _amounts(r: Record) -> tuple[float, float]:
    try:
        debit = _amount(r.debit)
    except ValueError as exc:
        raise ValueError(f"debit_sum: {exc}") from None
    try:
        credit = _amount(r.credit)
    except ValueError as exc:
        raise ValueError(f"credit_sum: {exc}") from None
    return debit, credit


def _location(name: str) -> ZoneInfo:
    name = name.strip()
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValueError(
            f"unknown timezone {name!r}: pass an IANA name such as Europe/Kyiv, "
            "or leave it out to read the rows' clock as UTC"
        ) from None


_DATE_TIME_LAYOUTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
_DATE_LAYOUT = ("%Y-%m-%d",)


def _occurred_at(r: Record, loc: ZoneInfo) -> int:
    date = r.date.strip()
    if not date:
        raise ValueError("no transaction_date: a row that cannot say when it happened would be stamped with today")
    clock = r.time.strip()
    if clock:
        value, layouts = f"{date} {clock}", _DATE_TIME_LAYOUTS
    else:
        value, layouts = date, _DATE_LAYOUT
    for layout in layouts:
        try:
            dt = datetime.strptime(value, layout).replace(tzinfo=loc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"transaction_date {r.date!r} with transaction_time {r.time!r} is not yyyy-mm-dd [hh:mm[:ss]]")


def ref_for(prefix: str, raw_line: str, side: str) -> str:
    """THE idempotency ref. Hashes the raw trimmed JSONL line text as it
    appeared on disk — not a re-serialization of the parsed record — because
    re-running the same file must reproduce the exact same ref, and only the
    original bytes are guaranteed stable across re-parses."""
    digest = hashlib.sha1(raw_line.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}-{side}"


def _comment(r: Record) -> str:
    when = r.date
    if r.time:
        when += " " + r.time
    d = r.description.strip()
    return when if not d else f"{when} {d}"


def _payload(r: Record) -> dict:
    d: dict = {"transaction_date": r.date}
    if r.time:
        d["transaction_time"] = r.time
    if r.description:
        d["description"] = r.description
    return d


def post(sim: SimulatorClient, opts: Options, types: Optional[TypeSet] = None) -> Result:
    if not opts.account_name.strip():
        raise ValueError("no account name: pass `account_name` with the account-name category to record under")

    actor_id = opts.actor_id.strip()
    actor_field = opts.actor_field.strip()
    actor_type = opts.actor_type.strip()
    if bool(actor_field) != bool(actor_type):
        raise ValueError(
            "actor_field and actor_type go together: actor_field names the field on the type "
            "actor_type identifies, and neither means anything without the other"
        )
    if not actor_id and not actor_field:
        raise ValueError(
            "no actor: pass `actor_id` for a single-actor file, or `actor_field`+`actor_type` so "
            "each row's own `uniq_actor_field_value` resolves that row's actor"
        )

    res = Result()

    field_type: Optional[Type] = None
    field_spec: Optional[FieldSpec] = None
    if actor_field:
        if types is None:
            raise ValueError(
                f"actor_field {actor_field!r} needs the type dictionary — pass `dir` pointing at "
                "the export directory that holds types.schema.yaml"
            )
        field_type = types.by_slug(actor_type)
        if field_type is None:
            raise ValueError(f"unknown actor_type {actor_type!r} — check types.schema.yaml")
        field_spec = _actor_field_spec(field_type, actor_field)  # raises early on a bad field name
        if not is_identity_field(field_spec):
            res.warnings.append(
                f"actor_field {actor_field!r} on type {actor_type!r} is not marked as an identity "
                "key — two different actors may legitimately carry the same value, and this run "
                "would then silently attribute rows to whichever one Simulator returns first"
            )

    prefix = opts.ref_prefix or "stmt"
    loc = _location(opts.timezone)

    sides_cache: dict[str, _Sides] = {}
    pair_cache: dict[str, "tuple[str, int]"] = {}
    actor_by_value: dict[str, str] = {}
    tally: dict[tuple, CurrencyTally] = {}
    pending: list = []

    with open(opts.path, "r", encoding="utf-8") as f:
        for line_no, raw_line_full in enumerate(f, start=1):
            raw = raw_line_full.strip()
            if not raw:
                continue
            if len(raw.encode("utf-8")) > MAX_LINE_BYTES:
                raise ValueError(f"{opts.path} line {line_no} exceeds {MAX_LINE_BYTES} bytes")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{opts.path} line {line_no} is not JSON: {exc}") from None
            r = Record.from_json(parsed)
            res.records += 1

            uniq_value = str(parsed.get("uniq_actor_field_value", "") or "").strip()
            if uniq_value:
                if field_spec is None:
                    res.skipped += 1
                    res.warnings.append(
                        f"line {line_no}: uniq_actor_field_value={uniq_value!r} but no actor_field/"
                        "actor_type was given to resolve it against — row not posted"
                    )
                    continue
                row_actor_id = actor_by_value.get(uniq_value)
                if row_actor_id is None:
                    try:
                        row_actor_id = _find_actor_by_field(
                            sim, field_type, field_spec, uniq_value, opts.workspace_id
                        )
                    except Exception as exc:
                        res.skipped += 1
                        res.warnings.append(f"line {line_no}: {actor_field}={uniq_value!r}: {exc}")
                        continue
                    actor_by_value[uniq_value] = row_actor_id
            elif actor_id:
                row_actor_id = actor_id
            else:
                res.skipped += 1
                res.warnings.append(
                    f"line {line_no}: no uniq_actor_field_value on this row, and no default "
                    "actor_id to fall back to — row not posted"
                )
                continue

            res.actors.add(row_actor_id)

            cur = r.currency.strip().upper() or opts.default_currency.strip().upper() or "XXX"
            debit, credit = _amounts(r)
            when = _occurred_at(r, loc)

            sides_key = f"{row_actor_id}::{cur}"
            if sides_key not in sides_cache:
                try:
                    sides_cache[sides_key] = _resolve(sim, opts, row_actor_id, cur, res, pair_cache)
                except Exception as exc:
                    raise RuntimeError(f"actor {row_actor_id} currency {cur}: {exc}") from exc
            sides = sides_cache[sides_key]

            tally_key = (row_actor_id, cur)
            ct = tally.get(tally_key)
            if ct is None:
                ct = CurrencyTally(
                    currency=cur, actor_id=row_actor_id, debit_id=sides.debit, credit_id=sides.credit,
                    name_id=sides.name_id, currency_id=sides.currency_id,
                )
                tally[tally_key] = ct
            ct.rows += 1
            ct.debit += debit
            ct.credit += credit

            for amount, account_id, side in (
                (debit, sides.debit, INCOME_TYPE_DEBIT),
                (credit, sides.credit, INCOME_TYPE_CREDIT),
            ):
                if amount == 0:
                    continue
                if opts.dry_run:
                    res.posted += 1
                    continue
                pending.append(_PendingTx(
                    line_no=line_no, cur=cur, side=side, account_id=account_id, amount=amount,
                    comment=_comment(r), ref=ref_for(prefix, raw, side), payload=_payload(r), when=when,
                ))

    _post_pending(sim, pending, res, opts.concurrency)
    res.currencies = sorted(tally.values(), key=lambda t: (t.currency, t.actor_id))
    return res


@dataclass
class _PendingTx:
    line_no: int
    cur: str
    side: str
    account_id: str
    amount: float
    comment: str
    ref: str
    payload: dict
    when: int


def _post_pending(sim: SimulatorClient, pending: list, res: Result, concurrency: int) -> None:
    """Fires every pending transaction concurrently instead of waiting on
    them one at a time. Safe because posting is idempotent by ref (see the
    module docstring) — unlike graph.apply's actor creation, nothing here
    stamps a file after each call, so there is no ordering to preserve."""
    if not pending:
        return

    outcomes: list = [None] * len(pending)

    def _one(i: int) -> None:
        tx = pending[i]
        try:
            sim.create_transaction(
                tx.account_id, amount=tx.amount, comment=tx.comment, ref=tx.ref,
                data=tx.payload, original_date=tx.when,
            )
            outcomes[i] = ("posted", None)
        except Exception as exc:  # noqa: BLE001 - captured per-tx, not raised
            if sim_errors.is_duplicate_ref(exc):
                outcomes[i] = ("duplicate", None)
            else:
                outcomes[i] = ("skipped", f"line {tx.line_no} {tx.cur} {tx.side}: {exc}")

    workers = concurrency if concurrency and concurrency > 0 else _DEFAULT_CONCURRENCY
    workers = min(workers, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, range(len(pending))))

    for outcome, warning in outcomes:
        if outcome == "posted":
            res.posted += 1
        elif outcome == "duplicate":
            res.duplicate += 1
        else:
            res.skipped += 1
            res.warnings.append(warning)


# --------------------------------------------------------------------- bulk

@dataclass
class StatementJob:
    """One file in a batch, and either an `actor_id` of its own (the
    single-actor form) or an `actor_field`/`actor_type` pair so the file's own
    rows resolve their actors from `uniq_actor_field_value` (the many-actor
    form). Everything else (account_name, workspace, group) is shared across
    the whole batch."""

    path: str
    actor_id: str = ""
    actor_field: str = ""
    actor_type: str = ""
    currency_name: str = ""
    timezone: str = ""
    ref_prefix: str = ""

    def label(self) -> str:
        """What identifies this job in a report — the actor id when the job
        named one, or the field it resolves actors from otherwise."""
        return self.actor_id or f"field:{self.actor_type}.{self.actor_field}"


@dataclass
class JobOutcome:
    job: StatementJob
    result: Optional[Result] = None
    error: Optional[str] = None


@dataclass
class BatchResult:
    account_name: str
    outcomes: list[JobOutcome] = field(default_factory=list)

    def totals(self) -> Result:
        """Grand total across every job that produced a Result (a job that
        failed outright contributes nothing — its failure is reported on its
        own outcome line instead)."""
        total = Result()
        by_currency: dict[str, CurrencyTally] = {}
        for outcome in self.outcomes:
            if outcome.result is None:
                continue
            r = outcome.result
            total.records += r.records
            total.posted += r.posted
            total.duplicate += r.duplicate
            total.skipped += r.skipped
            total.actors |= r.actors
            total.warnings.extend(f"{outcome.job.label()}: {w}" for w in r.warnings)
            for c in r.currencies:
                agg = by_currency.get(c.currency)
                if agg is None:
                    agg = CurrencyTally(currency=c.currency)
                    by_currency[c.currency] = agg
                agg.rows += c.rows
                agg.debit += c.debit
                agg.credit += c.credit
        total.currencies = sorted(by_currency.values(), key=lambda t: t.currency)
        return total


def post_batch(
    sim: SimulatorClient,
    jobs: list[StatementJob],
    *,
    account_name: str,
    workspace_id: str,
    group_id: int = 0,
    dry_run: bool = False,
    types: Optional[TypeSet] = None,
) -> BatchResult:
    """Posts every job with the same `post()` this module uses for a single
    file — same `ref_for`, same `_resolve`/`_share_pair` — so there is exactly
    one idempotency-ref implementation regardless of how many actors are in
    one call. A job's own failure (bad file, bad row) does not abort the
    batch: the other actors still post, and the failure is reported against
    that one job.

    `types` is one type dictionary shared across the whole batch — every job
    that names `actor_field`/`actor_type` is resolved against it, which is why
    a batch's jobs are expected to share one export directory."""
    batch = BatchResult(account_name=account_name)
    for job in jobs:
        opts = Options(
            account_name=account_name,
            path=job.path,
            workspace_id=workspace_id,
            actor_id=job.actor_id,
            actor_field=job.actor_field,
            actor_type=job.actor_type,
            group_id=group_id,
            ref_prefix=job.ref_prefix,
            timezone=job.timezone,
            default_currency=job.currency_name,
            dry_run=dry_run,
        )
        try:
            result = post(sim, opts, types)
            batch.outcomes.append(JobOutcome(job=job, result=result))
        except Exception as exc:
            batch.outcomes.append(JobOutcome(job=job, error=str(exc)))
    return batch
