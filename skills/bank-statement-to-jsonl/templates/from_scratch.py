#!/usr/bin/env python3
"""A parser written from zero, when the library is the wrong shape.

    python3 from_scratch.py <statement> -o out.jsonl

Copy this when `ColumnSpec` is fighting you rather than helping: a statement
with no columns to band (an HTML or JSON export, a receipt-style ledger, a
fixed-width dump, OCR output whose coordinates mean nothing), or one where two
rounds of spec edits have not converged. Reaching for this is a judgement
call, not a defeat -- a wrong abstraction costs more than no abstraction.

**What you are free to change: everything inside `extract()`.** Use pdfplumber,
PyMuPDF, an HTML parser, plain regex over a text dump, anything. Read the file
however you like.

**What you are not free to change: the contract and the gate.** Every row must
be one JSON object with the keys below, and the run must still be checked
against the totals the statement prints about itself. Those two survive every
rewrite, because they are the only reason anyone can trust the output -- an
implementation nobody can check is worth less than no implementation.

The four primitives below are imported rather than rewritten on purpose. They
are where the silent mistakes live: `1.238,92` read as 1.24, `01/04/2026` read
as the fourth of January, a float that makes 40,000 rows sum to 0.01 off.
Nothing stops you from replacing them -- but replace them knowingly.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statement_lib import Transaction, money, parse_amount, parse_date  # noqa: E402

# What the statement says about itself. Fill these in from the document -- the
# second gate is the only check that sees the rows nobody read.
EXPECT_DEBIT = None     # e.g. '478,805.56', in the statement's own notation
EXPECT_CREDIT = None
EXPECT_COUNT = None
NUMBERS = 'us'          # 'us' 1,163.14  |  'eu' 1.238,92


def extract(path):
    """Yield one Transaction per transaction. This is the part you write.

        yield Transaction(
            transaction_date = parse_date('01/04/2026', 'dmy'),   # -> yyyy-mm-dd
            transaction_time = '08:20:02',                        # or None
            debit_sum        = money(Decimal('29.00')),           # -> '29.00'
            credit_sum       = '0.00',
            currency         = 'RON',      # 'XXX' if the statement never names one
            description      = 'Pachet IZI ... REF: 547IZ...',
        )

    Rules that are not negotiable, because they are what the record means:
      - exactly one of debit_sum / credit_sum is non-zero on a row
      - both are strings, dot decimal, two places, unsigned
      - transaction_date is always present; transaction_time only when the
        statement prints one for THAT row -- never the document's print stamp
      - currency is a three-letter code, 'XXX' when the statement never named
        one -- and it must describe THIS amount, not a second amount printed
        beside it in the merchant's currency
      - description carries this row's continuation lines, joined
    """
    raise NotImplementedError('write extract() for this statement')


def main(argv):
    if not argv:
        sys.stderr.write('usage: %s <statement> [-o out.jsonl]\n'
                         % os.path.basename(sys.argv[0]))
        return 2
    src = argv[0]
    out = argv[argv.index('-o') + 1] if '-o' in argv \
        else os.path.splitext(src)[0] + '.jsonl'

    n = 0
    deb = cred = Decimal('0')
    with open(out, 'w', encoding='utf-8') as fh:
        for t in extract(src):
            fh.write(t.to_json() + '\n')
            n += 1
            deb += Decimal(t.debit_sum)
            cred += Decimal(t.credit_sum)

    w = sys.stderr.write
    w('\n%s -> %s\n  transactions %d\n  debit  %14s\n  credit %14s\n'
      % (src, out, n, money(deb), money(cred)))

    ok = True
    for name, got, want in (('debit', deb, EXPECT_DEBIT),
                            ('credit', cred, EXPECT_CREDIT)):
        if want is None:
            continue
        exp = parse_amount(want, NUMBERS)
        good = abs(got - exp) < Decimal('0.01')
        ok = ok and good
        w('  %-7s %14s   expected %14s   %s\n'
          % (name, money(got), money(exp), 'OK' if good else 'FAIL'))
    if EXPECT_COUNT is not None:
        good = n == EXPECT_COUNT
        ok = ok and good
        w('  count   %14d   expected %14d   %s\n'
          % (n, EXPECT_COUNT, 'OK' if good else 'FAIL'))
    if EXPECT_DEBIT is None and EXPECT_CREDIT is None and EXPECT_COUNT is None:
        w('\n  no expected totals set -- THIS RUN IS UNVERIFIED. Writing a\n'
          '  parser from scratch does not remove the need to prove it; it is\n'
          '  the case where proving it matters most.\n')
    w('\nnow: python3 validate.py %s --source %s\n\n' % (out, src))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
