"""Read a PDF with PyMuPDF and rebuild its structure (paragraphs, lists, code, images) as a Document."""

import re
import statistics

import pymupdf

from .bidi import ltr_visual_to_logical, strong_counts, visual_to_logical
from .model import Document, Image, PageBreak, Paragraph, Run

SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
MONO_HINTS = ("mono", "courier", "consolas", "menlo", "inconsolata", "fixed", "typewriter", "code")
STYLE_WORDS = re.compile(r"[-,](Bold|Italic|Oblique|Regular|Light|Medium|Semibold|SemiBold|Black|Heavy|Book|Roman|Demi)\w*$")
FONT_NAMES = {"TimesNewRoman": "Times New Roman", "CourierNew": "Courier New", "SegoeUI": "Segoe UI",
              "DejaVuSans": "DejaVu Sans", "DejaVuSansMono": "DejaVu Sans Mono", "DejaVuSerif": "DejaVu Serif",
              "LiberationSans": "Liberation Sans", "LiberationSerif": "Liberation Serif",
              "LiberationMono": "Liberation Mono", "NotoSans": "Noto Sans", "NotoSansHebrew": "Noto Sans Hebrew",
              "NotoSansArabic": "Noto Sans Arabic", "TraditionalArabic": "Traditional Arabic",
              "SimplifiedArabic": "Simplified Arabic", "ArialUnicodeMS": "Arial Unicode MS"}
LABEL_RE = re.compile(r"^(\(?\d{1,3}[.)]|\(?[א-תء-ي][.)\-]|\(?[A-Za-z][.)]|\(?[ivxIVX]{1,4}[.)]"
                      r"|[-–—•●○▪■◦·*➢►✓❖])$")
PAGE_NUM_RE = re.compile(r"^(-\s*)?\d{1,4}(\s*-)?$|^(page|עמוד|صفحة)\s*\d+(\s*(of|מתוך|من)\s*\d+)?$", re.I)


def font_family(name):
    name = SUBSET_RE.sub("", name)
    base = STYLE_WORDS.sub("", name)
    base = re.sub(r"(PSMT|PS|MT)$", "", base)
    base = re.sub(r"(PS|MT)$", "", base)
    if base in FONT_NAMES:
        return FONT_NAMES[base]
    if " " not in base and re.search(r"[a-z][A-Z]", base):
        base = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
    return base or "Arial"


class Glyph:
    __slots__ = ("c", "x0", "x1", "y0", "y1", "base", "size", "font", "bold", "italic", "mono", "color",
                 "underline", "gap", "seq")

    def __init__(self, c, x0, x1, y0, y1, base, size, font, bold, italic, mono, color, underline=False, gap=0.0,
                 seq=0):
        self.c, self.x0, self.x1, self.y0, self.y1, self.base = c, x0, x1, y0, y1, base
        self.size, self.font, self.bold, self.italic, self.mono, self.color = size, font, bold, italic, mono, color
        self.underline, self.gap, self.seq = underline, gap, seq

    def pseudo(self, c, gap, right=None):
        """A synthetic space/tab taking this glyph's style."""
        ul = self.underline and (right is None or right.underline)
        return Glyph(c, self.x1, self.x1, self.y0, self.y1, self.base, self.size, self.font, self.bold,
                     self.italic, self.mono, self.color, ul, gap, self.seq)

    def style(self):
        return (self.font, round(self.size * 2) / 2, self.bold, self.italic, self.underline, self.color, self.mono)


class Line:
    def __init__(self, visual, page):
        self.visual = visual  # left-to-right glyphs incl. synthetic spaces
        self.glyphs = visual  # logical order, set by finalize()
        self.page = page
        real = [g for g in visual if not g.c.isspace()]
        self.x0 = min(g.x0 for g in real)
        self.x1 = max(g.x1 for g in real)
        self.y0 = min(g.y0 for g in real)
        self.y1 = max(g.y1 for g in real)
        self.base = statistics.median(g.base for g in real)
        self.size = statistics.median(g.size for g in real)
        self.mono = sum(g.mono for g in real) >= 0.7 * len(real)
        self.raw_text = "".join(g.c for g in visual)
        self.rtl = False
        self.shade = None
        self.label_len = 0  # glyphs in the list marker (logical), 0 = none

    @property
    def text(self):
        return "".join(g.c for g in self.glyphs)

    def finalize(self, rtl):
        self.rtl = rtl
        key, seq = (lambda g: g.c), (lambda g: g.seq)
        reorder = visual_to_logical if rtl else ltr_visual_to_logical
        self.glyphs = reorder(self.visual, key, seq)
        # list marker: first token followed by a tab or a wide gap
        for i, g in enumerate(self.glyphs):
            if g.c in " \t":
                if 0 < i <= 5 and (g.c == "\t" or g.gap > 0.45 * g.size) and LABEL_RE.match(self.text[:i]):
                    self.label_len = i
                break


