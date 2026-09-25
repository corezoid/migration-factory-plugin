#!/usr/bin/env python3
"""Turn a bank statement into JSONL, one object per transaction.

    from statement_lib import ColumnSpec, run
    SPEC = ColumnSpec(debit=(430, 480), credit=(520, 580), numbers='us', ...)
    if __name__ == '__main__':
        sys.exit(run(SPEC))

Then:  python3 parse_<bank>.py <statement> -o out.jsonl

This module exists so that a per-bank parser is a *declaration* and not a
program. Everything hard about a statement is here; the per-bank file says
only where the columns are.

Why coordinates rather than text. A statement is a table that was printed and
then lost its grid. The text layer keeps every character and throws away the
one thing that says what a number means: the column it sat in. Read
`01/04/2026 Pachet IZI 29.00` as prose and nothing tells you whether 29.00 was
taken or received -- only x does (debit right-aligns at x1~474.5, credit at
~573.7). So every amount here is claimed by a *band* -- an x-window tested
against the token's RIGHT edge, because money is right-aligned and the left
edge moves with the digit count. A number in no band is prose, not an amount.

The output record, one JSON object per line:

    {"transaction_date": "yyyy-mm-dd",      # always
     "transaction_time": "hh:mm:ss",        # only when the row prints one
     "debit_sum":  "0.00",                  # string, dot decimal, 2dp, unsigned
     "credit_sum": "302.50",
     "description": "..."}                  # continuation lines joined in

Amounts are strings so that nothing downstream re-floats them, and Decimal
throughout so that summing 40,000 rows is exact.
"""

import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

try:
    from typing import Dict, Iterator, List, Optional, Sequence, Tuple
except ImportError:  # pragma: no cover
    pass

CENT = Decimal('0.01')
ZERO = Decimal('0')

Band = tuple  # (low, high) inclusive, tested against a token's right edge


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------

class StatementError(Exception):
    """Anything that should stop the run with a readable message."""


class AmountFormatError(StatementError):
    pass


def _need(module, why):
    """Fail with the install line rather than a traceback 300 pages in."""
    raise StatementError(
        "statement_lib: %s is required %s.\n"
        "    python3 -m pip install %s" % (module, why, module))


# --------------------------------------------------------------------------
# bands
# --------------------------------------------------------------------------

def col(i):
    """One spreadsheet/csv column, 0-based. `debit=col(4)`."""
    return (float(i), float(i))


def cols(a, b):
    """A span of spreadsheet/csv columns, inclusive. `description=cols(2,3)`."""
    return (float(a), float(b))


def _in(band, x):
    return band is not None and band[0] <= x <= band[1]


# --------------------------------------------------------------------------
# number grammars
# --------------------------------------------------------------------------
# Styles are grammars, not replace-chains. That is the whole point: `618,80`
# is *rejected* by 'us' and `1,163.14` is *rejected* by 'eu', so a mis-declared
# format is an error on row 1 instead of a plausible wrong number on every row.
# A replace-chain would read `1.238,92` as 1.24 and never complain.

_US = re.compile(r'^-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$|^-?\d+(?:\.\d{1,2})?$')
_EU = re.compile(r'^-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$|^-?\d+(?:,\d{1,2})?$')

# A money-shaped token, for probing and for deciding what to even test.
MONEY_TOKEN = re.compile(r'^[-(]?\d[\d.,]*\)?$')


def _clean(tok):
    """Strip currency noise and turn accounting parens into a minus."""
    t = tok.strip().replace(' ', '').replace(' ', '')
    neg = t.startswith('(') and t.endswith(')')
    if neg:
        t = t[1:-1]
    t = t.strip('€$£')
    if t.endswith('-'):          # trailing-minus statements
        t, neg = t[:-1], True
    if neg and not t.startswith('-'):
        t = '-' + t
    return t


def parse_amount(token, style):
    """'1,163.14' + 'us' -> Decimal('1163.14'). Raises on the wrong grammar."""
    t = _clean(token)
    if style == 'us':
        if not _US.match(t):
            raise AmountFormatError("%r is not a us number (1,163.14)" % token)
        return Decimal(t.replace(',', ''))
    if style == 'eu':
        if not _EU.match(t):
            raise AmountFormatError("%r is not an eu number (1.238,92)" % token)
        return Decimal(t.replace('.', '').replace(',', '.'))
    raise StatementError("numbers must be 'us', 'eu' or 'auto', not %r" % style)


