#!/usr/bin/env python3
"""Run every statement this skill has ever parsed correctly.

    python3 regress.py                 # from the directory holding the statements
    python3 regress.py --root ~/docs   # statements live elsewhere
    python3 regress.py --only BT

This is what makes editing the shared library a checkable act instead of a
hopeful one. `statement_lib.py` is used by every bank at once, so a change
made to rescue one statement can silently break another that nobody will open
again. The suite is the memory: each case carries the spec that parsed it and
the totals the statement printed about itself, so a regression shows up as a
reconciliation that used to hold and no longer does.

The protocol it exists for:

    1. run it BEFORE touching shared code, and keep the baseline
    2. make the change
    3. run it again -- every case that passed must still pass
    4. add the new statement as a case, so the next change is checked against
       it too

A case whose file is missing is SKIPPED, not failed: the statements are not
redistributed with the skill and some of them are somebody's private banking.
Skips are counted and printed, so a suite that is quietly empty cannot be
mistaken for a suite that passed.
"""

import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statement_lib import (Counts, StatementError, money,  # noqa: E402
                           reconcile, transactions)

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, '..', 'fixtures')


def load_spec(path):
    spec = importlib.util.spec_from_file_location('case_spec', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SPEC


def run_script_case(case, path):
    """A case whose parser owes the suite nothing but its output.

    A parser written from scratch is not a lesser citizen here: it is checked
    the same way, because the check was never about how the rows were produced.
    It runs the script and reads the JSONL back, so any implementation at all
    qualifies -- ColumnSpec, a hand-rolled regex pass, a different library.
    """
    import json as _json
    import subprocess
    import tempfile
    from decimal import Decimal as _D
    exp = case.get('expect', {})
    with tempfile.NamedTemporaryFile(suffix='.jsonl', delete=False) as tf:
        out = tf.name
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(FIX, case['script']), path, '-o', out],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
        rows = []
        with open(out) as fh:
            for line in fh:
                if line.strip():
                    rows.append(_json.loads(line))
    except Exception as e:
        return ('FAIL', '%s: %s' % (type(e).__name__, e), None)
    finally:
        if os.path.exists(out):
            os.unlink(out)

    deb = sum(_D(x.get('debit_sum', '0')) for x in rows)
    cred = sum(_D(x.get('credit_sum', '0')) for x in rows)
    detail = '%4d txns   D %14s   C %14s' % (len(rows), money(deb), money(cred))
    if r.returncode not in (0,):
        return ('FAIL', detail + '   exit %d' % r.returncode, None)
    for key, got in (('debit', deb), ('credit', cred)):
        if key in exp and abs(got - _D(str(exp[key]))) >= _D('0.01'):
            return ('FAIL', detail + '   %s != %s' % (key, exp[key]), None)
    if 'count' in exp and len(rows) != exp['count']:
        return ('FAIL', detail + '   count != %d' % exp['count'], None)
    if not exp:
        return ('FAIL', detail + '   no expectations recorded -- not a check', None)
    return ('PASS', detail, None)


def run_case(case, root):
    path = case['file']
    if not os.path.isabs(path):
        cand = [os.path.join(root, path),
                os.path.join(FIX, '..', path),
                os.path.join(HERE, '..', path)]
        path = next((c for c in cand if os.path.exists(c)), cand[0])
    if not os.path.exists(path):
        return ('SKIP', 'file not found: %s' % case['file'], None)
    if 'script' in case:
        return run_script_case(case, path)
    spec = load_spec(os.path.join(FIX, case['spec']))
    counts = Counts()
    n = 0
    for _ in transactions(spec, path, None, counts):
        n += 1
    ok, _lines = reconcile(counts, spec)
    detail = '%4d txns   D %14s   C %14s' % (
        n, money(counts.debit_total), money(counts.credit_total))
    if spec.expect_count is not None and n != spec.expect_count:
        return ('FAIL', detail + '   count %d != %d' % (n, spec.expect_count), counts)
    if not ok:
        return ('FAIL', detail + '   does not reconcile', counts)
    return ('PASS', detail, counts)


def main(argv):
    root = os.getcwd()
    only = None
    i = 0
    while i < len(argv):
        if argv[i] == '--root':
            i += 1; root = os.path.expanduser(argv[i])
        elif argv[i] == '--only':
            i += 1; only = argv[i]
        elif argv[i] in ('-h', '--help'):
            sys.stderr.write(__doc__); return 2
        else:
            sys.stderr.write('unknown argument %r\n' % argv[i]); return 2
        i += 1

    cases = json.load(open(os.path.join(FIX, 'cases.json')))['cases']
    if only:
        cases = [c for c in cases if only.lower() in c['name'].lower()]
    w = sys.stdout.write
    w('\nregression suite -- %d cases, statements resolved against %s\n\n'
      % (len(cases), root))
    tally = {'PASS': 0, 'FAIL': 0, 'SKIP': 0}
    for c in cases:
        try:
            status, detail, _ = run_case(c, root)
        except StatementError as e:
            status, detail = 'FAIL', str(e).split('\n')[0]
        except Exception as e:  # a spec that no longer imports is a regression
            status, detail = 'FAIL', '%s: %s' % (type(e).__name__, e)
        tally[status] += 1
        w('  %-4s  %-15s %s\n' % (status, c['name'], detail))
        if status == 'PASS':
            w('        %s\n' % c.get('shape', ''))
    w('\n  %d passed, %d failed, %d skipped\n' % (tally['PASS'], tally['FAIL'], tally['SKIP']))
    if tally['FAIL']:
        w('\n  A case that used to pass and now does not is the change you just\n'
          '  made, not a property of that statement. Revert or make the new\n'
          '  behaviour opt-in.\n\n')
        return 1
    if tally['PASS'] == 0:
        w('\n  nothing actually ran -- this is not a pass.\n\n')
        return 1
    w('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
