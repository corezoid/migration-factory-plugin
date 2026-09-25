import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from statement_lib import ColumnSpec, run
SPEC = ColumnSpec(
    amount      = (243, 268),   # band proposed by probe, verbatim
    balance     = (540, 560),      # 'Залишок після операції'
    ignore      = [(300, 325), (430, 450), (480, 496)],  # сума в валюті операції, комісія, кешбек
    description = (100, 230),
    date        = (0, 95),
    numbers     = 'us',
    # NOT the 'Валюта' column: that one names the currency of «Сума в валюті
    # операції», the column beside the one taken here. Banding it labels a
    # hryvnia amount USD on the three foreign purchases. The amount taken is
    # «Сума в валюті картки (UAH)», and its header says the currency outright.
    currency_default = 'UAH',
    date_re     = r'(\d{2}\.\d{2}\.\d{4})',
    date_order  = 'dmy',
    time_re     = r'\d{2}:\d{2}:\d{2}',
    expect_debit_total  = '12 052.80',
    expect_credit_total = '44.83',
)
if __name__ == '__main__':
    sys.exit(run(SPEC))
