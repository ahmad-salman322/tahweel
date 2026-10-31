"""Write a tahweel Document to .docx with python-docx, with proper right-to-left markup."""

import copy
import dataclasses
import io
import re

from docx import Document as DocxDocument
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .model import Image, PageBreak, Paragraph, Rule, Table

TWIP = 20  # twips per point
W = r"[A-Za-z$](?:[A-Za-z0-9_.+/#$\-]*[A-Za-z0-9_+#$])?"
LATIN_RE = re.compile(W + r"(?:[ ]+" + W + r")*")
ARABIC_RE = re.compile(r"[؀-ۿ]")
CODE_FONT = "Consolas"
HEADING_SIZES = {1: 20, 2: 16, 3: 13.5, 4: 12, 5: 11, 6: 11}
HEADING_COLOR = "1F3864"
LINK_COLOR = "0563C1"
QUOTE_COLOR = "595959"

# child order required by the OOXML schema (Word rejects files with misordered properties)
ORDER = {
    "pPr": "pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs "
           "suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi "
           "adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection "
           "textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange",
    "rPr": "rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid "
           "vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl "
           "cs em lang eastAsianLayout specVanish oMath",
    "tblPr": "tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize tblStyleColBandSize tblW jc tblCellSpacing "
             "tblInd tblBorders shd tblLayout tblCellMar tblLook tblCaption tblDescription tblPrChange",
    "sectPr": "headerReference footerReference footnotePr endnotePr type pgSz pgMar paperSrc pgBorders lnNumType "
              "pgNumType cols formProt vAlign noEndnote titlePg textDirection bidi rtlGutter docGrid printerSettings "
              "sectPrChange",
}
ORDER = {qn(f"w:{k}"): {qn(f"w:{t}"): i for i, t in enumerate(v.split())} for k, v in ORDER.items()}


def _fix_order(root):
    for tag, rank in ORDER.items():
        for el in root.iter(tag):
            kids = list(el)
            kids.sort(key=lambda k: rank.get(k.tag, len(rank)))
            for k in kids:
                el.append(k)


def _el(tag, **attrs):
    e = OxmlElement(tag)
    for k, v in attrs.items():
        e.set(qn(f"w:{k}"), str(v))
    return e


def _ppr_append(p, el):
    """Append to pPr keeping the schema order python-docx cares about (bidi must come early)."""
    ppr = p._p.get_or_add_pPr()
    if el.tag == qn("w:bidi"):
        jc = ppr.find(qn("w:jc"))
        if jc is not None:
            jc.addprevious(el)
            return
    ppr.append(el)


def _set_fonts(rpr, font):
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    for a in list(rf.attrib):
        if a.endswith("Theme"):  # theme fonts would override the explicit ones
            del rf.attrib[a]
    for a in ("ascii", "hAnsi", "cs", "eastAsia"):
        rf.set(qn(f"w:{a}"), font)


def _border(side, color="BFBFBF", size=6, space=1):
    return _el(f"w:{side}", val="single", sz=size, space=space, color=color)


