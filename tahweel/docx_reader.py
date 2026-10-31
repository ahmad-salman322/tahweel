"""Read a .docx into a Document: headings, lists (with their real numbers), tables, links, images."""

import os
import re

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph as DocxParagraph

from .bidi import strong_counts
from .markdown_io import first_strong_rtl
from .model import Document, Image, PageBreak, Paragraph, Rule, Run, Table

EMU_PER_PT = 12700
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
MONO_FONTS = re.compile(r"consol|courier|mono|menlo|monaco|cascadia|fira code|source code|jetbrains|inconsolata",
                        re.I)
HEADING_STYLE = re.compile(r"^(?:heading|כותרת|عنوان)\s*(\d)$", re.I)
TEXT_LABEL = re.compile(r"^\s*([^\s\t]{1,8})\t")  # "1.<tab>", "א-<tab>", "•<tab>" typed as text
BULLET_LABEL = re.compile(r"^[•·▪◦●○■□➢►✓✔\-–*]$")
NUMBER_LABEL = re.compile(r"^\(?\d+[.)]?$")
HEBREW_LETTERS = "אבגדהוזחטיכלמנסעפצקרשת"
ARABIC_LETTERS = "أبتثجحخدذرزسشصضطظعغفقكلمنهوي"
ARABIC_ABJAD = "أبجدهوزحطيكلمنسعفصقرشتثخذضظغ"


def _on(el, tag):
    """True if the boolean property `tag` is present and not switched off."""
    if el is None:
        return False
    found = el.find(qn(tag))
    return found is not None and found.get(qn("w:val"), "true") not in ("0", "false", "off")


