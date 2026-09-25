#!/usr/bin/env python3
"""Read a .docx, .xlsx or .pptx as plain text.

    office.py <file> [--chars N] [--rows N] [--notes]

    --chars   stop after N characters (0, the default, reads it whole)
    --rows    stop after N rows per sheet (0, the default, reads them all)
    --notes   .pptx only: also print the speaker notes under each slide

Text formats need nothing — `cat` is the whole story for .txt, .md, .csv,
.json, .eml or a saved page, and a PDF has `pdftotext -layout`. These three are
neither: they are zip archives of XML, and `cat` on one prints binary. So this
opens the zip and prints what a reader would see, on stdout, so the source
arrives as text like every other source.

.docx and .pptx cost nothing to open — zipfile and the standard library are the
whole dependency, which is the point: the image has no python-docx and no
python-pptx, and a run that stops to install one is a run that stops. .xlsx
goes through openpyxl, which is on the image; if it ever is not, the message is
the pip line rather than a traceback 300 rows in.

The last line says how much was left unread — a run that saw two sheets of nine
has to be able to say so rather than imply the workbook was thin.
"""

import html
import os
import re
import sys
import warnings
import zipfile

TAG = re.compile(r"<[^>]+>")
BLANKS = re.compile(r"\n{3,}")
TRAILING = re.compile(r"[ \t]+$", re.M)

# A closing tag is where a line or a cell ends; the text itself carries no
# separators of its own, so without these the whole document arrives as one
# run-on line and a table loses which value sat in which column.
DOCX_BREAKS = [("</w:tc>", "\t"), ("</w:tr>", "\n"), ("</w:p>", "\n"),
               ("<w:br/>", "\n"), ("<w:br />", "\n"),
               ("<w:tab/>", "\t"), ("<w:tab />", "\t")]
PPTX_BREAKS = [("</a:tc>", "\t"), ("</a:tr>", "\n"), ("</a:p>", "\n"),
               ("<a:br/>", "\n"), ("<a:br />", "\n")]

SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
NOTES = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
HEADER = re.compile(r"^word/header(\d*)\.xml$")
FOOTER = re.compile(r"^word/footer(\d*)\.xml$")

LEGACY = {
    ".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx",
    ".odt": ".docx", ".ods": ".xlsx", ".odp": ".pptx",
}


def text_of(xml, breaks):
    """XML → the text a reader sees, with the breaks that carry the layout."""
    for tag, sep in breaks:
        xml = xml.replace(tag, sep)
    out = html.unescape(TAG.sub("", xml))
    # A cell's own paragraph break lands just before the cell separator; left
    # in, every table cell would start its own line and the row would be gone.
    out = re.sub(r"\n+\t", "\t", out)
    out = re.sub(r"\t+\n", "\n", out)
    return BLANKS.sub("\n\n", TRAILING.sub("", out)).strip()


def read_docx(path, opts):
    """Headers, body, footers — in that order, and the order is the point.

    Pass 0 of dto-fill decides whose facts a document holds from the letterhead
    and the registry footer, and both live in their own parts of the archive:
    a body-only read hands the model a document with no issuer on it.
    """
    parts = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "word/document.xml" not in names:
            sys.exit("office.py: %s is a zip but not a .docx (no word/document.xml)"
                     % os.path.basename(path))
        for name in sorted(n for n in names if HEADER.match(n)):
            parts.append((name.rsplit("/", 1)[1], z.read(name)))
        parts.append(("document", z.read("word/document.xml")))
        for name in sorted(n for n in names if FOOTER.match(n)):
            parts.append((name.rsplit("/", 1)[1], z.read(name)))
    chunks = []
    for label, raw in parts:
        body = text_of(raw.decode("utf-8", "replace"), DOCX_BREAKS)
        if not body:
            continue
        chunks.append(body if label == "document" else
                      "--- %s ---\n%s" % (label, body))
    return "\n\n".join(chunks), None


def read_pptx(path, opts):
    """One block per slide, in slide order; notes only when asked for."""
    with zipfile.ZipFile(path) as z:
        slides = sorted((int(m.group(1)), m.group(0))
                        for m in (SLIDE.match(n) for n in z.namelist()) if m)
        notes = dict((int(m.group(1)), m.group(0))
                     for m in (NOTES.match(n) for n in z.namelist()) if m)
        if not slides:
            sys.exit("office.py: %s is a zip but not a .pptx (no ppt/slides/)"
                     % os.path.basename(path))
        chunks = []
        for number, name in slides:
            body = text_of(z.read(name).decode("utf-8", "replace"), PPTX_BREAKS)
            block = "--- slide %d ---\n%s" % (number, body or "(no text)")
            if opts["notes"] and number in notes:
                said = text_of(z.read(notes[number]).decode("utf-8", "replace"),
                               PPTX_BREAKS)
                if said:
                    block += "\n--- slide %d notes ---\n%s" % (number, said)
            chunks.append(block)
    held = len(notes) if notes and not opts["notes"] else 0
    return "\n\n".join(chunks), (
        "%s with speaker notes not read — pass --notes for them"
        % plural(held, "slide") if held else None)