def _hex(rgb):
    return "%02X%02X%02X" % tuple(max(0, min(255, round(v * 255))) for v in rgb[:3])


def _is_whiteish(rgb):
    return min(rgb[:3]) > 0.985


class PageData:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.lines = []
        self.images = []  # (rect, xref, digest)


def _read_page(page):
    pd = PageData(page.rect.width, page.rect.height)
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    glyphs, seen = [], set()
    for b in raw["blocks"]:
        if b["type"] != 0:
            continue
        for ln in b["lines"]:
            if abs(ln["dir"][1]) > 0.1:  # rotated text is out of scope
                continue
            for s in ln["spans"]:
                name = SUBSET_RE.sub("", s["font"])
                low = name.lower()
                flags = s["flags"]
                bold = bool(flags & 16) or any(w in low for w in ("bold", "black", "heavy", "semibold", "demi"))
                italic = bool(flags & 2) or "italic" in low or "oblique" in low
                mono = bool(flags & 8) or any(h in low for h in MONO_HINTS)
                fam, size, color = font_family(name), s["size"], "%06X" % s["color"]
                for c in s["chars"]:
                    ch = c["c"]
                    if ch.isspace() or ch in " ‎‏‪‫‬‭‮﻿":
                        continue
                    x0, y0, x1, y1 = c["bbox"]
                    if x1 - x0 <= 0.01:
                        x1 = x0 + size * 0.3
                    key = (ch, round(x0), round(c["origin"][1]))
                    if key in seen:  # fake-bold double strike
                        continue
                    seen.add(key)
                    glyphs.append(Glyph(ch, x0, x1, y0, y1, c["origin"][1], size, fam, bold, italic, mono, color,
                                        seq=len(glyphs)))

    # underlines (thin filled rects / horizontal strokes) and shaded backgrounds
    unders, fills = [], []
    area = pd.width * pd.height
    for d in page.get_drawings():
        r = d["rect"]
        fill = d.get("fill")
        if fill is not None and r.height <= 1.8 and r.width >= 2:
            unders.append(r)
        elif fill is not None and r.width > 20 and r.height > 4 and not _is_whiteish(fill) and r.width * r.height < 0.8 * area:
            fills.append((r, _hex(fill)))
        elif d.get("color") is not None:
            for it in d["items"]:
                if it[0] == "l" and abs(it[1].y - it[2].y) < 0.5 and abs(it[2].x - it[1].x) >= 2:
                    y = it[1].y
                    unders.append(pymupdf.Rect(min(it[1].x, it[2].x), y - 0.4, max(it[1].x, it[2].x), y + 0.4))
    if unders:
        for g in glyphs:
            cx = (g.x0 + g.x1) / 2
            for u in unders:
                if u.x0 - 1 <= cx <= u.x1 + 1 and g.base - 1 <= u.y0 <= g.base + 0.35 * g.size:
                    g.underline = True
                    break

    # group glyphs into rows by baseline, then split rows at big horizontal gaps
    glyphs.sort(key=lambda g: (g.base, g.x0))
    rows = []
    for g in glyphs:
        if rows and abs(g.base - rows[-1][0]) <= 0.3 * max(g.size, rows[-1][1]):
            rows[-1][2].append(g)
        else:
            rows.append([g.base, g.size, [g]])
    for _, _, row in rows:
        row.sort(key=lambda g: g.x0)
        seg = [row[0]]
        for g in row[1:]:
            if g.x0 - seg[-1].x1 > 5 * max(g.size, seg[-1].size):
                pd.lines.append(_make_line(seg, page.number))
                seg = []
            seg.append(g)
        pd.lines.append(_make_line(seg, page.number))

    for ln in pd.lines:
        cy, cx = (ln.y0 + ln.y1) / 2, (ln.x0 + ln.x1) / 2
        for r, color in fills:
            if r.x0 - 2 <= cx <= r.x1 + 2 and r.y0 - 2 <= cy <= r.y1 + 2:
                ln.shade = color
                break

    for info in page.get_image_info(xrefs=True):
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if r.width >= 8 and r.height >= 8:
            pd.images.append((r, info.get("xref", 0), info.get("digest")))
    return pd