def _roman(n):
    out = ""
    for value, sym in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
                       (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += sym
            n -= value
    return out


def _letters(n, alphabet):
    s = ""
    while n > 0:
        n -= 1
        s = alphabet[n % len(alphabet)] + s
        n //= len(alphabet)
    return s


def _format_number(n, fmt):
    if fmt in ("lowerLetter",):
        return _letters(n, "abcdefghijklmnopqrstuvwxyz")
    if fmt == "upperLetter":
        return _letters(n, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt in ("hebrew1", "hebrew2"):
        return _letters(n, HEBREW_LETTERS)
    if fmt == "arabicAlpha":
        return _letters(n, ARABIC_LETTERS)
    if fmt == "arabicAbjad":
        return _letters(n, ARABIC_ABJAD)
    return str(n)


class _Numbering:
    """Turns w:numPr (numId + ilvl) into the label Word would draw, counting as it goes."""

    def __init__(self, docx):
        self.levels = {}  # numId -> {ilvl: (fmt, text, start)}
        self.counters = {}  # abstract id -> [counts per level]
        self.abstract_of = {}
        try:
            root = docx.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return
        abstract = {}
        for an in root.findall(qn("w:abstractNum")):
            lv = {}
            for lvl in an.findall(qn("w:lvl")):
                ilvl = int(lvl.get(qn("w:ilvl"), "0"))
                fmt = lvl.find(qn("w:numFmt"))
                text = lvl.find(qn("w:lvlText"))
                start = lvl.find(qn("w:start"))
                lv[ilvl] = (fmt.get(qn("w:val")) if fmt is not None else "decimal",
                            text.get(qn("w:val")) if text is not None else f"%{ilvl + 1}.",
                            int(start.get(qn("w:val"))) if start is not None else 1)
            abstract[an.get(qn("w:abstractNumId"))] = lv
        for num in root.findall(qn("w:num")):
            ref = num.find(qn("w:abstractNumId"))
            if ref is not None:
                aid = ref.get(qn("w:val"))
                self.levels[num.get(qn("w:numId"))] = abstract.get(aid, {})
                self.abstract_of[num.get(qn("w:numId"))] = aid

    def label(self, num_id, ilvl):
        levels = self.levels.get(num_id)
        if not levels or num_id == "0":
            return None, None
        fmt, text, _start = levels.get(ilvl, ("decimal", f"%{ilvl + 1}.", 1))
        if fmt == "bullet":
            return "bullet", "•"
        if fmt == "none":
            return None, None
        key = self.abstract_of.get(num_id, num_id)
        counts = self.counters.setdefault(key, [0] * 9)
        if counts[ilvl] == 0:
            counts[ilvl] = levels.get(ilvl, ("", "", 1))[2]
        else:
            counts[ilvl] += 1
        for deeper in range(ilvl + 1, 9):
            counts[deeper] = 0

        def sub(m):
            k = int(m.group(1)) - 1
            lf = levels.get(k, ("decimal", "", 1))[0]
            n = counts[k] or levels.get(k, ("", "", 1))[2]
            return _format_number(n, lf if k == ilvl else ("decimal" if lf == "bullet" else lf))

        return "number", re.sub(r"%(\d)", sub, text)


class _DocxReader:
    def __init__(self, path):
        self.d = DocxDocument(path)
        self.numbering = _Numbering(self.d)
        self.doc = Document()
        sec = self.d.sections[0]
        pt = lambda v, dflt: v.pt if v is not None else dflt  # noqa: E731
        self.doc.page_width = pt(sec.page_width, 595.3)
        self.doc.page_height = pt(sec.page_height, 841.9)
        self.doc.margins = (pt(sec.left_margin, 72), pt(sec.right_margin, 72), pt(sec.top_margin, 72),
                            pt(sec.bottom_margin, 72))
        normal = self.d.styles["Normal"]
        if normal.font.name:
            self.doc.body_font = normal.font.name
        if normal.font.size:
            self.doc.body_size = normal.font.size.pt

    # ---------- runs ----------
    def _run(self, r, link=None):
        text = r.text
        if not text:
            return None
        font = r.font
        color = None
        try:
            if font.color is not None and font.color.type is not None and font.color.rgb is not None:
                color = str(font.color.rgb)
                if color in ("000000", "auto"):
                    color = None
        except (AttributeError, ValueError):
            color = None
        name = font.name or (r.style.font.name if r.style is not None else None)
        return Run(text, bold=bool(r.bold), italic=bool(r.italic), underline=bool(r.underline) and not link,
                   strike=bool(font.strike), color=color, size=font.size.pt if font.size else None,
                   font=name, mono=bool(name and MONO_FONTS.search(name)), link=link)

    def _images(self, r_el):
        out = []
        for drawing in r_el.iter(qn("w:drawing")):
            anchor = drawing.find(qn("wp:anchor"))
            if anchor is not None and anchor.get("behindDoc") == "1":
                continue  # page background / letterhead, not content
            if next(drawing.iter(qn("w:txbxContent")), None) is not None:
                continue  # a text box: its text is read separately
            blip = next(drawing.iter(qn("a:blip")), None)
            ext = next(drawing.iter(qn("wp:extent")), None)
            if blip is None:
                continue
            rid = blip.get(qn("r:embed"))
            part = self.d.part.related_parts.get(rid)
            if part is None:
                continue
            w = int(ext.get("cx")) / EMU_PER_PT if ext is not None else 200
            h = int(ext.get("cy")) / EMU_PER_PT if ext is not None else 150
            out.append(Image(data=part.blob, ext=os.path.splitext(str(part.partname))[1].lstrip(".") or "png",
                             width=w, height=h, space_before=6))
        return out

    def _text_boxes(self, r_el, parent):
        """Paragraphs inside text boxes anchored in this run (the VML fallback copy is skipped)."""
        out = []
        for box in r_el.iter(qn("w:txbxContent")):
            anc = box.getparent()
            fallback = False
            while anc is not None and anc is not r_el:
                if anc.tag == MC_FALLBACK:
                    fallback = True
                    break
                anc = anc.getparent()
            if fallback:
                continue
            for child in box:
                if child.tag == qn("w:p"):
                    out += self.paragraph(DocxParagraph(child, parent))
                elif child.tag == qn("w:tbl"):
                    out.append(self.table(DocxTable(child, parent)))
        return out

    # ---------- paragraphs ----------
    def paragraph(self, p):
        """Return the blocks for one Word paragraph (the paragraph itself, then its pictures)."""
        runs, images, boxes, page_break = [], [], [], False
        for item in p.iter_inner_content():
            if isinstance(item, Hyperlink):
                url = item.url or (f"#{item.fragment}" if item.fragment else None)
                for r in item.runs:
                    run = self._run(r, url)
                    if run:
                        runs.append(run)
                continue
            images += self._images(item._r)
            boxes += self._text_boxes(item._r, p._parent)
            if any(br.get(qn("w:type")) == "page" for br in item._r.iter(qn("w:br"))):
                page_break = True
            run = self._run(item)
            if run:
                runs.append(run)

        ppr = p._p.pPr
        style = p.style.name if p.style is not None else ""
        text = "".join(r.text for r in runs)
        para = Paragraph(runs=runs)
        if ppr is not None and ppr.find(qn("w:bidi")) is not None:
            para.rtl = _on(ppr, "w:bidi")
        else:
            style_ppr = p.style.element.pPr if p.style is not None else None
            para.rtl = _on(style_ppr, "w:bidi") or first_strong_rtl(text)

        m = HEADING_STYLE.match(style)
        if m:
            para.heading = int(m.group(1))
        elif style == "Title":
            para.heading = 1
        elif style == "Subtitle":
            para.heading = 2
        if re.search(r"quote|ציטוט|اقتباس", style, re.I):
            para.quote = True
        if re.search(r"code|preformatted|source", style, re.I) or (
                text.strip() and all(r.mono for r in runs if r.text.strip())):
            para.code = True
            para.shade = "F4F5F7"

        jc = ppr.find(qn("w:jc")) if ppr is not None else None
        jc = jc.get(qn("w:val")) if jc is not None else None
        # bidi paragraphs mirror left/right, so in both directions "right"/"end" is the trailing edge
        para.align = {"center": "center", "right": "end", "end": "end"}.get(jc, "start")

        num = self._numpr(p)
        if num is not None:
            kind, label = self.numbering.label(*num)
            if label is not None:
                para.list_kind = kind
                para.level = num[1]
                para.label = [Run(label)]
                para.indent = 22.0 * (num[1] + 1)
                para.hanging = 18.0
        else:
            if p.paragraph_format.left_indent:
                para.indent = max(0.0, p.paragraph_format.left_indent.pt)
            m = TEXT_LABEL.match(text) if runs else None
            if m and not para.code:
                label = m.group(1)
                self._strip_prefix(runs, m.end())
                para.label = [Run(label)]
                para.hanging = 18.0
                if BULLET_LABEL.match(label):
                    para.list_kind = "bullet"
                elif NUMBER_LABEL.match(label):
                    para.list_kind = "number"
                para.level = max(0, round(para.indent / 22.0) - 1)
                text = "".join(r.text for r in runs)

        bdr = ppr.find(qn("w:pBdr")) if ppr is not None else None
        if bdr is not None:
            sides = {child.tag.split("}")[1] for child in bdr}
            if not text.strip() and sides & {"bottom", "top"}:
                return [Rule()]
            if sides & {"left", "right"} and not sides & {"top", "bottom"}:
                para.quote = True
                para.indent = max(0.0, para.indent - 18.0)
                for r in runs:
                    if r.color == "595959":  # the colour our writer gives quotes
                        r.color = None

        shd = ppr.find(qn("w:shd")) if ppr is not None else None
        if shd is not None and shd.get(qn("w:fill"), "auto") not in ("auto", "FFFFFF"):
            para.shade = shd.get(qn("w:fill"))

        out = []
        if text.strip() or para.label:
            out.append(para)
        out += boxes + images
        if page_break:
            out.append(PageBreak())
        return out

    @staticmethod
    def _strip_prefix(runs, n):
        while n and runs:
            if len(runs[0].text) <= n:
                n -= len(runs[0].text)
                runs.pop(0)
            else:
                runs[0].text = runs[0].text[n:]
                n = 0

    def _numpr(self, p):
        for ppr in (p._p.pPr, p.style.element.pPr if p.style is not None else None):
            if ppr is None:
                continue
            numpr = ppr.find(qn("w:numPr"))
            if numpr is not None:
                nid = numpr.find(qn("w:numId"))
                lvl = numpr.find(qn("w:ilvl"))
                if nid is not None:
                    return nid.get(qn("w:val")), int(lvl.get(qn("w:val"))) if lvl is not None else 0
        return None

    # ---------- tables ----------
    def table(self, t):
        rows = []
        for row in t.rows:
            cells, seen = [], set()
            for cell in row.cells:
                if id(cell._tc) in seen:  # merged cell: show its text once
                    cells.append([])
                    continue
                seen.add(id(cell._tc))
                paras = []
                for cp in cell.paragraphs:
                    paras += [b for b in self.paragraph(cp) if isinstance(b, Paragraph)]
                cells.append(paras)
            rows.append(cells)
        tblpr = t._tbl.tblPr
        align = None
        if rows:
            align = [c[0].align if c else "start" for c in rows[0]]
            for c in rows[0]:  # header cells are bold by convention; don't carry that as formatting
                for p in c:
                    for r in p.runs:
                        r.bold = False
        text = " ".join(p.text for r in rows for c in r for p in c)
        rtl_n, ltr_n = strong_counts(text)
        return Table(rows=rows, header_rows=1, rtl=_on(tblpr, "w:bidiVisual") or rtl_n > ltr_n, align=align)

    def read(self):
        blocks = self.doc.blocks
        for item in self.d.iter_inner_content():
            if isinstance(item, DocxParagraph):
                blocks += self.paragraph(item)
            elif isinstance(item, DocxTable):
                blocks.append(self.table(item))
        paras = [b for b in blocks if isinstance(b, Paragraph)]
        self.doc.rtl = sum(p.rtl for p in paras) * 2 > len(paras)
        self.doc.title = self.d.core_properties.title or next((p.text for p in paras if p.heading), "")
        if not paras:
            self.doc.warnings.append("empty")
        return self.doc


def read_docx(path, progress=None):
    if progress:
        progress(0, 1)
    doc = _DocxReader(path).read()
    if progress:
        progress(1, 1)
    return doc