_HAS_CENTS = re.compile(r'[.,]\d{1,2}$')


def looks_like_amount(token, require_decimals=True):
    """Is this token money?

    `require_decimals` defaults True because a bare integer sitting in an
    amount band is, on this evidence, far more often a phone number or an
    account fragment than a sum: BT repeats `004 0264 30 8028 (BT)` in every
    page header, and `8028` lands squarely in its debit band. Statements that
    genuinely print whole amounts turn it off -- probe.py counts the
    decimal-less tokens so the choice is informed rather than guessed.
    """
    t = _clean(token)
    if not MONEY_TOKEN.match(token.strip()):
        return False
    if require_decimals and not _HAS_CENTS.search(t):
        return False
    return bool(_US.match(t)) or bool(_EU.match(t))


def sniff_numbers(tokens):
    """Pick the grammar that accepts more of a sample. Refuse a near-tie."""
    us = sum(1 for t in tokens if _US.match(_clean(t)))
    eu = sum(1 for t in tokens if _EU.match(_clean(t)))
    n = len(tokens)
    if not n:
        return 'us', 'no amount tokens sampled; defaulted to us'
    if us == eu:
        raise StatementError(
            "cannot tell us from eu numbers (%d/%d match both). "
            "Declare numbers='us' or numbers='eu' explicitly." % (us, n))
    style = 'us' if us > eu else 'eu'
    return style, '%d/%d match %s, %d match %s' % (
        max(us, eu), n, style, min(us, eu), 'eu' if style == 'us' else 'us')


def money(value):
    """Decimal -> '1163.14'. Unsigned, 2dp, no separators."""
    q = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    if q < ZERO:
        q = -q
    return str(q)


# --------------------------------------------------------------------------
# dates and times
# --------------------------------------------------------------------------

_SPLIT = re.compile(r'[./-]')
_MONTHS = ZERO  # placeholder so linters see the module-level name is intentional


def parse_date(token, order='dmy'):
    """'01/04/2026' + 'dmy' -> '2026-04-01'. Returns None if it is not a date."""
    parts = _SPLIT.split(token.strip())
    if len(parts) != 3:
        return None
    try:
        a, b, c = (int(p) for p in parts)
    except ValueError:
        return None
    if order == 'dmy':
        d, m, y = a, b, c
    elif order == 'mdy':
        m, d, y = a, b, c
    elif order == 'ymd':
        y, m, d = a, b, c
    else:
        raise StatementError("date_order must be dmy, mdy or ymd, not %r" % order)
    if y < 100:
        y += 2000
    if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2200):
        return None
    return '%04d-%02d-%02d' % (y, m, d)


# --------------------------------------------------------------------------
# the spec
# --------------------------------------------------------------------------

