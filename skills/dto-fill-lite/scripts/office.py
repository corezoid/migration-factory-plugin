#!/usr/bin/env python3
"""Read a document `cat` cannot — any Office, OpenDocument, RTF, Outlook .msg
or raw HTML file — as plain text.

    office.py <file> [--chars N] [--rows N] [--notes]

    --chars   stop after N characters (0, the default, reads it whole)
    --rows    stop after N rows per sheet (0, the default, reads them all)
    --notes   .pptx only: also print the speaker notes under each slide

Text formats need nothing — `cat` is the whole story for .txt, .md, .csv,
.json, .eml or a saved page, and a PDF has `pdftotext -layout`. Everything
here is a binary container of one kind or another, so each family needs its
own way in:

  .docx .xlsx .pptx  zip archives of XML — opened directly, .docx and .pptx
                      on the standard library alone, .xlsx through openpyxl
  .doc .ppt .xls      OLE or ODF containers no pure-Python reader here
  .odt .ods .odp      parses; LibreOffice converts each to the OOXML sibling
  .rtf                above and the same reader takes it from there
  .msg                an Outlook message, read with extract-msg
  .html .htm          a raw saved page (not one read_page already rendered to
                      Markdown) — read with the standard library alone

A run stopping to install something used to be the reason the legacy formats
were refused by name — the image had neither python-docx nor LibreOffice, and
a run that stalls on a network install is worse than one that names the
format and stops. That constraint is gone: install what a reader asks for and
retry. Every reader that needs something not on the standard library says the
exact line to run rather than raising a traceback partway through a document.
The line also says *where* the install goes, which is not the same answer on
every host: a runtime rebuilt from its image at each restart keeps its packages
on a volume instead, and a package put beside them rather than into the
container is one the next run does not pay for again.

The last line says how much was left unread — a run that saw two sheets of
nine has to be able to say so rather than imply the workbook was thin.
"""

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
HTML_BREAKS = [("</p>", "\n\n"), ("<br>", "\n"), ("<br/>", "\n"), ("<br />", "\n"),
               ("</tr>", "\n"), ("</td>", "\t"), ("</th>", "\t"),
               ("</li>", "\n"), ("</h1>", "\n\n"), ("</h2>", "\n\n"),
               ("</h3>", "\n\n"), ("</div>", "\n")]
HTML_DROP = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.I | re.S)

SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
NOTES = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
HEADER = re.compile(r"^word/header(\d*)\.xml$")
FOOTER = re.compile(r"^word/footer(\d*)\.xml$")


# --------------------------------------------------------------------------
# where an install goes
# --------------------------------------------------------------------------

def persistent_site():
    """The directory on this host where an installed package survives a restart.

    The runtimes this plugin runs in rebuild their filesystem from the image
    every time the process comes back, so a package installed into the
    container is gone by the next run and the run after it pays to install it
    again. A host that has solved that says so in PYTHONPATH: a writable
    directory named there is a volume outliving the container. Nothing
    writable there is an ordinary machine, where the plain pip line holds.
    """
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        entry = entry.strip()
        if entry and os.path.isdir(entry) and os.access(entry, os.W_OK):
            return entry
    return ""


def pip_line(package):
    """The command that installs `package` where it is still there next run."""
    site = persistent_site()
    if not site:
        return "python3 -m pip install %s" % package
    # uv rather than pip: a gateway image that keeps its packages on a volume
    # ships uv and no pip at all, and --target is what puts the package on the
    # volume instead of inside the container.
    return "uv pip install --target %s %s" % (site, package)


def tool_hint(package):
    """What to say when a system tool — not a python package — is missing.

    `apt-get install` is the wrong advice on a host with such a volume twice
    over: the run is not root there, and even as root the tool would be gone
    at the next restart. A host like that documents where its tools go, and
    pointing at that beats printing a command that cannot work.
    """
    site = persistent_site()
    if not site:
        return ("    apt-get install -y %s   (Debian/Ubuntu)\n"
                "    brew install --cask %s  (macOS)" % (package, package))
    root = os.path.dirname(site.rstrip(os.sep))
    notes = os.path.join(root, "DEPENDENCIES.md")
    if os.path.isfile(notes):
        return ("    anything installed into this container is gone at the next\n"
                "    restart, so tools live on the volume instead — %s\n"
                "    says how one is put there" % notes)
    return ("    anything installed into this container is gone at the next\n"
            "    restart: unpack it under %s and put a wrapper on PATH, the way\n"
            "    the packages already there were" % root)


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
        sys.exit("office.py: openpyxl is required to read .xlsx\n    "
                 + pip_line("openpyxl"))
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


