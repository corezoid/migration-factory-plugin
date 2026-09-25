import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from statement_lib import ColumnSpec, col, cols, run
SPEC = ColumnSpec(
    amount        = col(2),
    sign_band     = col(3),
    sign_re_debit = r'^(D|Dr|DR|DEBIT)$',
    sign_re_credit= r'^(C|Cr|CR|CREDIT)$',
    balance       = col(4),
    description   = col(1), date = col(0),
    numbers='us', currency_default='INR', date_re=r'(\d{2}/\d{2}/\d{4})', date_order='dmy',
    header_rows=1, stop_re=r'^Total',
    expect_debit_total='349.75', expect_credit_total='2501.30', expect_count=4,
)
if __name__ == '__main__': sys.exit(run(SPEC))