def read_xlsx(path, opts):
    """Sheets as tab-separated rows.

    `data_only=True` prints what the sheet shows, not its formulas — which is
    the cached result, so a workbook written by a library that never cached one
    shows the cell empty. That is why the note below counts what came back: a
    workbook that reads as all-blank is that case, not an empty workbook.
    """
    try:
        import openpyxl
    except ImportError:
        sys.exit("office.py: openpyxl is required to read .xlsx\n"
                 "    python3 -m pip install openpyxl")
    # openpyxl warns on stderr about drawing and formatting extensions it drops.
    # None of that is text, and a warning printed beside the sheet reads as if
    # something went wrong with the values.
    warnings.filterwarnings("ignore", module=r"openpyxl\..*")
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    chunks, values, clipped = [], 0, []
    try:
        for sheet in book.worksheets:
            lines, seen = [], 0
            for row in sheet.iter_rows(values_only=True):
                seen += 1
                if opts["rows"] and len(lines) >= opts["rows"]:
                    clipped.append("%s (%s read)"
                                   % (sheet.title, plural(len(lines), "row")))
                    break
                cells = [cell(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if not cells:
                    continue
                values += sum(1 for c in cells if c)
                lines.append("\t".join(cells))
            head = "--- sheet: %s (%s) ---" % (
                sheet.title, plural(sheet.max_row or seen, "row"))
            chunks.append(head + "\n" + ("\n".join(lines) or "(empty)"))
    finally:
        book.close()
    note = None
    if clipped:
        note = "stopped at --rows in: " + "; ".join(clipped)
    elif not values:
        note = ("every cell came back empty — if the sheet is not empty its "
                "values are uncached formulas, and the file has to be opened "
                "and saved by a spreadsheet before they exist")
    return "\n\n".join(chunks), note


def plural(count, noun):
    """A note that says "1 slides" reads as a bug in the note."""
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def cell(value):
    """One cell as the sheet shows it, not as Python repr()s it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value)
    # A date cell arrives as a datetime at midnight; printing 00:00:00 after
    # every date turns a column of dates into noise.
    return text[:10] if text.endswith(" 00:00:00") else text


READERS = {".docx": read_docx, ".xlsx": read_xlsx, ".pptx": read_pptx}


def main():
    args = list(sys.argv[1:])
    opts = {"chars": 0, "rows": 0, "notes": False}
    if "--notes" in args:
        args.remove("--notes")
        opts["notes"] = True
    for flag in ("--chars", "--rows"):
        if flag in args:
            i = args.index(flag)
            opts[flag[2:]] = int(args[i + 1])
            args = args[:i] + args[i + 2:]
    if len(args) != 1:
        sys.exit(__doc__)

    path = args[0]
    if not os.path.isfile(path):
        sys.exit("office.py: no such file: %s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext in LEGACY:
        sys.exit("office.py: %s is the legacy binary format and nothing here "
                 "opens it — ask for the same file as %s, or convert it"
                 % (ext, LEGACY[ext]))
    if ext not in READERS:
        sys.exit("office.py: %s is not an Office file — .txt .md .csv .json "
                 ".eml and saved pages read with `cat`, a .pdf with "
                 "`pdftotext -layout`, an image with the Read tool" % (ext or path))
    if not zipfile.is_zipfile(path):
        sys.exit("office.py: %s is not a zip archive — a .docx/.xlsx/.pptx "
                 "always is, so this file is something else wearing the "
                 "extension" % os.path.basename(path))

    text, note = READERS[ext](path, opts)
    total = len(text)
    if opts["chars"] and total > opts["chars"]:
        cut = text.rfind("\n", 0, opts["chars"])
        text = text[:cut if cut > opts["chars"] // 2 else opts["chars"]]
        read = "%d of %d characters" % (len(text), total)
    else:
        read = "all %d characters" % total

    sys.stdout.write(text.rstrip() + "\n")
    print("\n--- office.py read %s of %s%s ---"
          % (read, os.path.basename(path), "; " + note if note else ""))


if __name__ == "__main__":
    main()
