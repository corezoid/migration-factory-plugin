#!/usr/bin/env python3
"""Print the top of a document — at most a couple of pages of it.

    top.py <file> [--pages 2] [--chars 3000]

The lite run reads the head of a source and nothing else, and the head has to
be cut the same way every time: a model asked to "read about two pages" reads
whatever it feels like, and two runs of the same file then disagree about what
the source said. So the cut is made here, by the page for a PDF and by a
character budget for everything else, and the last line printed says how much
was left behind — that number is what the run reports as unread, and a run
that skipped 90% of a file should say so rather than imply the file was thin.

A .docx, .xlsx or .pptx is a zip of XML rather than text, so the head of one is
cut by `office.py` beside this script, under the same budget and with the same
last line. A non-zero exit means neither can open the format — an image, a
legacy .doc/.xls — and the message names what to use instead.
"""

import os
import re
import subprocess
import sys

PAGE_CHARS = 3000  # one "page" of plain text, near enough for a budget

PLAIN = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
         ".yaml", ".yml", ".eml", ".log", ".xml", ".rst"}
MARKUP = {".html", ".htm", ".xhtml"}
OFFICE = {".docx", ".xlsx", ".pptx"}  # zip of XML — office.py beside this script
# A complete sentence each: a refusal is only useful with the fix in it.
HANDOFF = {
    ".doc": "it is the legacy binary Word format and nothing here opens it — "
            "ask for the same file as .docx",
    ".xls": "it is the legacy binary Excel format and nothing here opens it — "
            "ask for the same file as .xlsx or .csv",
    ".ppt": "it is the legacy binary PowerPoint format and nothing here opens "
            "it — ask for the same file as .pptx",
    ".png": "it is an image — open it with the Read tool and stop at its head",
    ".jpg": "it is an image — open it with the Read tool and stop at its head",
    ".jpeg": "it is an image — open it with the Read tool and stop at its head",
    ".gif": "it is an image — open it with the Read tool and stop at its head",
    ".webp": "it is an image — open it with the Read tool and stop at its head",
}

TAG = re.compile(r"<[^>]+>")
DROP = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
BLANKS = re.compile(r"\n{3,}")


def pdf_text(path, pages):
    """First `pages` pages of a PDF, and whether there are more."""
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", "-f", "1", "-l", str(pages), path, "-"],
            capture_output=True, text=True, check=True).stdout
        total = pdf_pages(path)
    except FileNotFoundError:
        out, total = pdf_text_plumber(path, pages)
    except subprocess.CalledProcessError as e:
        sys.exit("top.py: pdftotext failed on %s: %s" % (path, e.stderr.strip()))
    return out, ("%d of %d pages" % (min(pages, total), total) if total else
                 "first %d pages" % pages)


def pdf_text_plumber(path, pages):
    """The same cut without poppler. Kept in here rather than left to the
    caller so the head of a PDF is the same text on any machine."""
    try:
        import pdfplumber
    except ImportError:
        sys.exit("top.py: neither pdftotext nor pdfplumber is available — "
                 "install one, or read %s yourself and stop after page %d"
                 % (path, pages))
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        text = "\n".join((page.extract_text() or "") for page in pdf.pages[:pages])
    return text, total


def pdf_pages(path):
    """Page count, or 0 when pdfinfo cannot say — it is only for the note."""
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True,
                             check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return 0
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def office_head(path, limit):
    """Hand a .docx/.xlsx/.pptx to office.py under this run's budget.

    Its output already carries the same trailing note, so it goes straight
    through: two budgets over one document would each report a different
    remainder, and the run has one number to report.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "office.py")
    done = subprocess.run([sys.executable, script, path, "--chars", str(limit)],
                          capture_output=True, text=True)
    sys.stdout.write(done.stdout)
    if done.returncode:
        sys.exit(done.stderr.strip() or
                 "top.py: office.py could not read %s" % os.path.basename(path))


def budget(text, limit):
    """`limit` characters of text, cut at the last line break before it."""
    if len(text) <= limit:
        return text, len(text)
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:  # one very long line: cut it mid-line rather than take it whole
        cut = limit
    return text[:cut], len(text)


def main():
    args = [a for a in sys.argv[1:]]
    pages, chars = 2, PAGE_CHARS
    for flag, setter in (("--pages", "pages"), ("--chars", "chars")):
        if flag in args:
            i = args.index(flag)
            value = int(args[i + 1])
            args = args[:i] + args[i + 2:]
            if setter == "pages":
                pages = value
            else:
                chars = value
    if len(args) != 1:
        sys.exit(__doc__)

    path = args[0]
    if not os.path.isfile(path):
        sys.exit("top.py: no such file: %s" % path)
    ext = os.path.splitext(path)[1].lower()

    if ext in HANDOFF:
        sys.exit("top.py: cannot read %s here — %s" % (ext, HANDOFF[ext]))

    limit = chars * pages
    if ext in OFFICE:
        return office_head(path, limit)
    if ext == ".pdf":
        text, note = pdf_text(path, pages)
        text, _ = budget(text, limit * 4)  # a page of PDF can be dense; the page cut already held
        left = note
    else:
        raw = open(path, encoding="utf-8", errors="replace").read()
        if ext in MARKUP:
            raw = BLANKS.sub("\n\n", TAG.sub(" ", DROP.sub(" ", raw)))
        elif ext not in PLAIN:
            # Unknown extension, but it opened as text — treat it as text
            # rather than refuse: a saved page or an export with no suffix is
            # still readable, and the budget below bounds the damage.
            pass
        text, total = budget(raw, limit)
        left = ("all %d characters" % total if len(text) == total else
                "%d of %d characters" % (len(text), total))

    sys.stdout.write(text.rstrip() + "\n")
    print("\n--- top.py read %s of %s ---" % (left, os.path.basename(path)))


if __name__ == "__main__":
    main()
