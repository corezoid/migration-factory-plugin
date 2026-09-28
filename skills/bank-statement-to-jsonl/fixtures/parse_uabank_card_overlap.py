import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from statement_lib import ColumnSpec, run
# The same bank and the same columns as parse_uabank_card.py, and one field
# apart: this statement's PDF draws the date, the description and the amount as
# three overlapping layers of characters on page 9. At the default word
# tolerance `-29 355.00` arrives as `...KVASYLIV,UA-29` plus `355.00`, so the
# row reads as a credit of 355 instead of a debit of 29 355 -- right column,
# right shape, wrong by twenty-nine thousand, and 336 rows either way. Only the
# printed totals disagree. `probe.py` now names this before the first parse;
# this case is here so that the library keeps answering it.
SPEC = ColumnSpec(
    amount      = (243, 268),
    balance     = (540, 560),
    ignore      = [(300, 325), (430, 450), (480, 496)],
    description = (100, 230),
    date        = (0, 95),
    numbers     = 'us',
    x_tolerance = 1,
    currency_default = 'UAH',
    date_re     = r'(\d{2}\.\d{2}\.\d{4})',
    date_order  = 'dmy',
    time_re     = r'\d{2}:\d{2}:\d{2}',
    expect_debit_total  = '823 750.52',
    expect_credit_total = '820 479.47',
)
if __name__ == '__main__':
    sys.exit(run(SPEC))