def _make_line(seg, pno):
    """Build a Line from left-to-right glyphs, inserting spaces/tabs for horizontal gaps."""
    out = [seg[0]]
    for a, b in zip(seg, seg[1:]):
        gap = b.x0 - a.x1
        sz = min(a.size, b.size)
        if a.mono and b.mono:
            cw = max(a.x1 - a.x0, 0.3 * sz)
            n = round(gap / cw) if gap > 0.3 * cw else 0
            out.extend(a.pseudo(" ", gap, b) for _ in range(n))
        elif gap > 1.2 * sz:
            out.append(a.pseudo("\t", gap, b))
        elif gap > 0.1 * sz:
            out.append(a.pseudo(" ", gap, b))
        out.append(b)
    return Line(out, pno)


def _runs(glyphs, mono=False):
    runs = []
    for g in glyphs:
        st = g.style()
        if runs and runs[-1][0] == st:
            runs[-1][1].append(g.c)
        else:
            runs.append((st, [g.c]))
    out = []
    for (font, size, bold, italic, ul, color, gmono), chars in runs:
        text = "".join(chars)
        if not mono:
            text = text.replace("\t", " ")
        near_black = all(int(color[i:i + 2], 16) < 40 for i in (0, 2, 4))
        out.append(Run(text, bold=bold, italic=italic, underline=ul and bool(text.strip()),
                       color=None if near_black else color, size=size, font=font, mono=gmono))
    return out


class _Area:
    def __init__(self, left, right, top, bottom):
        self.left, self.right, self.top, self.bottom = left, right, top, bottom
        self.width = right - left
        self.center = (left + right) / 2

    def start_off(self, ln, x0=None, x1=None):
        x0 = ln.x0 if x0 is None else x0
        x1 = ln.x1 if x1 is None else x1
        return (self.right - x1) if ln.rtl else (x0 - self.left)

    def far_gap(self, ln):
        return (ln.x0 - self.left) if ln.rtl else (self.right - ln.x1)

    def centered(self, ln):
        return (abs((ln.x0 + ln.x1) / 2 - self.center) < max(6, 0.06 * self.width)
                and ln.x0 - self.left > 0.1 * self.width and self.right - ln.x1 > 0.1 * self.width)


