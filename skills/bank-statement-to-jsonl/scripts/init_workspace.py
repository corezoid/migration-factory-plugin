#!/usr/bin/env python3
"""Copy the whole toolkit into the current folder, to be rewritten freely.

    python3 init_workspace.py <statement> [--dir <path>] [--force]

Drops **your own copies** beside the statement, in the folder you are in:

    statement_lib.py      the parsing library -- edit it
    probe.py              the prober          -- edit it
    validate.py           the two gates       -- edit it
    parse_<name>.py       the spec, pre-filled from what the probe measured
    NOTES_<name>.md       what was measured, and what to do next

The three tools are copied once per folder and shared by the statements in
it; the parser and notes are per statement, so several statements can sit in
one folder without colliding.

Why copies rather than a shared import. Statements disagree with each other in
ways no set of options anticipates -- a space thousands separator, a time on
the row's second line, five numeric columns where three were expected. Sooner
or later a statement needs the *library* changed, not the spec. Doing that to
the shared library means one bank's quirk becomes every bank's risk, and the
banks that break are the ones nobody will open again. Doing it to a private
copy costs a directory and risks nothing.

So the rule here is the opposite of the usual one: **edit these files.** They
are yours, they parse one statement, and nobody else depends on them. When a
fix turns out to be general, it can be promoted back into the skill -- and
that is exactly when the skill's regression suite has to pass.
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COPY = ('statement_lib.py', 'probe.py', 'validate.py')
EXTRA = (('..', 'templates', 'from_scratch.py'),)


def slug(path):
    b = os.path.splitext(os.path.basename(path))[0]
    b = re.sub(r'[^A-Za-z0-9]+', '-', b).strip('-').lower()
    return b or 'statement'


def probe(path):
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, 'probe.py'), path],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=300)
        return out.stdout.decode('utf-8', 'replace')
    except Exception as e:
        return 'probe failed: %s\n' % e


def proposal(text):
    """Pull the probe's proposed spec lines out of its report."""
    body, totals = [], []
    grab = False
    for line in text.split('\n'):
        if line.startswith('PROPOSED'):
            grab = True
            continue
        if grab:
            if not line.strip():
                break
            if line.strip().startswith('#'):
                continue
            body.append('    ' + line.strip())
    m = re.search(r"date_re=(r'[^']+'), date_order='(\w+)'", text)
    date_re, order = (m.group(1), m.group(2)) if m else ("r'(\\d{2}\\.\\d{2}\\.\\d{4})'", 'dmy')
    tm = re.search(r'\n(TOTALS THE DOCUMENT[^\n]*\n)((?:  p\d+.*\n)+)', text)
    if tm:
        totals = [l for l in tm.group(2).split('\n') if l.strip()]
    return body, date_re, order, totals


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        sys.stderr.write(__doc__)
        return 2
    src = argv[0]
    if not os.path.exists(src):
        sys.stderr.write('no such file: %s\n' % src)
        return 2
    out = '.'
    if '--dir' in argv:
        out = argv[argv.index('--dir') + 1]
    force = '--force' in argv
    name = slug(src)
    parser = os.path.join(out, 'parse_%s.py' % name)
    notes = os.path.join(out, 'NOTES_%s.md' % name)
    os.makedirs(out, exist_ok=True)

    if os.path.exists(parser) and not force:
        sys.stderr.write(
            '%s already exists -- that is this statement\'s parser, and it is\n'
            'probably further along than anything this would write. Keep editing\n'
            'it, or pass --force to start over.\n' % parser)
        return 2

    for parts in EXTRA:
        dst = os.path.join(out, os.path.basename(parts[-1]))
        if not os.path.exists(dst) or force:
            shutil.copy2(os.path.join(HERE, *parts), dst)

    kept = []
    for f in COPY:
        dst = os.path.join(out, f)
        if os.path.exists(dst) and not force:
            # Already edited for another statement in this folder; overwriting
            # it would silently throw that work away.
            kept.append(f)
            continue
        shutil.copy2(os.path.join(HERE, f), dst)

    report = probe(src)
    body, date_re, order, totals = proposal(report)
    rel = os.path.relpath(os.path.abspath(src), os.path.abspath(out))

    with open(parser, 'w') as fh:
        fh.write('''#!/usr/bin/env python3
"""Parser for %s -- yours, and only this statement's.

    python3 %s %s -o %s.jsonl

statement_lib.py sits in this folder and is imported from here, so editing it
changes only what happens in this folder. If the spec below cannot express
what this statement does, editing the library is the intended next step --
not a dead end.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from statement_lib import ColumnSpec, Cell, col, cols, run   # noqa: F401

SPEC = ColumnSpec(
%s
    date_re     = %s,
    date_order  = '%s',

    # description = (x0, x1),   # the text window: exclude the date and the
    #                           # amount columns, keep numbers that are prose
    # time_re     = r'\\d{2}:\\d{2}:\\d{2}',
    # date_carry  = True,       # when the date is printed once per day
    # balance     = (x0, x1),   # a running balance, declared to be discarded
    # skip_rows   = ['...'],    # ledger lines inside the table
    # stop_re     = r'...',     # the first trailer row after the last txn
    # start_re    = r'...',     # the header, when a summary block sits above

    # --- what the statement says about itself; the second gate needs it -----
    # expect_debit_total  = '...',
    # expect_credit_total = '...',
    # expect_count        = None,
)

# --- bank-local logic, if the spec is not enough ---------------------------
# These run inside the library and keep oddities out of the spec. Uncomment
# what you need; deleting them costs nothing.
#
# def cell_map(c):            # fix a token before it is banded; None drops it
#     return Cell(c.text.replace('\\u00a0', ''), c.x0, c.x1)
# SPEC.cell_map = cell_map
#
# def row_filter(row):        # False drops the row entirely
#     return 'CANCELLED' not in row.text
# SPEC.row_filter = row_filter
#
# def post(t):                # last word on a finished transaction
#     t.description = t.description.replace('  ', ' ')
#     return t
# SPEC.post = post

if __name__ == '__main__':
    sys.exit(run(SPEC))
''' % (os.path.basename(src), os.path.basename(parser), rel, name,
       '\n'.join(body) if body else '    # probe proposed nothing -- read NOTES.md',
       date_re, order))

    with open(notes, 'w') as fh:
        fh.write('# %s\n\nWorking copy. **Every file here is yours to edit**, '
                 'including `statement_lib.py`.\nNothing outside this folder '
                 'depends on them.\n\n## Run\n\n    python3 %s %s -o %s.jsonl\n'
                 '    python3 validate.py %s.jsonl --source %s \\\n'
                 '        --expect-debit <as printed> --expect-credit <as printed>\n\n'
                 '## Order to work in\n\n'
                 '1. **the spec in `%s`** -- bands, formats, skip/stop.\n'
                 '   Most statements end here.\n'
                 '2. **the hooks** `cell_map` / `row_filter` / `post` at the bottom of\n'
                 '   the parser -- bank-local oddities, still no library edit.\n'
                 '3. **`statement_lib.py` itself** -- when the statement needs something\n'
                 '   the library cannot express. Change it. Re-run the two commands\n'
                 '   above; this statement is the only thing that has to keep working.\n'
                 '4. **`from_scratch.py`** -- when the abstraction is the problem rather\n'
                 '   than the gap: no columns to band, an HTML/JSON export, OCR output,\n'
                 '   or two rounds of spec edits that did not converge. Write your own\n'
                 '   parser there. The record and the second gate survive; the\n'
                 '   implementation does not have to.\n\n'
                 '   If the fix turns out to be general, it is worth promoting back into\n'
                 '   the skill -- and that is the one case where the skill\'s own\n'
                 '   regression suite must pass first:\n\n'
                 '       python3 <skill-dir>/scripts/regress.py\n\n'
                 '## Not done until both gates pass\n\n'
                 'Gate 1 is the shape of the first rows. Gate 2 is the statement\'s own\n'
                 'arithmetic, and it is the only one that can see a dropped page or a\n'
                 'balance column read as an amount.\n\n'
                 '## What the probe measured\n\n```\n%s```\n'
                 % (os.path.basename(src), os.path.basename(parser), rel, name,
                    name, rel, os.path.basename(parser), report))

    w = sys.stdout.write
    w('\nworkspace: %s\n' % os.path.abspath(out))
    for f in COPY:
        w('  %-22s %s\n' % (f, '(kept, already edited here)' if f in kept else 'copied'))
    w('  %-22s written\n' % os.path.basename(parser))
    w('  %-22s written\n' % os.path.basename(notes))
    w('  %-22s copied  (a parser written from zero, if the spec is the wrong shape)\n'
      % 'from_scratch.py')
    if totals:
        w('\nthe statement prints these about itself -- they are the second gate:\n')
        for t in totals[:8]:
            w('  %s\n' % t.strip())
    w('\nnext:\n')
    w('  python3 %s %s -o %s.jsonl\n' % (os.path.basename(parser), rel, name))
    w('\nThese are copies. Edit them -- statement_lib.py included -- until this\n'
      'statement reconciles. Nothing outside this folder is affected.\n\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
