import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from statement_lib import ColumnSpec, col, cols, run
SPEC = ColumnSpec(
    debit=col(4), credit=col(5), description=cols(2,3), date=col(0),
    numbers='us', currency_default='GBP', date_re=r'(\d{2}/\d{2}/\d{4})', date_order='dmy',
    header_rows=1, stop_re=r'^Total',
    expect_debit_total='179.50', expect_credit_total='2500.00', expect_count=3,
)
if __name__ == '__main__': sys.exit(run(SPEC))