class DocxWriter:
    def __init__(self, doc):
        self.src = doc
        self.d = DocxDocument()
        self.lang_bidi = "ar-SA" if self._arabic(doc) else "he-IL"
        self._setup()

    @staticmethod
    def _arabic(doc):
        sample = "".join(b.text for b in doc.blocks[:80] if isinstance(b, Paragraph))
        return len(ARABIC_RE.findall(sample)) > len(re.findall(r"[֐-׿]", sample))

    # ---------- document setup ----------
    def _setup(self):
        src, d = self.src, self.d
        sec = d.sections[0]
        sec.page_width, sec.page_height = Pt(src.page_width), Pt(src.page_height)
        left, right, top, bottom = src.margins
        sec.left_margin, sec.right_margin = Pt(left), Pt(right)
        sec.top_margin, sec.bottom_margin = Pt(top), Pt(bottom)
        sec.header_distance = Pt(min(24, top / 2))
        sec.footer_distance = Pt(min(20, bottom / 2))
        if src.rtl:
            sec._sectPr.append(_el("w:bidi"))

        st = d.styles["Normal"]
        st.font.name = src.body_font
        st.font.size = Pt(src.body_size)
        rpr = st.element.get_or_add_rPr()
        _set_fonts(rpr, src.body_font)
        rpr.append(_el("w:szCs", val=int(src.body_size * 2)))
        rpr.append(_el("w:lang", val="en-US", bidi=self.lang_bidi))
        pf = st.paragraph_format
        pf.space_before = pf.space_after = Pt(0)
        pf.line_spacing = 1.0
        if src.title:
            d.core_properties.title = src.title[:200]

        for level, size in HEADING_SIZES.items():
            hs = d.styles[f"Heading {level}"]
            hr = hs.element.get_or_add_rPr()
            for tag in ("w:color", "w:sz", "w:szCs", "w:i", "w:iCs"):
                for old in hr.findall(qn(tag)):
                    hr.remove(old)
            _set_fonts(hr, src.body_font)
            hs.font.size = Pt(size)
            hs.font.bold = True
            hr.append(_el("w:bCs"))
            hr.append(_el("w:szCs", val=int(size * 2)))
            hr.append(_el("w:color", val=HEADING_COLOR))
            hpf = hs.paragraph_format
            hpf.space_before = Pt({1: 18, 2: 14}.get(level, 10))
            hpf.space_after = Pt(4)
            hpf.keep_with_next = True

    # ---------- runs ----------
    def _run(self, p, text, run, rtl):
        r = p.add_run(text)
        if run.bold:
            r.bold = True
        if run.italic:
            r.italic = True
        if run.underline or run.link:
            r.underline = True
        if run.strike:
            r.font.strike = True
        color = run.color or (LINK_COLOR if run.link else None)
        if color:
            r.font.color.rgb = RGBColor.from_string(color)
        rpr = r._r.get_or_add_rPr()
        font = CODE_FONT if run.mono and not run.font else run.font
        if font and font != self.src.body_font:
            _set_fonts(rpr, font)
        if run.bold:
            rpr.append(_el("w:bCs"))
        if run.italic:
            rpr.append(_el("w:iCs"))
        if run.size and abs(run.size - self.src.body_size) > 0.1:
            r.font.size = Pt(run.size)
            rpr.append(_el("w:szCs", val=int(round(run.size * 2))))
        if rtl:
            rpr.append(_el("w:rtl"))
        if run.link:
            self._hyperlink(p, r, run.link)
        return r

    def _hyperlink(self, p, r, url):
        if url.startswith("#"):
            hl = _el("w:hyperlink", anchor=url[1:])
        else:
            rid = p.part.relate_to(url, RT.HYPERLINK, is_external=True)
            hl = OxmlElement("w:hyperlink")
            hl.set(qn("r:id"), rid)
        r._r.addprevious(hl)
        hl.append(r._r)

    def _runs(self, p, runs, rtl_para):
        """RTL paragraphs: Latin chunks become LTR runs, everything else RTL runs (Word's own split)."""
        for run in runs:
            text = run.text
            if not rtl_para or run.mono:
                self._run(p, text, run, False)
                continue
            pos = 0
            for m in LATIN_RE.finditer(text):
                if m.start() > pos:
                    self._run(p, text[pos:m.start()], run, True)
                chunk = m.group()
                if not chunk[-1].isalnum():
                    chunk += "‎"  # LRM keeps trailing neutrals like "++" with the Latin word
                self._run(p, chunk, run, False)
                pos = m.end()
            if pos < len(text):
                self._run(p, text[pos:], run, True)

    # ---------- blocks ----------
    def paragraph(self, para, container=None):
        container = container or self.d
        p = container.add_paragraph()
        pf = p.paragraph_format
        if para.heading:
            p.style = self.d.styles[f"Heading {min(6, para.heading)}"]
        if para.rtl and not para.code:
            _ppr_append(p, _el("w:bidi"))
        if para.align == "center":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif para.align == "end":
            # inside bidi paragraphs jc left/right are mirrored (jc=right puts the text on the left)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        elif para.align == "left":
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if para.rtl else WD_ALIGN_PARAGRAPH.LEFT
        elif para.align == "right":
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT if para.rtl else WD_ALIGN_PARAGRAPH.RIGHT
        indent = para.indent + (18 if para.quote else 0)
        if para.quote:
            bdr = _el("w:pBdr")
            bdr.append(_border("left" if not para.rtl else "right", "A6A6A6", 18, 8))
            _ppr_append(p, bdr)
        if indent or para.hanging or para.first_line:
            ind = _el("w:ind", left=int(indent * TWIP))
            if para.hanging:
                ind.set(qn("w:hanging"), str(int(para.hanging * TWIP)))
            elif para.first_line:
                ind.set(qn("w:firstLine"), str(int(para.first_line * TWIP)))
            _ppr_append(p, ind)
        if para.label is not None:
            tabs = _el("w:tabs")
            tabs.append(_el("w:tab", val="left", pos=int(indent * TWIP)))
            _ppr_append(p, tabs)
        if para.shade:
            _ppr_append(p, _el("w:shd", val="clear", color="auto", fill=para.shade))
        pf.space_before = Pt(para.space_before) if para.space_before else None
        if para.line_spacing:
            pf.line_spacing = para.line_spacing
        if para.keep_next:
            pf.keep_with_next = True

        if para.label is not None:
            self._runs(p, para.label, para.rtl)
            tab_run = copy.copy(para.label[-1])
            r = self._run(p, "", tab_run, para.rtl)
            r.add_tab()
        if para.code:
            for run in para.runs:
                self._run(p, run.text.replace(" ", " ").replace("\t", " " * 4), run, False)
        else:
            runs = para.runs
            if para.quote:
                runs = [r if r.color else dataclasses.replace(r, color=QUOTE_COLOR) for r in runs]
            self._runs(p, runs, para.rtl)
        return p

    def rule(self):
        p = self.d.add_paragraph()
        bdr = _el("w:pBdr")
        bdr.append(_border("bottom", "BFBFBF", 6, 1))
        _ppr_append(p, bdr)
        p.paragraph_format.space_after = Pt(6)

    def table(self, t):
        ncols = t.columns
        if not ncols:
            return
        tbl = self.d.add_table(rows=len(t.rows), cols=ncols)
        tbl.style = self.d.styles["Table Grid"]
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tblpr = tbl._tbl.tblPr
        if t.rtl:
            tblpr.append(_el("w:bidiVisual"))
        for i, row in enumerate(t.rows):
            head = i < t.header_rows
            for j in range(ncols):
                cell = tbl.cell(i, j)
                paras = row[j] if j < len(row) else []
                first = cell.paragraphs[0]._p
                for para in paras:
                    if head:
                        para = dataclasses.replace(para, runs=[dataclasses.replace(r, bold=True) for r in para.runs])
                    if t.align and j < len(t.align) and para.align == "start":
                        para = dataclasses.replace(para, align=t.align[j])
                    self.paragraph(para, cell)
                if paras:
                    first.getparent().remove(first)
                if head:
                    tcpr = cell._tc.get_or_add_tcPr()
                    tcpr.append(_el("w:shd", val="clear", color="auto", fill="E7ECF3"))
            if head:
                trpr = tbl.rows[i]._tr.get_or_add_trPr()
                trpr.append(_el("w:tblHeader"))
        # a little air between the table and the next paragraph
        self.d.add_paragraph().paragraph_format.space_after = Pt(0)

    def image(self, img):
        p = self.d.add_paragraph()
        p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "right": WD_ALIGN_PARAGRAPH.RIGHT}.get(
            img.align, WD_ALIGN_PARAGRAPH.CENTER)
        if img.space_before:
            p.paragraph_format.space_before = Pt(img.space_before)
        usable = self.src.page_width - self.src.margins[0] - self.src.margins[1]
        width = min(img.width, usable)
        try:
            p.add_run().add_picture(io.BytesIO(img.data), width=Pt(width))
        except Exception:  # noqa: BLE001 - unsupported image format; keep going without it
            p.add_run("[image]")
        return p

    def page_break(self):
        p = self.d.add_paragraph()
        p.add_run().add_break(WD_BREAK.PAGE)

    # ---------- header / footer ----------
    def _background(self, img):
        """Full-page picture anchored behind the text in the header, so it repeats on every page."""
        hdr = self.d.sections[0].header
        p = hdr.paragraphs[0]
        run = p.add_run()
        run.add_picture(io.BytesIO(img.data), width=Pt(self.src.page_width), height=Pt(self.src.page_height))
        inline = run._r.find(".//" + qn("wp:inline"))
        cx, cy = inline.find(qn("wp:extent")).get("cx"), inline.find(qn("wp:extent")).get("cy")
        graphic = inline.find(qn("a:graphic"))
        docpr = inline.find(qn("wp:docPr"))
        anchor = OxmlElement("wp:anchor")
        for k, v in dict(distT="0", distB="0", distL="0", distR="0", simplePos="0", relativeHeight="0",
                         behindDoc="1", locked="1", layoutInCell="1", allowOverlap="1").items():
            anchor.set(k, v)
        sp = OxmlElement("wp:simplePos")
        sp.set("x", "0")
        sp.set("y", "0")
        anchor.append(sp)
        for axis, rel in (("wp:positionH", "page"), ("wp:positionV", "page")):
            pos = OxmlElement(axis)
            pos.set("relativeFrom", rel)
            off = OxmlElement("wp:posOffset")
            off.text = "0"
            pos.append(off)
            anchor.append(pos)
        ext = OxmlElement("wp:extent")
        ext.set("cx", cx)
        ext.set("cy", cy)
        anchor.append(ext)
        eff = OxmlElement("wp:effectExtent")
        for k in ("l", "t", "r", "b"):
            eff.set(k, "0")
        anchor.append(eff)
        anchor.append(OxmlElement("wp:wrapNone"))
        anchor.append(docpr)
        anchor.append(OxmlElement("wp:cNvGraphicFramePr"))
        anchor.append(graphic)
        inline.getparent().replace(inline, anchor)

    def _page_number(self, where, align):
        sec = self.d.sections[0]
        part = sec.header if where == "header" else sec.footer
        p = part.add_paragraph() if (where == "header" and self.src.background) else part.paragraphs[0]
        p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "right": WD_ALIGN_PARAGRAPH.RIGHT}.get(
            align, WD_ALIGN_PARAGRAPH.CENTER)
        r = p.add_run()
        r._r.append(_el("w:fldChar", fldCharType="begin"))
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = " PAGE "
        r._r.append(instr)
        r._r.append(_el("w:fldChar", fldCharType="end"))

    # ---------- main ----------
    def build(self):
        src = self.src
        if src.background is not None:
            self._background(src.background)
        sec = self.d.sections[0]
        for para in src.header:
            self.paragraph(para, sec.header)
        for para in src.footer:
            self.paragraph(para, sec.footer)
        if src.page_numbers:
            self._page_number(src.page_numbers, src.page_number_align)

        body = self.d._body._body
        first = body.find(qn("w:p"))
        for b in src.blocks:
            if isinstance(b, Paragraph):
                self.paragraph(b)
            elif isinstance(b, Image):
                self.image(b)
            elif isinstance(b, PageBreak):
                self.page_break()
            elif isinstance(b, Table):
                self.table(b)
            elif isinstance(b, Rule):
                self.rule()
        if first is not None and len(first) == 0:  # drop python-docx's empty starter paragraph
            body.remove(first)
        _fix_order(self.d.element)
        _fix_order(self.d.styles.element)
        _fix_order(sec.header._element)
        _fix_order(sec.footer._element)
        return self.d

    def save(self, path):
        self.build().save(path)


def write_docx(doc, path):
    DocxWriter(doc).save(path)
