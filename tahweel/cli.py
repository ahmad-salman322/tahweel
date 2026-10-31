"""Command line: `tahweel file.pdf ...` converts, `tahweel` alone opens the window."""

import argparse
import os
import subprocess
import sys

from . import DEFAULT_TARGET, __version__, convert, detect, normalize, output_path

FORMAT_HELP = "word|docx, pdf, md|markdown"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == ["--gui"]:
        from .app import run
        return run([])
    if argv[0] == "--gui":
        from .app import run
        return run(argv[1:])

    ap = argparse.ArgumentParser(
        prog="tahweel",
        description="Convert between PDF, Word (.docx) and Markdown; Hebrew/Arabic text keeps its direction.",
        epilog="Default target: PDF → Word, Word → PDF, Markdown → Word. "
               "PDF output needs LibreOffice. Run without arguments to open the window.")
    ap.add_argument("files", nargs="+", metavar="FILE", help=".pdf, .docx or .md")
    ap.add_argument("-t", "--to", metavar="FORMAT", help=f"target format: {FORMAT_HELP}")
    ap.add_argument("-o", "--output", help="output file (single input) or folder")
    ap.add_argument("-f", "--force", action="store_true", help="overwrite an existing output file")
    ap.add_argument("--no-background", action="store_true", help="PDF input: drop full-page letterhead images")
    ap.add_argument("--open", action="store_true", help="open the result when done")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--version", action="version", version=f"tahweel {__version__}")
    args = ap.parse_args(argv)

    try:
        to = normalize(args.to)
    except ValueError:
        ap.error(f"--to must be one of: {FORMAT_HELP}")
    out = args.output
    if out and len(args.files) > 1 and not os.path.isdir(out):
        ap.error("--output must be a folder when converting several files")
    if out and not os.path.isdir(out) and to is None:
        to = detect(out)  # "-o notes.md" picks the format
        if to is None:
            ap.error("cannot tell the format from --output; add --to")

    failed = 0
    for src in args.files:
        fmt = detect(src)
        if not os.path.isfile(src):
            print(f"✗ {src}: not found", file=sys.stderr)
            failed += 1
            continue
        if fmt is None:
            print(f"✗ {src}: not a .pdf, .docx or .md file", file=sys.stderr)
            failed += 1
            continue
        target = to or DEFAULT_TARGET[fmt]
        if target == fmt:
            print(f"✗ {src}: already {fmt.upper()} (pick another format with --to)", file=sys.stderr)
            failed += 1
            continue
        dest = out if out and not os.path.isdir(out) else output_path(src, out, overwrite=args.force, to=target)

        def progress(done, total, name=os.path.basename(src)):
            if not args.quiet and sys.stderr.isatty() and total > 1:
                print(f"\r  {name}: {done}/{total}", end="", file=sys.stderr, flush=True)

        try:
            doc = convert(src, dest, progress=progress, keep_background=not args.no_background, to=target)
        except Exception as e:  # noqa: BLE001 - report and continue with the next file
            print(f"\r✗ {src}: {explain(e)}", file=sys.stderr)
            failed += 1
            continue
        if not args.quiet:
            note = "  (no text layer: scanned PDF?)" if "no-text" in doc.warnings else ""
            print(f"\r✓ {dest}{note}" + " " * 10)
        if args.open:
            subprocess.Popen(["xdg-open", dest], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 1 if failed else 0


def explain(e):
    msg = str(e)
    if isinstance(e, ValueError) and msg == "encrypted":
        return "password-protected PDF"
    if msg == "no-libreoffice":
        return "PDF output needs LibreOffice (soffice) installed"
    if isinstance(e, ValueError) and msg == "same-format":
        return "source and target are the same format"
    return f"{type(e).__name__}: {e}"


if __name__ == "__main__":
    sys.exit(main())
