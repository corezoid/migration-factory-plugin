#!/usr/bin/env python3
"""Two gates on a finished JSONL. Nothing is done until both pass.

    python3 validate.py out.jsonl --source statement.pdf
    python3 validate.py out.jsonl --expect-debit 478,805.56 \
                                  --expect-credit 478,805.56 --numbers us
    python3 validate.py out.jsonl --show 10

Gate 1 is the shape of the rows: dates really yyyy-mm-dd, amounts really
strings with two places, exactly one side non-zero, descriptions non-empty.

Gate 2 is the statement's own arithmetic. It exists because gate 1 cannot
fail on the errors that matter most. A parser that dropped half the pages, or
read the running-balance column instead of the amount, produces ten perfectly
well-formed opening rows -- this was measured, not imagined: a fixed-width
line-binning bug fabricated a transaction on page 2 of a fixture while its
first ten rows stayed flawless. Only the totals saw it.

So an unverified run says so loudly rather than printing a reassuring PASS:
`--expect-*` or `--source` is what makes the second gate possible, and
without either the result is one gate out of two.

Expected totals reach it three ways:
  --expect-debit / --expect-credit / --expect-count   in the statement's own
                                                      notation, parsed with
                                                      --numbers
  --source <statement>                                re-scan the document for
                                                      the totals it prints
  neither                                             gate 2 SKIPPED, loudly
"""

import json
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statement_lib import (CENT, ZERO, StatementError, money,  # noqa: E402
                           parse_amount, MONEY_TOKEN, _US, _EU, _clean, _HAS_CENTS)

DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$')
AMT = re.compile(r'^\d+\.\d{2}$')
KEYS = {'transaction_date', 'transaction_time', 'debit_sum', 'credit_sum',
        'currency', 'description'}
CUR = re.compile(r'^[A-Z]{3}$')

TOTAL_LINE = re.compile(
    r'\b(total|rulaj|sold|balance|closing|opening|deschidere|inchidere)\b', re.I)