def read_msg(path, opts):
    """An Outlook .msg — an OLE compound file, not a zip, so it needs its own
    reader rather than the zip trick above. extract-msg is pure Python and
    small; the pip line is the whole ask when it is missing.
    """
    try:
        import extract_msg
    except ImportError:
        sys.exit("office.py: extract-msg is required to read .msg\n    "
                 + pip_line("extract-msg"))
    msg = extract_msg.Message(path)
    try:
        head = "\n".join(
            "%s: %s" % (label, value) for label, value in (
                ("From", msg.sender), ("To", msg.to), ("Cc", msg.cc),
                ("Date", msg.date), ("Subject", msg.subject))
            if value)
        body = (msg.body or "").strip()
        note = ("%s not extracted" % plural(len(msg.attachments), "attachment")
                if msg.attachments else None)
        return "\n\n".join(c for c in (head, body) if c), note
    finally:
        msg.close()


def read_html(path, opts):
    """A raw saved page a user attached — not one `read_page` already rendered
    to Markdown. `cat` on it prints the tags, so this drops the script/style
    bodies (code, not content) and runs the same tag-to-text pass as above.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    return text_of(HTML_DROP.sub(" ", raw), HTML_BREAKS), None


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


def via_libreoffice(path, opts, target_ext, reader):
    """Convert a legacy or OpenDocument file to `target_ext`, then hand it to
    the reader that already knows that format.

    One external tool covers seven formats that would otherwise need seven
    readers: .doc/.ppt/.xls are OLE compound files, .odt/.ods/.odp are ODF — a
    different XML dialect from OOXML — and neither family has a pure-Python
    reader in this file. LibreOffice reads both natively and writes the OOXML
    this script already understands, so the conversion is the whole fix.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        sys.exit(
            "office.py: %s needs LibreOffice to convert — install it and retry\n%s"
            % (os.path.basename(path), tool_hint("libreoffice")))
    with tempfile.TemporaryDirectory() as tmp:
        run = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", target_ext,
             "--outdir", tmp, path],
            capture_output=True, text=True, timeout=180)
        out = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0]
                            + "." + target_ext)
        if run.returncode or not os.path.isfile(out):
            sys.exit("office.py: LibreOffice could not convert %s to .%s: %s"
                     % (os.path.basename(path), target_ext,
                        (run.stderr or run.stdout).strip() or "no output produced"))
        text, note = reader(out, opts)
    via = "read via LibreOffice conversion from %s" % os.path.splitext(path)[1]
    return text, (via + "; " + note if note else via)


# Zip archives read directly: format checked with zipfile.is_zipfile below.
READERS = {".docx": read_docx, ".xlsx": read_xlsx, ".pptx": read_pptx,
           ".msg": read_msg, ".html": read_html, ".htm": read_html}

# Legacy binary and OpenDocument containers: converted to the OOXML sibling
# reader() already knows, rather than parsed here directly.
LIBREOFFICE = {
    ".doc": ("docx", read_docx), ".odt": ("docx", read_docx),
    ".rtf": ("docx", read_docx),
    ".ppt": ("pptx", read_pptx), ".odp": ("pptx", read_pptx),
    ".xls": ("xlsx", read_xlsx), ".ods": ("xlsx", read_xlsx),
}


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

    if ext in LIBREOFFICE:
        target_ext, reader = LIBREOFFICE[ext]
        text, note = via_libreoffice(path, opts, target_ext, reader)
    elif ext in READERS:
        if ext in (".docx", ".xlsx", ".pptx") and not zipfile.is_zipfile(path):
            sys.exit("office.py: %s is not a zip archive — a .docx/.xlsx/.pptx "
                     "always is, so this file is something else wearing the "
                     "extension" % os.path.basename(path))
        text, note = READERS[ext](path, opts)
    else:
        sys.exit("office.py: %s is not a format this reads — .txt .md .csv "
                 ".json .eml and saved pages read with `cat`, a .pdf with "
                 "`pdftotext -layout`, an image with the Read tool" % (ext or path))

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