@dataclass
class ColumnSpec:
    # --- where the money is -------------------------------------------------
    debit: Optional[Band] = None
    credit: Optional[Band] = None
    amount: Optional[Band] = None            # one signed column instead
    balance: Optional[Band] = None           # declared so it is DISCARDED
    ignore: Sequence = ()
    amount_sign: str = 'negative_is_debit'
    sign_band: Optional[Band] = None
    sign_re_debit: Optional[str] = None
    sign_re_credit: Optional[str] = None
    allow_both_sides: bool = False
    require_decimals: bool = True

    # --- which currency the row is in ---------------------------------------
    # Amounts without a currency are not money, they are numbers. Most
    # statements name it once in the account header (`currency_default`); some
    # print it per row in a column of its own (`currency`), and those are the
    # ones that can hold more than one. When neither says, the record carries
    # 'XXX' -- ISO 4217's own code for "no currency" -- so the field is always
    # present and a reader can tell "unknown" from "assumed".
    currency: Optional[Band] = None
    currency_default: Optional[str] = None
    currency_re: str = r'^[A-Za-z]{3}$|^[\u20ac\u0024\u00a3\u20b4\u20bd]$'

    # --- where the text is --------------------------------------------------
    description: Band = (0.0, 1e9)
    date: Optional[Band] = None
    desc_join: str = ' '

    # --- formats ------------------------------------------------------------
    numbers: str = 'auto'
    date_re: str = r'(\d{1,4}[./-]\d{1,2}[./-]\d{1,4})'
    date_order: str = 'dmy'
    date_carry: bool = True
    time_re: Optional[str] = None

    # --- where the table begins and ends ------------------------------------
    start_re: Optional[str] = None
    stop_re: Optional[str] = None
    start_per_page: bool = False
    skip_rows: Sequence = ()
    skip_re: Optional[str] = None

    # --- row geometry -------------------------------------------------------
    row_start: str = 'amount'
    y_tolerance: float = 2.0
    x_tolerance: float = 2.0
    space_thousands: bool = True

    # --- tabular only -------------------------------------------------------
    sheet: Optional[str] = None
    header_rows: int = 0
    delimiter: Optional[str] = None
    encoding: str = 'utf-8-sig'

    # --- escape hatches: bank-local logic, kept out of the shared library ----
    # Each is a plain callable set in the per-bank script. Reach for these
    # BEFORE editing statement_lib: a quirk that lives in one bank's file
    # cannot regress another bank, and needs no regression run to be safe.
    cell_map: Optional[object] = None      # f(Cell) -> Cell | None   before banding
    row_filter: Optional[object] = None    # f(Row) -> bool           False drops the row
    post: Optional[object] = None          # f(Transaction) -> Txn | None  before writing

    # --- reconciliation -----------------------------------------------------
    expect_debit_total: Optional[str] = None
    expect_credit_total: Optional[str] = None
    expect_count: Optional[int] = None
    strict_bands: bool = True

    def validate(self):
        if not (self.debit or self.credit or self.amount):
            raise StatementError(
                "declare debit/credit bands, or a single signed `amount` band")
        if self.amount and (self.debit or self.credit):
            raise StatementError(
                "`amount` is the one-signed-column shape; do not also set debit/credit")
        if (self.sign_re_debit or self.sign_re_credit) and not self.amount:
            raise StatementError(
                'sign_re_debit/sign_re_credit describe a marker column beside a '
                'single `amount` band; with separate debit/credit bands the '
                'column already says the side')
        if self.row_start not in ('amount', 'date', 'date_and_amount'):
            raise StatementError("row_start must be amount, date or date_and_amount")
        if not (0.3 <= self.y_tolerance <= 6.0):
            raise StatementError(
                "y_tolerance %r is outside the workable window 0.3-6.0 pt: too "
                "small orphans a row's own numbers, too large merges two rows"
                % self.y_tolerance)


# --------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------

@dataclass
class Cell:
    text: str
    x0: float
    x1: float


@dataclass
class Row:
    cells: List
    page: int
    top: float = 0.0

    @property
    def text(self):
        return ' '.join(c.text for c in self.cells)


SYMBOL_ISO = {'\u20ac': 'EUR', '$': 'USD', '\u00a3': 'GBP',
              '\u20b4': 'UAH', '\u20bd': 'RUB'}

UNKNOWN_CURRENCY = 'XXX'   # ISO 4217: "no currency". Not a guess, and says so.


@dataclass
class Transaction:
    transaction_date: str
    debit_sum: str
    credit_sum: str
    description: str
    currency: str = UNKNOWN_CURRENCY
    transaction_time: Optional[str] = None
    page: int = 0

    def to_json(self):
        o = {'transaction_date': self.transaction_date}
        if self.transaction_time:
            o['transaction_time'] = self.transaction_time
        o['debit_sum'] = self.debit_sum
        o['credit_sum'] = self.credit_sum
        o['currency'] = self.currency
        o['description'] = self.description
        return json.dumps(o, ensure_ascii=False)


# --------------------------------------------------------------------------
# row sources -- one per input format, all yielding Row
# --------------------------------------------------------------------------

_GRP_HEAD = re.compile(r'^-?\d{1,3}$')
_GRP_TAIL = re.compile(r'^\d{3}(?:[.,]\d{1,2})?$')


def join_spaced_numbers(cells, max_gap=4.0):
    """Rejoin `1 234.56` after the space split it into `1` and `234.56`.

    A space is a legitimate thousands separator across much of Europe, and
    pdfplumber splits words on it no matter the x_tolerance -- it is a real
    space character, not a gap. Left alone, `-2 633.00` is claimed as 633.00
    and the row is wrong by two and a half thousand while still looking like
    money.

    The rule is deliberately narrow: a 1-3 digit head, then strictly 3-digit
    groups, each within `max_gap` points of the last. `MCC 5499` cannot match
    (four digits), and a merged token still has to pass the money grammar
    before anything treats it as an amount.
    """
    out = []
    i = 0
    while i < len(cells):
        c = cells[i]
        if _GRP_HEAD.match(c.text):
            text, x1, j = c.text, c.x1, i + 1
            while j < len(cells) and _GRP_TAIL.match(cells[j].text) \
                    and cells[j].x0 - x1 <= max_gap:
                text += cells[j].text
                x1 = cells[j].x1
                j += 1
                if '.' in text or ',' in text:
                    break
            if j > i + 1:
                out.append(Cell(text, c.x0, x1))
                i = j
                continue
        out.append(c)
        i += 1
    return out


