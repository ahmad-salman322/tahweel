"""Print the paragraphs tahweel extracts from a PDF (debug helper)."""
import sys

from tahweel.extract import read_pdf
from tahweel.model import Paragraph

d = read_pdf(sys.argv[1])
print("rtl", d.rtl, "margins", [round(m) for m in d.margins], "bg", bool(d.background),
      "pages#", d.page_numbers, d.page_number_align, d.body_font, d.body_size, d.warnings)
for b in d.blocks:
    if isinstance(b, Paragraph):
        lab = "".join(r.text for r in b.label) if b.label else ""
        flags = ("C" if b.code else "") + b.align[0] + ("R" if b.rtl else "L")
        print(f"{flags:4} in={b.indent:5.1f} h={b.hanging:4.1f} sb={b.space_before:4.1f} ls={b.line_spacing} "
              f"[{lab}] {b.text[:150]}")
    else:
        print(type(b).__name__)
