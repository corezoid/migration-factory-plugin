#!/usr/bin/env python3
"""Copy me for a spreadsheet or a delimited file.

    python3 parse_<bank>.py <statement.xlsx> -o out.jsonl

A sheet has no x-coordinates, so a band is a COLUMN INDEX instead: col(4) is
one column, cols(2,3) is a span. Nothing else in the spec changes -- the same
number grammars, the same date handling, the same skip/stop rules, the same
two gates. That is the point of one spec shape for every input.

Continuation merging works here too, and is usually what you want: exports
that wrap a long narrative across rows leave the amount columns empty on the
wrapped rows, and row_start='amount' rejoins them.
"""
import os
import sys

sys.path.insert(0, '<skill-dir>/scripts')
from statement_lib import ColumnSpec, col, cols, run

SPEC = ColumnSpec(
    # --- where the money is. For a sheet these are column indices, 0-based.
    debit       = col(4),
    credit      = col(5),
    # balance   = col(6),   # a running balance column: declare it so it is
    #                           # DISCARDED. Undeclared, it is the number a
    #                           # "last number on the line" parser would take.
    # amount    = col(4),   # INSTEAD of debit/credit when there is one
    #                           # signed column: negative is a debit.

    # --- where the text is. Exclude the date and the amount columns, keep any
    #     numbers that live inside the narrative.
    description = cols(2, 3),
    # date      = col(0),   # when the statement prints two date columns

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
    header_rows = 1,            # a header row at the top of the sheet
    # sheet     = 'Transactions',
    # delimiter = ';',          # csv only; sniffed when absent
    # stop_re   = r'Total',
    skip_rows   = [],           # ledger lines inside the table

    # --- what the statement says about itself. This is the second gate, and
    #     it is the only check that sees the rows nobody read.
    expect_debit_total  = None,
    expect_credit_total = None,
    expect_count        = None,
)

if __name__ == '__main__':
    sys.exit(run(SPEC))