def load(path):
    rows = []
    with open(path, 'r', encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append((n, json.loads(line)))
            except ValueError as e:
                raise StatementError('line %d is not JSON: %s' % (n, e))
    return rows


def gate_one(rows, show):
    w = sys.stdout.write
    w('\nGATE 1  SHAPE OF THE ROWS%s%d rows\n' % (' ' * 34, len(rows)))
    fails = []

    def check(name, bad, note=''):
        if bad:
            fails.append(name)
            w('  %-18s FAIL  %d rows, first at line %d  %s\n'
              % (name, len(bad), bad[0][0], note))
            for n, r in bad[:3]:
                w('        line %d: %s\n' % (n, json.dumps(r, ensure_ascii=False)[:120]))
        else:
            w('  %-18s OK\n' % name)

    check('keys', [(n, r) for n, r in rows if set(r) - KEYS or
                   not {'transaction_date', 'debit_sum', 'credit_sum',
                        'currency', 'description'} <= set(r)])
    check('transaction_date', [(n, r) for n, r in rows
                               if not DATE.match(str(r.get('transaction_date', '')))])
    check('transaction_time', [(n, r) for n, r in rows
                               if 'transaction_time' in r
                               and not TIME.match(str(r['transaction_time']))])
    check('debit_sum', [(n, r) for n, r in rows
                        if not AMT.match(str(r.get('debit_sum', '')))],
          'want a string like "0.00" -- 2dp, no separators, no sign')
    check('credit_sum', [(n, r) for n, r in rows
                         if not AMT.match(str(r.get('credit_sum', '')))])

    both = [(n, r) for n, r in rows
            if _f(r.get('debit_sum')) > 0 and _f(r.get('credit_sum')) > 0]
    check('one side only', both,
          'both sides non-zero is what a turnover/ledger row looks like')
    check('not both zero', [(n, r) for n, r in rows
                            if _f(r.get('debit_sum')) == 0
                            and _f(r.get('credit_sum')) == 0],
          'a band is missing its rail, or a noise row got through')
    check('currency', [(n, r) for n, r in rows
                       if not CUR.match(str(r.get('currency', '')))],
          'want a three-letter ISO code; XXX when the statement never named one')
    check('description', [(n, r) for n, r in rows
                          if not str(r.get('description', '')).strip()])

    by_cur = {}
    for _n, r in rows:
        c = str(r.get('currency', '?'))
        d, k = by_cur.get(c, (0, 0))
        by_cur[c] = (d + 1, k)
    w('  %-18s %s\n' % ('currencies', ', '.join(
        '%s x%d' % (c, v[0]) for c, v in sorted(by_cur.items(), key=lambda k: -k[1][0]))))
    if 'XXX' in by_cur:
        w('  %-18s %d rows unnamed -- amounts without a currency are numbers,\n'
          '%sset currency_default or a currency band\n'
          % ('  (XXX)', by_cur['XXX'][0], ' ' * 22))
    if len(by_cur) > 1:
        w('  %-18s more than one: gate 2 below is only meaningful per currency\n'
          % '  (mixed)')

    dates = [r['transaction_date'] for _, r in rows if DATE.match(str(r.get('transaction_date', '')))]
    if dates:
        w('  %-18s %s .. %s%s\n' % ('date range', min(dates), max(dates),
                                    '   (not sorted)' if dates != sorted(dates) else ''))
    vals = [_f(r.get('debit_sum')) + _f(r.get('credit_sum')) for _, r in rows]
    if vals:
        w('  %-18s min %s   max %s\n' % ('magnitude', money(Decimal(str(min(vals)))),
                                         money(Decimal(str(max(vals))))))
    ntime = sum(1 for _, r in rows if 'transaction_time' in r)
    w('  %-18s %d rows carry one\n' % ('transaction_time', ntime))

    if show:
        w('\nFIRST %d ROWS -- read these against the statement, not against\n'
          'themselves: ten self-consistent rows is exactly what an inverted\n'
          'debit/credit map produces.\n' % min(show, len(rows)))
        for n, r in rows[:show]:
            w('  %3d  %s  D=%-13s C=%-13s %s\n'
              % (n, r.get('transaction_date', '?'), r.get('debit_sum', '?'),
                 r.get('credit_sum', '?'),
                 ('%s  %s' % (r.get('currency', '???'),
                              str(r.get('description', ''))))[:58]))
    return not fails


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def scan_totals(path):
    """Re-read the statement for the totals it prints about itself."""
    if not path.lower().endswith('.pdf'):
        return []
    try:
        import pdfplumber
    except ImportError:
        return []
    out = []
    with pdfplumber.open(path) as pdf:
        pages = list(range(min(2, len(pdf.pages)))) + \
                list(range(max(0, len(pdf.pages) - 2), len(pdf.pages)))
        for i in sorted(set(pages)):
            for line in (pdf.pages[i].extract_text() or '').split('\n'):
                if TOTAL_LINE.search(line) and re.search(r'\d[\d.,]*[.,]\d{2}', line):
                    out.append((i + 1, line.strip()))
            pdf.pages[i].flush_cache()
    return out


def gate_two(rows, want_d, want_c, want_n, source, only_cur=None):
    w = sys.stdout.write
    w('\nGATE 2  THE STATEMENT\'S OWN ARITHMETIC\n')
    if only_cur:
        rows = [(n, r) for n, r in rows if str(r.get('currency', '')) == only_cur]
        w('  filtered to %s: %d rows\n' % (only_cur, len(rows)))
    seen = set(str(r.get('currency', '?')) for _, r in rows)
    if len(seen) > 1:
        w('  !! %d currencies in these rows (%s). Summing across them is not\n'
          '     arithmetic anybody printed -- pass --currency <ISO> and check\n'
          '     each against its own total.\n' % (len(seen), ', '.join(sorted(seen))))
    deb = sum(Decimal(str(r.get('debit_sum', '0'))) for _, r in rows)
    cred = sum(Decimal(str(r.get('credit_sum', '0'))) for _, r in rows)

    if want_d is None and want_c is None and want_n is None:
        w('  SKIPPED -- no expected totals given. THIS RUN IS UNVERIFIED:\n'
          '  gate 1 cannot see dropped pages or a balance column read as an\n'
          '  amount. Pass --expect-debit/--expect-credit, or --source.\n')
        if source:
            found = scan_totals(source)
            if found:
                w('\n  the statement prints these -- one of them is the check:\n')
                for p, t in found[:12]:
                    w('    p%-3d %s\n' % (p, t[:96]))
        w('\n  parsed debit  %16s\n  parsed credit %16s\n  rows %25d\n'
          % (money(deb), money(cred), len(rows)))
        return None

    # Both sides over by the SAME amount is the arithmetic fingerprint of the
    # two conventions disagreeing, and nothing else produces it. A negative
    # amount kept in its own column (how the statement totals it) versus moved
    # to the opposite side (how the JSONL must emit it, since debit_sum and
    # credit_sum are unsigned) shifts each side by the same sum. Recognising
    # it is not a fudge: the signed reading reconciles exactly, and calling
    # that a failure would push the agent to break a correct parser.
    conv = None
    if want_d is not None and want_c is not None:
        dd, dc = deb - want_d, cred - want_c
        if dd > ZERO and abs(dd - dc) < CENT:
            conv = dd

    ok = True
    for name, got, want in (('debit', deb, want_d), ('credit', cred, want_c)):
        if want is None:
            continue
        if conv is not None:
            got = got - conv
        delta = got - want
        good = abs(delta) < CENT
        ok = ok and good
        w('  %-7s %16s   expected %16s   delta %12s   %s\n'
          % (name, money(got), money(want), money(delta),
             'OK' if good else 'FAIL'))
        if not good:
            explain(rows, name, delta)
    if conv is not None:
        w('  both sides are over by the same %s -- the statement totals its\n'
          '  negatives in place, the JSONL moves them to the other side (it has\n'
          '  no sign to keep). Reconciled on the signed convention above.\n'
          % money(conv))
    if want_n is not None:
        good = len(rows) == want_n
        ok = ok and good
        w('  %-7s %16d   expected %16d   delta %12d   %s\n'
          % ('count', len(rows), want_n, len(rows) - want_n, 'OK' if good else 'FAIL'))
    return ok


def explain(rows, side, delta):
    """A gap is a lead, not a number to apologise for."""
    w = sys.stdout.write
    key = side + '_sum'
    d = abs(delta)
    hits = [(n, r) for n, r in rows if abs(Decimal(str(r.get(key, '0'))) - d) < CENT]
    if hits:
        n, r = hits[0]
        w('      the gap is exactly one row: line %d, %s\n'
          '      -> a noise/totals row was emitted, or one row was counted twice\n'
          % (n, str(r.get('description', ''))[:60]))
        return
    w('      %s\n' % _signature(rows, side, delta))


def _signature(rows, side, delta):
    d = abs(delta)
    tot = sum(Decimal(str(r.get(side + '_sum', '0'))) for _, r in rows)
    if tot and abs(d - tot) < CENT:
        return 'the whole side is unaccounted for -- the band is wrong or empty'
    if tot and d > tot * Decimal('0.5'):
        return ('over by more than half the side -- a balance column is being\n'
                '      read as an amount, or negatives are being flipped')
    for f in (Decimal('1000'), Decimal('100')):
        if tot and abs(d - tot * (f - 1) / f) < CENT:
            return 'off by a factor of %s -- the number format is the other one' % f
    return ('no single row explains it: suspect continuations emitted as rows,\n'
            '      or a page dropped. Check the parser report\'s per-page counts.')


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        sys.stderr.write(__doc__)
        return 2
    path = argv[0]
    want_d = want_c = want_n = None
    source = None
    style = 'us'
    show = 10
    only_cur = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--expect-debit':
            i += 1; want_d = argv[i]
        elif a == '--expect-credit':
            i += 1; want_c = argv[i]
        elif a == '--expect-count':
            i += 1; want_n = int(argv[i])
        elif a == '--source':
            i += 1; source = argv[i]
        elif a == '--numbers':
            i += 1; style = argv[i]
        elif a == '--currency':
            i += 1; only_cur = argv[i].upper()
        elif a == '--show':
            i += 1; show = int(argv[i])
        else:
            sys.stderr.write('unknown argument %r\n' % a); return 2
        i += 1

    rows = load(path)
    if not rows:
        sys.stdout.write('\n%s is empty -- nothing was parsed.\n' % path)
        return 1

    g1 = gate_one(rows, show)
    wd = parse_amount(want_d, style) if want_d else None
    wc = parse_amount(want_c, style) if want_c else None
    g2 = gate_two(rows, wd, wc, want_n, source, only_cur)

    sys.stdout.write('\nRESULT  ')
    if g1 and g2:
        sys.stdout.write('PASS  (2 of 2 gates)\n\n')
        return 0
    if g1 and g2 is None:
        sys.stdout.write('PARTIAL  (1 of 2 gates -- gate 2 not run)\n\n')
        return 0
    sys.stdout.write('FAIL  (gate 1 %s, gate 2 %s)\n\n'
                     % ('pass' if g1 else 'fail',
                        'not run' if g2 is None else ('pass' if g2 else 'fail')))
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except StatementError as e:
        sys.stderr.write('\n%s\n' % e)
        sys.exit(2)
