#!/usr/bin/env python3
"""Unicredit parsed from zero -- no ColumnSpec, no banding machinery.

Exists to keep the from-scratch path honest: the suite must be able to protect
a parser that shares nothing with the library but the output contract.
"""
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from statement_lib import Transaction, money, parse_date  # noqa: E402

EXPECT_DEBIT, EXPECT_CREDIT, EXPECT_COUNT = '20705.54', '20706.54', 37
AMT = re.compile(r'^-?[\d.]*\d,\d{2}$')
DATE = re.compile(r'^\d{2}\.\d{2}\.\d{4}$')


def eu(tok):
    return Decimal(tok.replace('.', '').replace(',', '.'))


def extract(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines = {}
            for w in page.extract_words():
                lines.setdefault(round(w['top'] / 4), []).append(w)
            for k in sorted(lines):
                row = sorted(lines[k], key=lambda w: w['x0'])
                text = ' '.join(w['text'] for w in row)
                if text.startswith(('Sold ', 'Credit total', 'Debit total', 'Totalul')):
                    continue
                amt = next((w['text'] for w in row
                            if AMT.match(w['text']) and 505 <= w['x1'] <= 515), None)
                if not amt or not DATE.match(row[0]['text']):
                    continue
                v = eu(amt)
                desc = ' '.join(w['text'] for w in row if 195 <= w['x1'] <= 430)
                cur = next((w['text'] for w in row
                            if re.match(r'^[A-Z]{3}$', w['text'])
                            and 526 <= w['x1'] <= 538), 'XXX')
                yield Transaction(
                    currency=cur,
                    transaction_date=parse_date(row[0]['text'], 'dmy'),
                    debit_sum=money(v) if v < 0 else '0.00',
                    credit_sum='0.00' if v < 0 else money(v),
                    description=re.sub(r'\s+', ' ', desc).strip())


def main(argv):
    src = argv[0]
    out = argv[argv.index('-o') + 1] if '-o' in argv else 'out.jsonl'
    n = 0
    with open(out, 'w', encoding='utf-8') as fh:
        for t in extract(src):
            fh.write(t.to_json() + '\n')
            n += 1
    sys.stderr.write('%d transactions -> %s\n' % (n, out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