def _cluster(words, tol):
    """Words -> rows, greedy, anchored on the first word's top.

    Anchored rather than a running mean: a mean drifts across a wide row and
    can swallow the line below. The tolerance is load-bearing -- BT prints a
    row's label at top 350.777 and its own numbers at 350.437, so anything
    under ~0.35 splits a row away from its amounts, and the numbers then form
    a keyword-less row that no skip list can recognise. That is how a phantom
    transaction gets minted once per day.
    """
    out = []
    cur = []
    anchor = None
    for w in words:
        if anchor is None:
            cur, anchor = [w], w['top']
        elif w['top'] - anchor <= tol:
            cur.append(w)
        else:
            out.append((anchor, sorted(cur, key=lambda x: x['x0'])))
            cur, anchor = [w], w['top']
    if cur:
        out.append((anchor, sorted(cur, key=lambda x: x['x0'])))
    return out


def pdf_rows(spec, path, pages=None, on_page=None):
    try:
        import pdfplumber
    except ImportError:
        _need('pdfplumber', 'to read PDF statements')
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            if pages and i not in pages:
                continue
            if on_page:
                on_page(i, total)
            words = page.extract_words(x_tolerance=spec.x_tolerance,
                                       y_tolerance=1.0,
                                       keep_blank_chars=False)
            words = [w for w in words if 0 <= w['top'] <= page.height]
            words.sort(key=lambda w: (w['top'], w['x0']))
            for top, group in _cluster(words, spec.y_tolerance):
                cells = [Cell(w['text'], w['x0'], w['x1']) for w in group]
                if spec.space_thousands:
                    cells = join_spaced_numbers(cells)
                yield Row(cells, i, top)
            # pdfplumber caches every char of every page; without this a
            # 500-page statement grows to gigabytes instead of staying flat.
            page.flush_cache()
            try:
                page.get_textmap.cache_clear()
            except AttributeError:
                pass


def _tabular_row(values, page):
    cells = []
    for i, v in enumerate(values):
        if v is None:
            continue
        s = str(v).strip()
        if s:
            cells.append(Cell(s, float(i), float(i)))
    return Row(cells, page)


def xlsx_rows(spec, path, pages=None, on_page=None):
    try:
        import openpyxl
    except ImportError:
        _need('openpyxl', 'to read .xlsx statements')
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[spec.sheet] if spec.sheet else wb.worksheets[0]
        for n, values in enumerate(ws.iter_rows(values_only=True)):
            if n < spec.header_rows:
                continue
            if on_page and n % 5000 == 0:
                on_page(n, 0)
            yield _tabular_row(values, 1)
    finally:
        wb.close()


def csv_rows(spec, path, pages=None, on_page=None):
    delim = spec.delimiter
    with open(path, 'r', encoding=spec.encoding, newline='') as fh:
        if delim is None:
            sample = fh.read(8192)
            fh.seek(0)
            try:
                delim = csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
            except Exception:
                delim = ','
        for n, values in enumerate(csv.reader(fh, delimiter=delim)):
            if n < spec.header_rows:
                continue
            if on_page and n % 5000 == 0:
                on_page(n, 0)
            yield _tabular_row(values, 1)


def text_rows(spec, path, pages=None, on_page=None):
    """Fixed-width text: bands are character offsets."""
    tok = re.compile(r'\S+')
    with open(path, 'r', encoding=spec.encoding, errors='replace') as fh:
        for n, line in enumerate(fh):
            if n < spec.header_rows:
                continue
            if on_page and n % 5000 == 0:
                on_page(n, 0)
            cells = [Cell(m.group(0), float(m.start()), float(m.end()))
                     for m in tok.finditer(line.rstrip('\n'))]
            if spec.space_thousands:
                cells = join_spaced_numbers(cells, max_gap=1.5)
            if cells:
                yield Row(cells, 1)