def read_pdf(path, progress=None, keep_background=True):
    """Parse `path` into a Document. `progress(done, total)` is called after each page."""
    pdf = pymupdf.open(path)
    if pdf.needs_pass:
        raise ValueError("encrypted")
    pages = []
    for page in pdf:
        pages.append(_read_page(page))
        if progress:
            progress(page.number + 1, len(pdf) + 1)

    doc = Document()
    if pages:
        doc.page_width, doc.page_height = pages[0].width, pages[0].height
    title = (pdf.metadata or {}).get("title") or ""

    # ---- header / footer zones: page numbers and repeated text ----
    zone_texts = {}
    for pd in pages:
        for ln in pd.lines:
            if ln.y1 < 0.09 * pd.height or ln.y0 > 0.91 * pd.height:
                zone_texts.setdefault(ln.raw_text.strip(), set()).add(id(pd))
    body = []
    for pd in pages:
        kept = []
        for ln in pd.lines:
            top, bottom = ln.y1 < 0.09 * pd.height, ln.y0 > 0.91 * pd.height
            if top or bottom:
                t = ln.raw_text.strip()
                if PAGE_NUM_RE.match(t) or PAGE_NUM_RE.match(t[::-1]):
                    doc.page_numbers = "header" if top else "footer"
                    cx = (ln.x0 + ln.x1) / 2
                    doc.page_number_align = "left" if cx < pd.width * 0.35 else "right" if cx > pd.width * 0.65 else "center"
                    continue
                if len(pages) > 1 and len(zone_texts.get(t, ())) >= max(2, len(pages) // 2):
                    if pd is pages[0] or not any(p.text == t for p in doc.header + doc.footer):
                        (doc.header if top else doc.footer).append(ln)
                    continue
            kept.append(ln)
        pd.lines = kept
        body.extend(kept)

    if not body:
        doc.blocks = []
        doc.warnings = ["no-text"]
        return doc

    area = _Area(min(ln.x0 for ln in body), max(ln.x1 for ln in body),
                 min(ln.y0 for ln in body), max(ln.y1 for ln in body))
    n_rtl = n_ltr = 0
    for ln in body:
        r, l = strong_counts(ln.raw_text)
        n_rtl, n_ltr = n_rtl + r, n_ltr + l
    doc.rtl = n_rtl > 0.4 * n_ltr

    for ln in body + doc.header + doc.footer:
        ln.finalize(_line_rtl(ln, area, doc.rtl))

    # ---- full-page background (letterhead) ----
    bg_pages = {}
    for pd in pages:
        for r, xref, digest in pd.images:
            if r.width * r.height >= 0.8 * pd.width * pd.height and digest:
                bg_pages.setdefault(digest, [xref, 0])[1] += 1
    bg_digest = None
    if bg_pages:
        digest, (xref, count) = max(bg_pages.items(), key=lambda kv: kv[1][1])
        if count >= max(1, len(pages) // 2):
            bg_digest = digest
            if keep_background and xref:
                img = pdf.extract_image(xref)
                doc.background = Image(img["image"], img["ext"], doc.page_width, doc.page_height)

    # ---- body blocks ----
    sizes = [ln.size for ln in body]
    body_size = statistics.median(sizes)
    for pi, pd in enumerate(pages):
        items = [("line", ln.y0, ln) for ln in pd.lines]
        for r, xref, digest in pd.images:
            if digest != bg_digest:
                items.append(("image", r.y0, (r, xref)))
        items.sort(key=lambda it: (it[1], -(it[2].x1 if it[0] == "line" and it[2].rtl else 0)))
        prev_bottom = None  # baseline / bottom of previous element on this page
        cur = None
        code_run = []
        for kind, _, obj in items:
            if kind == "image":
                _flush_code(code_run, doc)
                cur = None
                r, xref = obj
                data, ext = _image_bytes(pdf, pdf[pi], r, xref)
                cx = (r.x0 + r.x1) / 2
                align = "center" if abs(cx - area.center) < 0.1 * area.width else (
                    "left" if cx < area.center else "right")
                gap = (r.y0 - prev_bottom) if prev_bottom is not None else 0
                doc.blocks.append(Image(data, ext, r.width, r.height, align, max(0, gap - 2)))
                prev_bottom = r.y1
                continue
            ln = obj
            if cur is not None and _continues(cur, ln, area):
                _append_line(cur, ln)
                prev_bottom = ln.base
                continue
            if not ln.mono or (code_run and ln.shade != code_run[-1][1].shade):
                _flush_code(code_run, doc)
            gap_before = 0.0
            if prev_bottom is not None:
                gap_before = max(0.0, ln.base - prev_bottom - 1.15 * ln.size)
            cur = _new_para(ln, area)
            cur.space_before = round(gap_before, 1) if gap_before > 1.5 else 0.0
            if ln.mono:
                code_run.append((cur, ln))
                cur = None  # code lines never merge
            else:
                doc.blocks.append(cur)
            prev_bottom = ln.base
        _flush_code(code_run, doc)
        last = max((ln.y1 for ln in pd.lines), default=area.top)
        if pi < len(pages) - 1 and last < area.bottom - 4 * body_size:
            doc.blocks.append(PageBreak())
        if progress and pi == len(pages) - 1:
            progress(len(pages) + 1, len(pages) + 1)

    for lst in (doc.header, doc.footer):
        lst[:] = [_new_para(ln, _Area(0, doc.page_width, 0, doc.page_height)) for ln in lst]

    doc.margins = (max(18, area.left), max(18, doc.page_width - area.right),
                   max(18, area.top - 2), max(18, doc.page_height - area.bottom - 2))
    doc.body_font, doc.body_size = _dominant_font(body), round(body_size * 2) / 2
    doc.title = title or next((b.text.strip() for b in doc.blocks if isinstance(b, Paragraph) and b.text.strip()), "")
    doc.warnings = []
    return doc


def _line_rtl(ln, area, doc_rtl):
    if ln.mono:
        return False
    r, l = strong_counts(ln.raw_text)
    if r == 0:
        return False
    if l == 0:
        return True
    flush_right = ln.x1 >= area.right - 4
    flush_left = ln.x0 <= area.left + 4
    if flush_right and not flush_left:
        return True
    if flush_left and not flush_right:
        return False
    return doc_rtl if 3 * r >= l else False


def _new_para(ln, area):
    p = Paragraph(rtl=ln.rtl, shade=ln.shade, code=ln.mono)
    glyphs = ln.glyphs
    if ln.label_len and not ln.mono:
        label, rest = glyphs[:ln.label_len], glyphs[ln.label_len:]
        while rest and rest[0].c in " \t":
            rest = rest[1:]
        if rest:
            p.label = _runs(label)
            first = rest[0]
            lab0 = label[0]
            p.indent = max(0.0, area.start_off(ln, first.x0, first.x1))
            label_off = max(0.0, area.start_off(ln, lab0.x0, lab0.x1))
            p.hanging = max(0.0, p.indent - label_off)
            glyphs = rest
    if p.label is None and not ln.mono:
        if area.centered(ln):
            p.align = "center"
        elif area.far_gap(ln) < 4 and area.start_off(ln) > 0.15 * area.width:
            p.align = "end"
        else:
            p.indent = max(0.0, area.start_off(ln))
            if p.indent < 3:
                p.indent = 0.0
    p.runs = _runs(glyphs, mono=ln.mono)
    p._lines = [ln]
    p._cont = None
    p._area = area
    return p


def _continues(p, ln, area):
    q = p._lines[-1]
    if p.code or ln.mono or ln.label_len or ln.rtl != q.rtl or ln.shade != q.shade:
        return False
    delta = ln.base - q.base
    if not (0 < delta <= 1.9 * q.size) or abs(ln.size - q.size) >= 1:
        return False
    if area.far_gap(q) > 0.14 * area.width:  # previous line ended early: paragraph ended
        return False
    if p.align == "center":
        return area.centered(ln)
    off = area.start_off(ln)
    if p.label is not None:
        return abs(off - p.indent) < 8
    if p._cont is None:
        return off <= p.indent + 3
    return abs(off - p._cont) < 8


def _append_line(p, ln):
    sep = [] if p.text.endswith("\u00ad") else [Run(" ", **_style_kw(p.runs[-1]))]
    p.runs.extend(sep + _runs(ln.glyphs))
    _merge_runs(p)
    p._lines.append(ln)
    deltas = [b.base - a.base for a, b in zip(p._lines, p._lines[1:])]
    ratio = statistics.median(deltas) / (1.15 * statistics.median(l.size for l in p._lines))
    p.line_spacing = round(ratio, 2) if ratio > 1.2 else None
    if p.label is None and p.align != "center":
        # the first line may carry a first-line indent; continuation lines define the real indent
        p._cont = statistics.median(p._area.start_off(l) for l in p._lines[1:])
        if p._cont < p.indent - 3:
            p.first_line = p.indent - p._cont
            p.indent = 0.0 if p._cont < 3 else p._cont


def _style_kw(r):
    return dict(bold=r.bold, italic=r.italic, underline=False, color=r.color, size=r.size, font=r.font)


def _merge_runs(p):
    merged = []
    for r in p.runs:
        if merged and merged[-1].same_style(r) and merged[-1].mono == r.mono:
            merged[-1].text += r.text
        else:
            merged.append(r)
    p.runs = merged


def _flush_code(code_run, doc):
    """Emit consecutive code lines with indentation rebuilt from x positions."""
    if not code_run:
        return
    lines = [ln for _, ln in code_run]
    x0 = min(ln.x0 for ln in lines)
    widths = [g.x1 - g.x0 for ln in lines for g in ln.glyphs if not g.c.isspace()]
    cw = statistics.median(widths) if widths else 6.0
    deltas = [b.base - a.base for a, b in zip(lines, lines[1:]) if b.base > a.base]
    pitch = min(deltas) if deltas else 0
    prev = None
    for p, ln in code_run:
        if prev is not None:
            gap = ln.base - prev.base
            p.space_before = 0.0
            if pitch and gap > 3.5 * pitch:  # separate boxes: plain spacer between them
                doc.blocks.append(Paragraph(runs=[], space_before=round(gap - 2 * pitch, 1)))
            elif pitch and gap > 1.6 * pitch:  # blank lines inside the box
                for _ in range(round(gap / pitch) - 1):
                    doc.blocks.append(Paragraph(runs=[], code=True, shade=p.shade))
        lead = round((ln.x0 - x0) / cw)
        if lead > 0 and p.runs:
            p.runs[0].text = " " * lead + p.runs[0].text
        doc.blocks.append(p)
        prev = ln
    code_run.clear()


def _image_bytes(pdf, page, rect, xref):
    if xref:
        try:
            img = pdf.extract_image(xref)
            if img and img.get("image") and img.get("ext") in ("png", "jpeg", "jpg", "bmp", "gif", "tiff"):
                return img["image"], img["ext"]
        except Exception:  # noqa: BLE001 - fall back to rendering the region
            pass
    pix = page.get_pixmap(clip=rect, dpi=200)
    return pix.tobytes("png"), "png"


def _dominant_font(lines):
    count = {}
    for ln in lines:
        for g in ln.glyphs:
            if not g.c.isspace() and not g.mono:
                count[g.font] = count.get(g.font, 0) + 1
    return max(count, key=count.get) if count else "Arial"
