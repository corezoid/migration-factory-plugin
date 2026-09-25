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
    amount      = (500, 515),
    description = (195, 430),
    date        = (0, 100),
    numbers     = 'eu',
    currency    = (526, 538),      # 'Valuta' column, EUR on all 37 rows
    date_re     = r'(\d{2}\.\d{2}\.\d{4})',
    date_order  = 'dmy',
    start_re    = r'Valoare\s+Tranz',
    stop_re     = r'Sold\s+deschidere',
    expect_debit_total  = '20.705,54',
    expect_credit_total = '20.706,54',
    expect_count        = 37,
)

if __name__ == '__main__':
    sys.exit(run(SPEC))
