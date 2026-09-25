#!/usr/bin/env python3
"""Copy me next to the statement, fill in the spec, run me.

    python3 parse_<bank>.py <statement.pdf> -o out.jsonl

Everything hard is in statement_lib. What belongs here is the description of
THIS bank's columns and nothing else -- if you find yourself importing
pdfplumber or writing a loop, the answer you want is already a field below.
"""
import os
import sys

sys.path.insert(0, '<skill-dir>/scripts')
from statement_lib import ColumnSpec, run

SPEC = ColumnSpec(
    # --- where the money is. Bands are x1 (RIGHT edge): money is right-aligned,
    #     so the right edge is the stable one. probe.py proposes these.
    debit       = (000, 000),
    credit      = (000, 000),
    # balance   = (000, 000),   # a running balance column: declare it so it is
    #                           # DISCARDED. Undeclared, it is the number a
    #                           # "last number on the line" parser would take.
    # amount    = (000, 000),   # INSTEAD of debit/credit when there is one
    #                           # signed column: negative is a debit.

    # --- where the text is. Exclude the date and the amount columns, keep any
    #     numbers that live inside the narrative.
    description = (000, 000),
    # date      = (000, 000),   # when the statement prints two date columns

    # --- formats. Never guessed: 'us' is 1,163.14 and 'eu' is 1.238,92, and
    #     the wrong one is silently plausible money.
    numbers     = 'us',

    # --- which currency the row is in. Always emitted; 'XXX' when unnamed.
    currency_default = None,    # 'RON' -- the usual case: the header says it once
    # currency  = (x0, x1),     # only when a column names YOUR amount's currency
    #                           # (a card statement's Валюта names the *other* one)
    date_re     = r'(\d{2}/\d{2}/\d{4})',
    date_order  = 'dmy',
    date_carry  = True,         # statements that print the date once per day

    # --- where the table begins and ends. Both matter: a summary block above
    #     the table and a totals row below it both land in real amount bands.
    # start_re  = r'Data\s+Descriere\s+Debit\s+Credit',
    # start_per_page = True,    # only if that header repeats on every page
    # stop_re   = r'RULAJ\s+TOTAL\s+CONT',
    skip_rows   = [],           # ledger lines inside the table

    # --- what the statement says about itself. This is the second gate, and
    #     it is the only check that sees the rows nobody read.
    expect_debit_total  = None,
    expect_credit_total = None,
    expect_count        = None,
)

if __name__ == '__main__':
    sys.exit(run(SPEC))