def rows_for(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return pdf_rows
    if ext in ('.xlsx', '.xlsm'):
        return xlsx_rows
    if ext in ('.csv', '.tsv'):
        return csv_rows
    if ext == '.xls':
        raise StatementError(
            "legacy .xls cannot be read here: xlrd is not installed and there "
            "is no LibreOffice to convert it.\n"
            "Ask for the file re-saved as .xlsx or .csv -- that is the whole fix.")
    return text_rows


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

@dataclass
class Counts:
    transactions: int = 0
    pages: int = 0
    continuations: int = 0
    skipped: int = 0
    before_start: int = 0
    orphan: int = 0
    both_sides: int = 0
    sign_flips: int = 0
    carried_dates: int = 0
    unclaimed: int = 0
    markers_read: int = 0
    unknown_currency: int = 0
    currencies: Dict = field(default_factory=dict)
    unclaimed_samples: List = field(default_factory=list)
    debit_total: Decimal = ZERO
    credit_total: Decimal = ZERO
    signed_debit: Decimal = ZERO
    signed_credit: Decimal = ZERO
    stopped_at: Optional[int] = None
    number_style: str = ''
    style_note: str = ''
    warnings: List = field(default_factory=list)
    per_page: Dict = field(default_factory=dict)


def _resolve_style(spec, path, pages):
    if spec.numbers in ('us', 'eu'):
        return spec.numbers, 'declared'
    sample = []
    src = rows_for(path)
    for row in src(spec, path, pages):
        for c in row.cells:
            if not MONEY_TOKEN.match(c.text):
                continue
            if _in(spec.debit, c.x1) or _in(spec.credit, c.x1) or _in(spec.amount, c.x1):
                sample.append(c.text)
        if len(sample) >= 200:
            break
    return sniff_numbers(sample)


def transactions(spec, path, pages=None, counts=None, on_page=None):
    """The generator. One open transaction at a time; nothing accumulates."""
    spec.validate()
    counts = counts if counts is not None else Counts()
    style, note = _resolve_style(spec, path, pages)
    counts.number_style, counts.style_note = style, note

    start_re = re.compile(spec.start_re) if spec.start_re else None
    stop_re = re.compile(spec.stop_re) if spec.stop_re else None
    skip_re = re.compile(spec.skip_re) if spec.skip_re else None
    cur_re = re.compile(spec.currency_re)
    sign_d = re.compile(spec.sign_re_debit) if spec.sign_re_debit else None
    sign_c = re.compile(spec.sign_re_credit) if spec.sign_re_credit else None
    date_re = re.compile(spec.date_re)
    time_re = re.compile(spec.time_re) if spec.time_re else None
    skips = [s.lower() for s in spec.skip_rows]

    armed = start_re is None
    last_date = None
    open_txn = None
    page_seen = None
    src = rows_for(path)

    def close():
        if open_txn is None:
            return None
        open_txn.description = re.sub(r'\s+', ' ', open_txn.description).strip()
        if spec.post is not None:
            return spec.post(open_txn)
        return open_txn

    for row in src(spec, path, pages, on_page):
        if row.page != page_seen:
            page_seen = row.page
            counts.pages += 1
            counts.per_page.setdefault(row.page, 0)
            if spec.start_per_page and start_re is not None:
                armed = False

        if spec.cell_map is not None:
            mapped = []
            for c in row.cells:
                c2 = spec.cell_map(c)
                if c2 is not None:
                    mapped.append(c2)
            row = Row(mapped, row.page, row.top)
        if spec.row_filter is not None and not spec.row_filter(row):
            counts.skipped += 1
            done = close()
            if done is not None:
                yield done
                open_txn = None
            continue

        text = row.text

        if stop_re is not None and stop_re.search(text):
            done = close()
            if done is not None:
                yield done
                open_txn = None
            counts.stopped_at = row.page
            return

        if not armed:
            if start_re.search(text):
                armed = True
            counts.before_start += 1
            continue

        low = text.lower()
        if (skips and any(s in low for s in skips)) or (skip_re and skip_re.search(text)):
            # A skipped row also CLOSES the open transaction, so a ledger line
            # can never be absorbed as somebody's description.
            done = close()
            if done is not None:
                yield done
                open_txn = None
            counts.skipped += 1
            continue

        # --- claim the numbers -------------------------------------------
        deb = cred = None
        claimed_any = False
        unclaimed_here = []

        # A statement with one unsigned amount column says the side in a
        # marker column instead -- `D`/`C`, `Dr`/`Cr`, `+`/`-`. Read it before
        # the amounts, so the amount branch has an answer to consult.
        marker = None
        if sign_d is not None or sign_c is not None:
            for c in row.cells:
                if spec.sign_band is not None and not _in(spec.sign_band, c.x1):
                    continue
                if sign_d is not None and sign_d.match(c.text.strip()):
                    marker = 'debit'
                    break
                if sign_c is not None and sign_c.match(c.text.strip()):
                    marker = 'credit'
                    break
        for c in row.cells:
            if not looks_like_amount(c.text, spec.require_decimals):
                continue
            if _in(spec.debit, c.x1):
                deb = parse_amount(c.text, style); claimed_any = True
            elif _in(spec.credit, c.x1):
                cred = parse_amount(c.text, style); claimed_any = True
            elif _in(spec.amount, c.x1):
                v = parse_amount(c.text, style); claimed_any = True
                if marker == 'debit':
                    deb = abs(v)
                elif marker == 'credit':
                    cred = abs(v)
                else:
                    neg_is_debit = spec.amount_sign == 'negative_is_debit'
                    if (v < ZERO) == neg_is_debit:
                        deb = abs(v)
                    else:
                        cred = abs(v)
            elif _in(spec.balance, c.x1) or any(_in(b, c.x1) for b in spec.ignore):
                pass
            elif _in(spec.description, c.x1) or _in(spec.date, c.x1):
                pass
            else:
                unclaimed_here.append(c.text)

        # A negative inside a one-sided column belongs on the other side:
        # Exim's "Suma neutilizata credit -142,646.85" sits in the credit band
        # and its running balance falls, so it is economically a debit. The
        # move is counted, never silent.
        counts.signed_debit += (deb or ZERO)
        counts.signed_credit += (cred or ZERO)
        if deb is not None and deb < ZERO:
            cred, deb = (cred or ZERO) + (-deb), None
            counts.sign_flips += 1
        if cred is not None and cred < ZERO:
            deb, cred = (deb or ZERO) + (-cred), None
            counts.sign_flips += 1

        if marker is not None:
            counts.markers_read += 1
        cur = None
        if spec.currency is not None:
            for c in row.cells:
                if not _in(spec.currency, c.x1):
                    continue
                t = c.text.strip().strip('.,;:')
                if cur_re.match(t):
                    cur = SYMBOL_ISO.get(t, t.upper())
                    break

        has_amount = deb is not None or cred is not None
        dtok = None
        for c in row.cells:
            if spec.date is not None and not _in(spec.date, c.x1):
                continue
            m = date_re.search(c.text)
            if m:
                d = parse_date(m.group(1), spec.date_order)
                if d:
                    dtok = d
                    break
        if dtok is None:
            m = date_re.search(text)
            if m:
                dtok = parse_date(m.group(1), spec.date_order)

        if spec.row_start == 'amount':
            is_new = has_amount
        elif spec.row_start == 'date':
            is_new = dtok is not None
        else:
            is_new = has_amount and dtok is not None

        desc = ' '.join(c.text for c in row.cells if _in(spec.description, c.x1))

        if is_new:
            done = close()
            if done is not None:
                yield done
            if dtok is None and spec.date_carry:
                dtok = last_date
                if dtok:
                    counts.carried_dates += 1
            if dtok is None:
                counts.orphan += 1
                open_txn = None
                continue
            last_date = dtok

            if deb is not None and cred is not None and not spec.allow_both_sides:
                # Both sides at once is what a turnover/ledger row looks like.
                counts.both_sides += 1
                counts.warnings.append(
                    'page %d: two amounts on one row (%s / %s) -- rejected: %s'
                    % (row.page, money(deb), money(cred), text[:70]))
                open_txn = None
                continue

            tm = None
            if time_re is not None:
                m = time_re.search(text)
                if m:
                    tm = m.group(0)

            if spec.strict_bands and unclaimed_here:
                counts.unclaimed += len(unclaimed_here)
                if len(counts.unclaimed_samples) < 5:
                    counts.unclaimed_samples.append(
                        'page %d: %s' % (row.page, ', '.join(unclaimed_here)))

            open_txn = Transaction(
                currency=(cur or spec.currency_default or UNKNOWN_CURRENCY).upper(),
                transaction_date=dtok,
                debit_sum=money(deb) if deb is not None else '0.00',
                credit_sum=money(cred) if cred is not None else '0.00',
                description=desc, transaction_time=tm, page=row.page)
            counts.transactions += 1
            counts.currencies[open_txn.currency] = \
                counts.currencies.get(open_txn.currency, 0) + 1
            if open_txn.currency == UNKNOWN_CURRENCY:
                counts.unknown_currency += 1
            counts.per_page[row.page] = counts.per_page.get(row.page, 0) + 1
            counts.debit_total += (deb or ZERO)
            counts.credit_total += (cred or ZERO)
        else:
            if open_txn is not None:
                if spec.currency is not None and \
                        open_txn.currency == UNKNOWN_CURRENCY:
                    for c in row.cells:
                        if _in(spec.currency, c.x1):
                            t = c.text.strip().strip('.,;:')
                            if cur_re.match(t):
                                open_txn.currency = SYMBOL_ISO.get(t, t.upper())
                                break
                if time_re is not None and not open_txn.transaction_time:
                    m = time_re.search(text)
                    if m:
                        open_txn.transaction_time = m.group(0)
                        if desc:
                            desc = desc.replace(m.group(0), '').strip()
                if desc:
                    open_txn.description += spec.desc_join + desc
                counts.continuations += 1
            else:
                counts.orphan += 1

    done = close()
    if done is not None:
        yield done


# --------------------------------------------------------------------------
# output and reconciliation
# --------------------------------------------------------------------------

def write_jsonl(txns, out_path, limit=None):
    n = 0
    with open(out_path, 'w', encoding='utf-8') as fh:
        for t in txns:
            fh.write(t.to_json() + '\n')
            n += 1
            if n % 1000 == 0:
                fh.flush()
            if limit and n >= limit:
                break
    return n


def _expect(tok, style):
    if tok is None:
        return None
    if isinstance(tok, Decimal):
        return tok
    return parse_amount(str(tok), style)


def reconcile(counts, spec):
    """Compare our totals with what the statement printed about itself.

    Two conventions exist and statements do not agree on which they use. A
    negative amount sitting in a one-sided column -- Exim's
    `Suma neutilizata credit -142,646.85` -- is economically a debit: the
    running balance falls. So the JSONL moves it, which is what a reader of
    the rows wants. But Exim's own `Total Rulaje` counts it as a negative
    credit and never moves it, so our sided totals cannot match its printed
    ones by construction.

    Rather than pick a convention and call the other a bug, check both and say
    which one the statement used. Guessing here would mean either a wrong
    JSONL or a reconciliation that can never pass.
    """
    style = counts.number_style or 'us'
    want_d = _expect(spec.expect_debit_total, style)
    want_c = _expect(spec.expect_credit_total, style)

    def check(gd, gc):
        ok = True
        if want_d is not None:
            ok = ok and abs(gd - want_d) < CENT
        if want_c is not None:
            ok = ok and abs(gc - want_c) < CENT
        return ok

    sided_ok = check(counts.debit_total, counts.credit_total)
    signed_ok = check(counts.signed_debit, counts.signed_credit)
    use_signed = (not sided_ok) and signed_ok and counts.sign_flips > 0

    gd = counts.signed_debit if use_signed else counts.debit_total
    gc = counts.signed_credit if use_signed else counts.credit_total

    lines = []
    ok = True
    for name, got, want in (('debit', gd, want_d), ('credit', gc, want_c)):
        if want is None:
            continue
        delta = got - want
        good = abs(delta) < CENT
        ok = ok and good
        lines.append('  %-7s %16s   expected %16s   delta %12s   %s'
                     % (name, money(got), money(want), money(delta),
                        'OK' if good else 'FAIL'))
    if use_signed:
        lines.append('  (matched on the signed convention: %d negative amounts '
                     'kept in their own\n   column for the totals, moved to the '
                     'other side in the JSONL)' % counts.sign_flips)
    if spec.expect_count is not None:
        good = counts.transactions == spec.expect_count
        ok = ok and good
        lines.append('  %-7s %16d   expected %16d   delta %12d   %s'
                     % ('count', counts.transactions, spec.expect_count,
                        counts.transactions - spec.expect_count,
                        'OK' if good else 'FAIL'))
    return ok, lines


def report(counts, spec, src, out, stream=sys.stderr):
    w = stream.write
    w('\n%s  ->  %s\n' % (src, out))
    w('  pages read           %8d%s\n' % (
        counts.pages,
        '      stopped at page %d' % counts.stopped_at if counts.stopped_at else ''))
    w('  transactions         %8d\n' % counts.transactions)
    w('  continuation rows    %8d\n' % counts.continuations)
    w('  skipped (noise)      %8d\n' % counts.skipped)
    w('  discarded pre-start  %8d\n' % counts.before_start)
    w('  orphan rows          %8d\n' % counts.orphan)
    w('  two-amount rows      %8d\n' % counts.both_sides)
    w('  sign-flips           %8d\n' % counts.sign_flips)
    w('  dates carried        %8d of %d\n' % (counts.carried_dates, counts.transactions))
    w('  unclaimed numbers    %8d\n' % counts.unclaimed)
    if spec.sign_re_debit or spec.sign_re_credit:
        w('  sign markers read    %8d of %d\n'
          % (counts.markers_read, counts.transactions))
        if counts.markers_read < counts.transactions:
            w('  !! rows without a marker fell back to the sign of the amount;\n'
              '     if the column is unsigned that silently made them all one side.\n')
    for s in counts.unclaimed_samples:
        w('      %s\n' % s)
    w('  number format        %8s   (%s)\n' % (counts.number_style, counts.style_note))
    w('  currency             %8s\n'
      % ', '.join('%s x%d' % kv for kv in sorted(counts.currencies.items(),
                                                 key=lambda k: -k[1])))
    if counts.unknown_currency:
        w('  !! %d rows carry XXX -- the currency was never named. Set\n'
          '     currency_default from the account header, or a `currency` band\n'
          '     if the statement prints it per row.\n' % counts.unknown_currency)
    if len(counts.currencies) > 1:
        w('  !! more than one currency: the totals gate is only meaningful per\n'
          '     currency, so reconcile each one separately.\n')
    w('  debit total  %16s\n' % money(counts.debit_total))
    w('  credit total %16s\n' % money(counts.credit_total))

    interior = [p for p, n in sorted(counts.per_page.items()) if n == 0]
    if interior:
        w('  !! pages with no transactions: %s\n' % interior[:10])
    for m in counts.warnings[:10]:
        w('  !! %s\n' % m)
    if counts.unclaimed:
        w('  !! numeric tokens landed in no declared band -- a column is\n'
          '     unaccounted for. Declare it (balance=/ignore=) or widen a band.\n')

    ok, lines = reconcile(counts, spec)
    if lines:
        w('\nRECONCILIATION\n')
        for ln in lines:
            w(ln + '\n')
        w('  %s\n' % ('reconciles to the cent' if ok else 'DOES NOT RECONCILE'))
        return ok
    w('\n  no expected totals declared -- this run is UNVERIFIED.\n'
      '  Set expect_debit_total / expect_credit_total from the statement.\n')
    return True


def _parse_pages(text):
    if not text:
        return None
    out = set()
    for part in text.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def run(spec, argv=None):
    """The whole program: parse argv, stream, write, report, return exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        sys.stderr.write(
            'usage: %s <statement> [-o out.jsonl] [--pages 1-3] [--limit N] [--quiet]\n'
            % os.path.basename(sys.argv[0]))
        return 2
    src = argv[0]
    out = None
    pages = None
    limit = None
    quiet = False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ('-o', '--out'):
            i += 1; out = argv[i]
        elif a == '--pages':
            i += 1; pages = _parse_pages(argv[i])
        elif a == '--limit':
            i += 1; limit = int(argv[i])
        elif a == '--quiet':
            quiet = True
        else:
            sys.stderr.write('unknown argument %r\n' % a)
            return 2
        i += 1
    if out is None:
        out = os.path.splitext(src)[0] + '.jsonl'

    counts = Counts()

    def on_page(i, total):
        if not quiet and total and i % 250 == 0:
            sys.stderr.write('  ... page %d/%d\n' % (i, total))

    try:
        n = write_jsonl(transactions(spec, src, pages, counts, on_page), out, limit)
    except StatementError as e:
        sys.stderr.write('\n%s\n' % e)
        return 2
    if not quiet:
        ok = report(counts, spec, src, out, sys.stderr)
    else:
        ok, _ = reconcile(counts, spec)
    sys.stderr.write('\n%d transactions -> %s\n' % (n, out))
    return 0 if ok else 1
