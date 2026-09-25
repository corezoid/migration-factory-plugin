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
    debit       = (430, 480),
    credit      = (520, 580),
    description = (85, 430),
    numbers     = 'us',
    currency_default = 'RON',      # 'Valuta Cont de disponibil RON', header only
    date_re     = r'(\d{2}/\d{2}/\d{4})',
    date_order  = 'dmy',
    date_carry  = True,
    start_re    = r'Data\s+Descriere\s+Debit\s+Credit',
    start_per_page = True,
    stop_re     = r'RULAJ\s+TOTAL\s+CONT',
    skip_rows   = ['SOLD ANTERIOR', 'RULAJ ZI', 'SOLD FINAL ZI'],
    expect_debit_total  = '140,265.18',
    expect_credit_total = '141,597.01',
    expect_count        = 47,
)

if __name__ == '__main__':
    sys.exit(run(SPEC))
