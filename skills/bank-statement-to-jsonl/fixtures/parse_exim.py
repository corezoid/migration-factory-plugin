#!/usr/bin/env python3
"""Copy me next to the statement, fill in the spec, run me.

    python3 parse_<bank>.py <statement.pdf> -o out.jsonl

Everything hard is in statement_lib. What belongs here is the description of
THIS bank's columns and nothing else -- if you find yourself importing
pdfplumber or writing a loop, the answer you want is already a field below.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from statement_lib import ColumnSpec, run

SPEC = ColumnSpec(
    debit       = (400, 440),
    credit      = (480, 515),
    balance     = (576, 620),
    description = (60, 340),
    date        = (0, 60),
    numbers     = 'us',
    currency_default = 'RON',      # 'Valuta: RON' in the account block
    date_re     = r'(\d{2}-\d{2}-\d{4})',
    date_order  = 'dmy',
    start_re    = r'Rulaj\s+Debit',
    stop_re     = r'Total\s+Rulaje',
    expect_debit_total  = '478,805.56',
    expect_credit_total = '478,805.56',
    expect_count        = 218,
)

if __name__ == '__main__':
    sys.exit(run(SPEC))
