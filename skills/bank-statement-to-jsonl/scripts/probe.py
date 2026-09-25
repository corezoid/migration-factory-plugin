#!/usr/bin/env python3
"""Look at one page of a statement and propose a ColumnSpec.

    python3 probe.py <statement>              # page 1 in full, plus a sample
    python3 probe.py <statement> --page 6     # a middle page: the steady state
    python3 probe.py <statement> --totals     # only what the statement says about itself

This is the only thing that reads the statement before the parser does, and it
is bounded on purpose: one page in full, and a capped sample of pages for the
column clustering. A 900-page file costs what a 25-page one costs.

It exists because the alternative is reading the statement into the run, and
the whole design is that a script reads it and you do not. One page carries
every fact a spec needs -- where the columns are, which number grammar is in
use, which date order, and whether a row's date is printed or inherited.

What it prints, and why each part is there:

  HEADER          the line it believes names the columns, scored rather than
                  first-match: Exim prints a decoy `Sold Initial: 0.00` thirty
                  lines above its real header.
  CLUSTERS        numeric right edges, grouped. Money is right-aligned, so a
                  column is a stack of x1 values; the count of each stack is
                  what tells a real column from a stray.
  ARITHMETIC      debit-count + credit-count == balance-count, when a balance
                  column exists. A band map that fails this is wrong before a
                  single row is parsed.
  UNCLAIMED       stacks that are not transaction columns -- summary blocks
                  above the table, totals below it. These are where the
                  phantom transactions come from, so each one gets a suggested
                  start_re/stop_re rather than a shrug.
  TOTALS          what the statement says about itself, to be pasted into
                  expect_debit_total / expect_credit_total.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from decimal import Decimal  # noqa: E402
from statement_lib import Cell, join_spaced_numbers  # noqa: E402
from statement_lib import (MONEY_TOKEN, _US, _EU, _clean, _HAS_CENTS,  # noqa: E402
                           StatementError, parse_date, parse_amount, money)

# Column names are advisory only -- the parser bands by geometry and never
# reads a header. This list exists so the probe can put a name beside a
# cluster; a statement in a language it does not cover still probes, still
# parses and still reconciles, it just prints clusters unnamed.
HEADER_WORDS = re.compile(
    r'(?:\b|(?<=[^\w]))('
    r'data|date|datum|fecha|descrie|detali|detail|descri|narrat|referin|'
    r'referen|debit|credit|suma|amount|valoare|value|sold|balance|balanta|'
    r'saldo|tranzact|transact|operat|document|explicat|'
    # Cyrillic: ru / uk
    r'дата|час|время|опис|описание|деталі|детали|призначення|назначение|'
    r'дебет|кредит|сума|сумма|залишок|остаток|баланс|валюта|курс|комісі|'
    r'комисси|операці|операци|рахунок|счет|рух|коштів|надходж|витрат|'
    r'зарахув|кешбек|документ|признач'
    r')\w*', re.I | re.U)

# Not a validation list -- just the codes common enough to be worth spotting
# without being told. Anything else is found by reading the account header.
ISO = set(('UAH RON EUR USD GBP PLN CZK HUF BGN MDL TRY CHF SEK NOK DKK '
           'RUB KZT GEL AZN AMD BYN JPY CNY CAD AUD INR AED').split())

TOTAL_LINE = re.compile(
    r'(?:\b|(?<=[^\w]))('
    r'total|sold|rulaj|balance|closing|opening|brought\s+forward|'
    r'deschidere|inchidere|initial|final|'
    r'всього|итого|разом|підсум|итог|оборот|обороти|залишок|остаток|баланс|'
    r'сума\s+витрат|сума\s+зарахув|сумма\s+расход|сумма\s+зачисл|'
    r'вхідн|вихідн|входящ|исходящ|на\s+початок|на\s+кінець'
    r')', re.I | re.U)


def money_tokens(words, cents_only=True):
    """Money-shaped tokens. `cents_only` mirrors the parser's require_decimals:
    a bare integer is usually a page number, an account fragment or -- in BT's
    repeating page header -- a phone number whose `8028` lands in a debit band.
    """
    for w in words:
        if MONEY_TOKEN.match(w.text):
            t = _clean(w.text)
            if cents_only and not _HAS_CENTS.search(t):
                continue
            if _US.match(t) or _EU.match(t):
                yield w


def cluster(edges, tol):
    """Single-link cluster of x1 values."""
    if not edges:
        return []
    edges = sorted(edges)
    out, cur = [], [edges[0]]
    for e in edges[1:]:
        if e - cur[-1] <= tol:
            cur.append(e)
        else:
            out.append(cur)
            cur = [e]
    out.append(cur)
    return out


def rows_of(page, tol=2.0):
    ws = [w for w in page.extract_words() if 0 <= w['top'] <= page.height]
    ws.sort(key=lambda w: (w['top'], w['x0']))
    out, cur, anchor = [], [], None

    def emit(group):
        cells = [Cell(w['text'], w['x0'], w['x1'])
                 for w in sorted(group, key=lambda x: x['x0'])]
        out.append(join_spaced_numbers(cells))

    for w in ws:
        if anchor is None:
            cur, anchor = [w], w['top']
        elif w['top'] - anchor <= tol:
            cur.append(w)
        else:
            emit(cur)
            cur, anchor = [w], w['top']
    if cur:
        emit(cur)
    return out


def score_header(row):
    """A header row names columns and carries no money.

    Scored rather than first-match because the first line containing a column
    keyword is very often not the header: Exim's account block prints
    `Sold Initial: 0.00` and `Rulaj debit: 478,805.56` well above the table.
    A line with money in it is a summary, not a header -- hence the penalty.
    """
    text = ' '.join(w.text for w in row)
    hits = len(set(m.group(0).lower()[:6] for m in HEADER_WORDS.finditer(text)))
    if hits < 2:
        return 0
    cash = sum(1 for _ in money_tokens(row))
    return hits * 10 - cash * 25 - (5 if ':' in text else 0)


def sample_pages(n, want):
    if n <= want:
        return list(range(1, n + 1))
    picks = set([1, 2, 3, n - 1, n])
    step = max(1, n // max(1, want - len(picks)))
    picks.update(range(1, n + 1, step))
    return sorted(p for p in picks if 1 <= p <= n)[:want]


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    path = argv[0]
    page_no, tol, want, only_totals = 1, 2.5, 20, False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--page':
            i += 1; page_no = int(argv[i])
        elif a == '--tol':
            i += 1; tol = float(argv[i])
        elif a == '--sample':
            i += 1; want = int(argv[i])
        elif a == '--totals':
            only_totals = True
        else:
            sys.stderr.write('unknown argument %r\n' % a); return 2
        i += 1

    if not path.lower().endswith('.pdf'):
        return probe_tabular(path)

    try:
        import pdfplumber
    except ImportError:
        raise StatementError('probe: pdfplumber is required.\n'
                             '    python3 -m pip install pdfplumber')

    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        picks = sample_pages(n, want)
        page_no = min(page_no, n)

        page_w = float(pdf.pages[0].width)
        all_edges, cash_tokens, dates, edge_vals = [], [], [], []
        date_edges, time_toks = [], []
        cur_hits = {}
        header, header_pg, header_top = None, None, None
        best = 0
        totals, amount_rows, date_rows = [], 0, 0
        decimal_less = 0

        for p in picks:
            page = pdf.pages[p - 1]
            for row in rows_of(page):
                text = ' '.join(w.text for w in row)
                cash = list(money_tokens(row))
                for w in cash:
                    all_edges.append(w.x1)
                    cash_tokens.append(w.text)
                    edge_vals.append((w.x1, w.text))
                    if not _HAS_CENTS.search(_clean(w.text)):
                        decimal_less += 1
                s = score_header(row)
                if s > best:
                    best, header, header_pg, header_top = s, row, p, 0.0
                if cash:
                    amount_rows += 1
                if re.match(r'^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$', row[0].text):
                    date_rows += 1
                for m in re.finditer(r'\b(\d{1,4}[./-]\d{1,2}[./-]\d{1,4})\b', text):
                    dates.append(m.group(1))
                for c in row:
                    if re.match(r'^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$', c.text):
                        date_edges.append(c.x1)
                    elif re.match(r'^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$', c.text):
                        time_toks.append(c.text)
                    elif re.match(r'^[A-Z]{3}$', c.text) and c.text in ISO:
                        cur_hits.setdefault(c.text, []).append(c.x1)
                if TOTAL_LINE.search(text) and cash:
                    totals.append((p, text.strip()[:96]))
            page.flush_cache()

        w = sys.stdout.write
        w('\nfile      %s   %d pages   sampled %d\n' % (
            os.path.basename(path), n, len(picks)))

        if only_totals:
            w('\nTOTALS THE DOCUMENT PRINTS ABOUT ITSELF\n')
            for p, t in totals:
                w('  p%-3d %s\n' % (p, t))
            return 0

        # ---- the page the caller asked to see, in full --------------------
        page = pdf.pages[page_no - 1]
        w('\nPAGE %d, as rows  (x1 in brackets on money tokens)\n' % page_no)
        for row in rows_of(page)[:45]:
            parts = []
            for c in row:
                if MONEY_TOKEN.match(c.text) and _HAS_CENTS.search(_clean(c.text)):
                    parts.append('%s[%.0f]' % (c.text, c.x1))
                else:
                    parts.append(c.text)
            line = ' '.join(parts)
            w('  %s\n' % line[:150])
        page.flush_cache()

        # ---- header -------------------------------------------------------
        if header:
            w('\nHEADER CANDIDATE   page %d\n  ' % (header_pg,))
            w(' '.join('%s[%.0f-%.0f]' % (x.text, x.x0, x.x1)
                       for x in header) + '\n')

        us0 = sum(1 for t in cash_tokens if _US.match(_clean(t)))
        eu0 = sum(1 for t in cash_tokens if _EU.match(_clean(t)))
        style_guess = 'us' if us0 >= eu0 else 'eu'

        # ---- clusters -----------------------------------------------------
        groups = cluster(all_edges, tol)
        groups = [g for g in groups if len(g) >= 2]
        w('\nNUMERIC RIGHT-EDGE CLUSTERS            (tol %.1f pt)\n' % tol)
        w('  id     x1 range          n    nearest header\n')
        named = []
        for k, g in enumerate(groups):
            lo, hi = min(g), max(g)
            name = ''
            if header:
                cand = [x for x in header if x.x1 >= lo - 120]
                if cand:
                    name = min(cand, key=lambda x: abs(x.x1 - hi)).text
            named.append((chr(65 + k), lo, hi, len(g), name))
            w('  %-4s %7.1f - %-7.1f %5d    %s\n' % (chr(65 + k), lo, hi, len(g), name))

        # ---- arithmetic ---------------------------------------------------
        _c = [t for t in named if t[1] >= page_w * 0.55] or named
        _tn = max(t[3] for t in _c) if _c else 0
        _big = sorted([t for t in _c if t[3] >= max(3, _tn * 0.15)],
                      key=lambda t: t[1])
        if len(_big) == 3:
            a, b, c = _big
            if a[3] + b[3] == c[3]:
                w('\n  ARITHMETIC  %s(%d) + %s(%d) = %s(%d)  -- one amount plus one\n'
                  '              balance per row: the band map is self-consistent.\n'
                  % (a[0], a[3], b[0], b[3], c[0], c[3]))

        # ---- formats ------------------------------------------------------
        us = sum(1 for t in cash_tokens if _US.match(_clean(t)))
        eu = sum(1 for t in cash_tokens if _EU.match(_clean(t)))
        style = 'us' if us > eu else 'eu'
        w('\nNUMBER FORMAT   %s     (%d/%d match us, %d match eu)\n'
          % (style, us, len(cash_tokens), eu))
        if decimal_less:
            w('  %d money-shaped tokens carry no decimals -- keep\n'
              '  require_decimals=True unless this statement prints whole amounts.\n'
              % decimal_less)

        seps = {}
        for d in dates:
            s = re.search(r'[./-]', d)
            if s:
                seps[s.group(0)] = seps.get(s.group(0), 0) + 1
        if seps:
            sep = max(seps, key=seps.get)
            same = [d for d in dates if sep in d]
            order = 'dmy'
            for d in same:
                parts = re.split(r'[./-]', d)
                if len(parts) == 3 and len(parts[0]) == 4:
                    order = 'ymd'; break
                if len(parts) == 3 and int(parts[0]) > 12:
                    order = 'dmy'; break
                if len(parts) == 3 and int(parts[1]) > 12:
                    order = 'mdy'; break
            esc = '\\.' if sep == '.' else sep
            w("DATE FORMAT     %s   %d hits   -> date_re=r'(\\d{2}%s\\d{2}%s\\d{4})', "
              "date_order='%s'\n" % (sep.join(['dd', 'mm', 'yyyy']), len(dates),
                                     esc, esc, order))

        if cur_hits:
            w('\nCURRENCY\n')
            for code, xs in sorted(cur_hits.items(), key=lambda k: -len(k[1])):
                lo, hi = min(xs), max(xs)
                w('  %s  x1 %.1f-%-6.1f  %d rows\n' % (code, lo, hi, len(xs)))
            top = max(cur_hits.items(), key=lambda k: len(k[1]))
            if len(cur_hits) == 1:
                w("  one code, printed per row -> currency = (%d, %d)\n"
                  "  (or simply currency_default = '%s' if it never varies)\n"
                  % (int(min(top[1]) - 6), int(max(top[1]) + 6), top[0]))
            else:
                w('  more than one code appears -- if they vary per row this is a\n'
                  '  multi-currency statement: band it, and reconcile per currency.\n')
        else:
            w('\nCURRENCY  no ISO code found on the sampled pages. Read it off the\n'
              "          account header and set currency_default; unset it stays XXX.\n")
        w('ROW START       rows with money: %d     rows beginning with a date: %d\n'
          % (amount_rows, date_rows))
        if date_rows > amount_rows * 1.3:
            w("  -> use row_start='amount'. Far more rows begin with a date than\n"
              "     carry money, so continuation lines repeat the date: splitting\n"
              "     on dates would cut every transaction into pieces.\n")

        # ---- unclaimed ----------------------------------------------------
        # A column is not always right-aligned: this statement's amounts are
        # left-set, so their right edges drift over ~9 pt and arrive as three
        # separate stacks. Merge neighbours before judging them, or the real
        # column loses every count comparison to the tidy ones beside it.
        merged = []
        for nm in sorted(named, key=lambda t: t[1]):
            if merged and nm[1] - merged[-1][2] <= 10.0:
                a = merged[-1]
                merged[-1] = (a[0], a[1], nm[2], a[3] + nm[3], a[4] or nm[4])
            else:
                merged.append(nm)

        def vals_in(lo, hi):
            out = []
            for x1, t in edge_vals:
                if lo <= x1 <= hi:
                    try:
                        out.append(parse_amount(t, style_guess))
                    except Exception:
                        pass
            return out

        scored = []
        for nm in merged:
            vs = vals_in(nm[1] - 1, nm[2] + 1)
            if not vs or len(set(vs)) == 1:
                continue          # a constant column is a fee column, not the amount
            neg = sum(-v for v in vs if v < 0)
            pos = sum(v for v in vs if v > 0)
            hit = ''
            for _p, line in totals:
                for mm in re.finditer(r'-?[\d  .,]*\d[.,]\d{2}', line):
                    try:
                        tv = parse_amount(mm.group(0).replace(' ', '').replace(' ', ''),
                                          style_guess)
                    except Exception:
                        continue
                    if neg and abs(abs(tv) - neg) < Decimal('0.01'):
                        hit = 'sum of negatives matches a printed total'
                    elif pos and abs(abs(tv) - pos) < Decimal('0.01'):
                        hit = 'sum of positives matches a printed total'
            scored.append((nm, len(vs), neg, pos, hit))

        # A column the statement's own totals vouch for beats any heuristic.
        # One such column carrying both signs is a single signed column, not
        # half of a debit/credit pair -- adding a partner to fill the shape
        # would propose the same amounts in a second currency as the credits.
        confirmed = [x for x in scored if x[4]]
        signed_one = False
        if confirmed:
            big = [x[0] for x in confirmed]
            if len(confirmed) == 1 and confirmed[0][2] and confirmed[0][3]:
                signed_one = True
        else:
            right = page_w * 0.55
            cand = [t for t, _, _, _, _ in scored if t[1] >= right] or \
                   [t for t, _, _, _, _ in scored]
            top_n = max(t[3] for t in cand) if cand else 0
            big = [t for t in cand if t[3] >= max(3, top_n * 0.15)]
            big = sorted(big, key=lambda t: -t[3])[:3]
        big.sort(key=lambda t: t[1])
        money_left = min(t[1] for t in big) - 40 if big else 0

        def inside_big(t):
            return any(b[1] - 1 <= t[1] <= b[2] + 1 for b in big)

        stray = [t for t in named
                 if not inside_big(t) and t[1] >= money_left]
        if stray:
            w('\nUNCLAIMED CLUSTERS -- in the money region but not a transaction\n'
              'column. Each is a summary block above the table or a totals row\n'
              'below it, and each becomes a phantom transaction unbounded.\n')
            for nm in sorted(stray, key=lambda t: -t[3]):
                w('  %s  x1 %.1f  n=%-4d  bound with start_re (above the table)\n'
                  '      or stop_re (below it) -- see TOTALS for the wording.\n'
                  % (nm[0], nm[1], nm[3]))
        left = [t for t in named if t not in big and t[1] < money_left]
        if left:
            w('\n  (%d further clusters left of the money region are numbers inside\n'
              '   the description -- keep them by setting `description` wide enough.)\n'
              % len(left))

        # ---- totals -------------------------------------------------------
        if totals:
            w('\nTOTALS THE DOCUMENT PRINTS ABOUT ITSELF  -> expect_* in the spec\n')
            for p, t in totals[:14]:
                w('  p%-3d %s\n' % (p, t))

        # ---- proposal -----------------------------------------------------
        if scored:
            w('\nCANDIDATE AMOUNT COLUMNS  (constant columns dropped)\n')
            for nm, n, neg, pos, hit in sorted(scored, key=lambda x: x[0][1]):
                w('  x1 %6.1f-%-6.1f n=%-4d  -sum %13s  +sum %13s  %s\n'
                  % (nm[1], nm[2], n, money(neg) if neg else '-',
                     money(pos) if pos else '-', hit))
        w('\nPROPOSED ColumnSpec -- confirm the bands against the page above\n')
        if signed_one or len(big) == 1:
            big = big[:1]
            labels = ['amount']
        elif len(big) == 2:
            labels = ['debit', 'credit']
        else:
            labels = ['debit', 'credit', 'balance']
        for lab, nm in zip(labels, big):
            note = ''
            if lab == 'balance':
                note = '   # running balance -- DISCARDED'
            elif lab == 'amount':
                note = '   # one signed column: negative = debit'
            w('    %-11s = (%d, %d),%s\n'
              % (lab, int(nm[1] - 12), int(nm[2] + 4), note))
        w("    numbers     = '%s',\n" % style)
        if big and date_edges:
            # The date COLUMN, not the right-most date on the page. A statement
            # prints dates in its boilerplate too -- a licence date here sits at
            # x1=450, two hundred points right of the column, and taking the max
            # swallows the whole description window. Same shape of error as
            # letting a print stamp vote on the date order.
            from collections import Counter as _C
            bucket = _C(round(e / 5.0) * 5 for e in date_edges)
            dom = bucket.most_common(1)[0][0]
            dmax = max(e for e in date_edges if abs(e - dom) <= 6)
            lo = dmax + 4
            hi = min(b[1] for b in big) - 12
            if hi > lo:
                w('    description = (%d, %d),\n' % (int(lo), int(hi)))
                w('    date        = (0, %d),\n' % int(dmax + 3))
        if cur_hits and len(cur_hits) == 1:
            code, xs = list(cur_hits.items())[0]
            w("    currency    = (%d, %d),\n" % (int(min(xs) - 6), int(max(xs) + 6)))
        else:
            w("    # currency_default = '<ISO from the account header>',\n")
        if time_toks:
            n6 = sum(1 for t in time_toks if t.count(':') == 2)
            w("    time_re     = r'\\d{2}:\\d{2}%s',\n"
              % (':\\d{2}' if n6 >= len(time_toks) / 2 else ''))
        if not (big and date_edges):
            w('    # description = (x0, x1)  -- the text window, excluding date and amounts\n')
        w('    # start_re / stop_re from UNCLAIMED above\n')
        if totals:
            w('    # the statement prints these about itself -- one pair is the\n'
              '    # second gate; copy the numbers verbatim, in its own notation:\n')
            for _p, t in totals[:6]:
                w('    #   %s\n' % t[:88])
        w('    # expect_debit_total  = ...,\n')
        w('    # expect_credit_total = ...,\n\n')
    return 0


def probe_tabular(path):
    """Spreadsheets and delimited files: bands are column indices."""
    sys.stdout.write('\nfile      %s   (tabular: bands are COLUMN INDICES, '
                     'use col(i) / cols(a,b))\n\n' % os.path.basename(path))
    ext = os.path.splitext(path)[1].lower()
    rows = []
    if ext in ('.xlsx', '.xlsm'):
        try:
            import openpyxl
        except ImportError:
            raise StatementError('probe: openpyxl is required for .xlsx\n'
                                 '    python3 -m pip install openpyxl')
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for n, vals in enumerate(wb.worksheets[0].iter_rows(values_only=True)):
                rows.append(['' if v is None else str(v) for v in vals])
                if n >= 30:
                    break
        finally:
            wb.close()
    elif ext == '.xls':
        raise StatementError(
            'legacy .xls cannot be read here: xlrd is absent and there is no\n'
            'LibreOffice to convert it. Ask for .xlsx or .csv -- that is the fix.')
    else:
        import csv as _csv
        with open(path, 'r', encoding='utf-8-sig', newline='') as fh:
            sample = fh.read(8192); fh.seek(0)
            try:
                d = _csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
            except Exception:
                d = ','
            for n, vals in enumerate(_csv.reader(fh, delimiter=d)):
                rows.append(vals)
                if n >= 30:
                    break
    for r in rows[:25]:
        sys.stdout.write('  ' + ' | '.join('%d:%s' % (i, v[:22])
                                           for i, v in enumerate(r) if v) + '\n')
    sys.stdout.write('\n  money-looking columns:\n')
    tally = {}
    for r in rows:
        for i, v in enumerate(r):
            if v and MONEY_TOKEN.match(v.strip()):
                tally[i] = tally.get(i, 0) + 1
    for i, c in sorted(tally.items()):
        sys.stdout.write('    col %-3d n=%d\n' % (i, c))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except StatementError as e:
        sys.stderr.write('\n%s\n' % e)
        sys.exit(2)
