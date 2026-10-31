"""Markdown ⇄ Document.

Reading uses markdown-it-py (CommonMark + tables + strikethrough). Writing produces plain
CommonMark/GFM: headings, lists, quotes, fenced code, pipe tables, links and images (saved to a
folder next to the .md file).
"""

import dataclasses
import os
import re

from .bidi import RTL_RE, strong_counts
from .model import Document, Image, PageBreak, Paragraph, Rule, Run, Table

BULLETS = "•◦▪"
CODE_SHADE = "F4F5F7"
LIST_STEP = 22.0  # points of indent per list level
HANGING = 18.0
LATIN_RE = re.compile(r"[A-Za-z]")


def first_strong_rtl(text):
    """Direction the way dir="auto" decides it: by the first strong character."""
    for ch in text:
        if RTL_RE.match(ch):
            return True
        if LATIN_RE.match(ch):
            return False
    return False


# ======================================================================
# reading
# ======================================================================

def _image_block(src, base_dir, usable_width):
    if re.match(r"^[a-z][a-z0-9+.-]*://", src, re.I):
        return None  # remote images are not downloaded
    path = src if os.path.isabs(src) else os.path.join(base_dir, src)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
    width, height = 300.0, 200.0
    try:
        import pymupdf

        pix = pymupdf.Pixmap(data)
        width, height = pix.width * 0.75, pix.height * 0.75  # 96 dpi
    except Exception:  # noqa: BLE001 - unknown size: keep the default box
        pass
    if width > usable_width:
        height *= usable_width / width
        width = usable_width
    return Image(data=data, ext=ext, width=width, height=height, space_before=6)


class _MdReader:
    def __init__(self, base_dir, usable_width):
        self.base_dir = base_dir
        self.usable = usable_width
        self.blocks = []
        self.lists = []  # stack of [kind, next_number]
        self.item_fresh = False  # next paragraph is the first one of a list item
        self.quote = 0
        self.pending_images = []

    # ---------- inline ----------
    def inline(self, tokens):
        runs = []
        style = {"bold": 0, "italic": 0, "strike": 0}
        links = []
        for t in tokens:
            kind = t.type
            if kind in ("text", "code_inline", "softbreak", "hardbreak", "html_inline"):
                if kind == "text":
                    text = t.content
                elif kind == "code_inline":
                    text = t.content
                elif kind == "softbreak":
                    text = " "
                elif kind == "hardbreak":
                    text = "\n"
                else:  # html_inline: <br> becomes a line break, other tags are dropped
                    text = "\n" if re.match(r"<br\s*/?>", t.content, re.I) else ""
                if not text:
                    continue
                runs.append(Run(text, bold=style["bold"] > 0, italic=style["italic"] > 0,
                                strike=style["strike"] > 0, mono=kind == "code_inline",
                                link=links[-1] if links else None))
            elif kind == "strong_open":
                style["bold"] += 1
            elif kind == "strong_close":
                style["bold"] -= 1
            elif kind == "em_open":
                style["italic"] += 1
            elif kind == "em_close":
                style["italic"] -= 1
            elif kind == "s_open":
                style["strike"] += 1
            elif kind == "s_close":
                style["strike"] -= 1
            elif kind == "link_open":
                links.append(t.attrs.get("href") or None)
            elif kind == "link_close":
                if links:
                    links.pop()
            elif kind == "image":
                img = _image_block(t.attrs.get("src", ""), self.base_dir, self.usable)
                if img is not None:
                    self.pending_images.append(img)
                elif t.content:
                    runs.append(Run(f"[{t.content}]", italic=True))
        return _merge(runs)

    # ---------- blocks ----------
    def add_paragraph(self, runs, heading=0):
        text = "".join(r.text for r in runs)
        if not text.strip() and not self.pending_images:
            return
        rtl = first_strong_rtl(text)
        para = Paragraph(runs=runs, rtl=rtl, heading=heading, quote=self.quote > 0,
                         space_before=6, line_spacing=1.15)
        if self.lists:
            level = len(self.lists) - 1
            para.indent = LIST_STEP * (level + 1)
            para.level = level
            para.line_spacing = 1.1
            if self.item_fresh:
                kind, number = self.lists[-1]
                para.list_kind = kind
                para.hanging = HANGING
                if kind == "bullet":
                    para.label = [Run(BULLETS[level % len(BULLETS)])]
                else:
                    para.label = [Run(f"{number}.")]
                    self.lists[-1][1] += 1
                para.space_before = 2
                self.item_fresh = False
        if text.strip():
            self.blocks.append(para)
        self.blocks.extend(self.pending_images)
        self.pending_images = []

    def code(self, content):
        lines = content.rstrip("\n").split("\n")
        indent = LIST_STEP * len(self.lists)
        for i, line in enumerate(lines):
            self.blocks.append(Paragraph(runs=[Run(line.expandtabs(4), mono=True)], code=True, shade=CODE_SHADE,
                                         indent=indent, space_before=8 if i == 0 else 0))
        self.blocks.append(Paragraph(runs=[], space_before=0))  # breathing room after the block

    def table(self, tokens, i):
        """Consume table tokens starting at i (table_open); return the index after table_close."""
        rows, aligns, header_rows = [], [], 0
        row = None
        in_head = False
        while i < len(tokens):
            t = tokens[i]
            if t.type == "table_close":
                break
            if t.type == "thead_open":
                in_head = True
            elif t.type == "thead_close":
                in_head = False
            elif t.type == "tr_open":
                row = []
            elif t.type == "tr_close":
                rows.append(row)
                if in_head:
                    header_rows += 1
            elif t.type in ("th_open", "td_open"):
                if in_head:
                    m = re.search(r"text-align:(\w+)", t.attrs.get("style", "") or "")
                    aligns.append({"center": "center", "left": "left", "right": "right"}.get(
                        m.group(1) if m else "", "start"))
            elif t.type == "inline" and row is not None:
                runs = self.inline(t.children or [])
                text = "".join(r.text for r in runs)
                row.append([Paragraph(runs=runs, rtl=first_strong_rtl(text))])
            i += 1
        text = " ".join(p.text for r in rows for c in r for p in c)
        rtl_n, ltr_n = strong_counts(text)
        self.blocks.append(Table(rows=rows, header_rows=header_rows, rtl=rtl_n > ltr_n,
                                 align=aligns or None))
        return i + 1

    def run(self, tokens):
        i = 0
        heading = 0
        while i < len(tokens):
            t = tokens[i]
            kind = t.type
            if kind == "heading_open":
                heading = int(t.tag[1])
            elif kind == "heading_close":
                heading = 0
            elif kind == "inline":
                self.add_paragraph(self.inline(t.children or []), heading)
            elif kind == "bullet_list_open":
                self.lists.append(["bullet", 1])
            elif kind == "ordered_list_open":
                start = t.attrs.get("start")
                self.lists.append(["number", int(start) if start else 1])
            elif kind in ("bullet_list_close", "ordered_list_close"):
                self.lists.pop()
                self.item_fresh = False
            elif kind == "list_item_open":
                self.item_fresh = True
            elif kind == "blockquote_open":
                self.quote += 1
            elif kind == "blockquote_close":
                self.quote -= 1
            elif kind in ("fence", "code_block"):
                self.code(t.content)
            elif kind == "hr":
                self.blocks.append(Rule())
            elif kind == "html_block":
                text = re.sub(r"<[^>]+>", "", t.content).strip()
                if text:
                    self.add_paragraph([Run(text)])
            elif kind == "table_open":
                i = self.table(tokens, i)
                continue
            i += 1
        return self.blocks


def _merge(runs):
    out = []
    for r in runs:
        if out and out[-1].same_style(r) and out[-1].mono == r.mono:
            out[-1].text += r.text
        else:
            out.append(r)
    return out


def read_md(path, progress=None):
    from markdown_it import MarkdownIt

    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    if progress:
        progress(0, 1)
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    doc = Document(margins=(64.0, 64.0, 64.0, 64.0))
    usable = doc.page_width - doc.margins[0] - doc.margins[1]
    doc.blocks = _MdReader(os.path.dirname(os.path.abspath(path)), usable).run(md.parse(text))
    rtl_n, ltr_n = strong_counts(text)
    doc.rtl = rtl_n > ltr_n
    first_h = next((b for b in doc.blocks if isinstance(b, Paragraph) and b.heading == 1), None)
    doc.title = first_h.text if first_h else os.path.splitext(os.path.basename(path))[0]
    if not any(isinstance(b, Paragraph) and b.text.strip() for b in doc.blocks):
        doc.warnings.append("empty")
    if progress:
        progress(1, 1)
    return doc


# ======================================================================
# writing
# ======================================================================

LINE_START = re.compile(r"^(\s*)([#>+\-=]|\d+[.)])(?=\s|$)")
BULLET_LABEL = re.compile(r"^[•·▪◦●○■□➢►✓✔\-–*]$")
NUMBER_LABEL = re.compile(r"^\(?(\d+)[.)]?$")
ESCAPE_RE = re.compile(
    r"\\(?=[!-/:-@\[-`{-~]|$)"   # a backslash that would escape the next character
    r"|[`*\[\]~]"                 # emphasis, code, links, strikethrough
    r"|(?<!\w)_|_(?!\w)"            # underscores at word edges (snake_case stays as is)
    r"|<(?=[A-Za-z/!?])"            # would start an HTML tag
    r"|\|")


def _escape(text, in_table=False):
    def sub(m):
        if m.group() == "|" and not in_table:
            return "|"
        return "\\" + m.group()
    return ESCAPE_RE.sub(sub, text)


def _wrap(text, marker):
    """Put marker around text, keeping surrounding spaces outside (``** a **`` is not bold)."""
    stripped = text.strip()
    if not stripped:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return f"{lead}{marker}{stripped}{marker}{trail}"


def _code_span(text):
    ticks = "`"
    while ticks in text:
        ticks += "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _inline(runs, in_table=False):
    # join runs that render the same in Markdown (font/colour/size are not expressible)
    groups = []
    for r in runs:
        key = (r.bold, r.italic, r.strike, r.mono, r.link)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(r.text)
        else:
            groups.append((key, [r.text]))
    out = []
    for (bold, italic, strike, mono, link), parts in groups:
        text = "".join(parts)
        if not text:
            continue
        if mono:
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            s = lead + _code_span(text.strip()) + trail if text.strip() else text
            if in_table:
                s = s.replace("|", "\\|")
        else:
            s = _escape(text, in_table)
            if strike:
                s = _wrap(s, "~~")
            if italic:
                s = _wrap(s, "*")
            if bold:
                s = _wrap(s, "**")
        if link:
            s = f"[{s.strip()}]({link.replace(' ', '%20').replace(')', '%29')})"
        out.append(s)
    return "".join(out)


class _MdWriter:
    def __init__(self, doc, path):
        self.doc = doc
        self.path = path
        stem = os.path.splitext(os.path.basename(path))[0]
        self.img_dir_name = f"{stem}-images"
        self.img_dir = os.path.join(os.path.dirname(os.path.abspath(path)), self.img_dir_name)
        self.img_count = 0
        self.has_headings = any(isinstance(b, Paragraph) and b.heading for b in doc.blocks)
        list_indents = [b.indent for b in doc.blocks if isinstance(b, Paragraph) and b.label]
        self.base_indent = min(list_indents) if list_indents else 0.0

    def heading_level(self, para):
        if para.heading:
            return min(6, para.heading)
        if self.has_headings or para.label or para.code or not para.runs:
            return 0
        text = para.text.strip()
        if not text or len(text) > 120:
            return 0
        sizes = [r.size or self.doc.body_size for r in para.runs if r.text.strip()]
        if not sizes:
            return 0
        ratio = min(sizes) / max(1.0, self.doc.body_size)
        if ratio >= 1.5:
            return 1
        if ratio >= 1.25:
            return 2
        if ratio >= 1.12 and all(r.bold for r in para.runs if r.text.strip()):
            return 3
        return 0

    def image(self, img):
        os.makedirs(self.img_dir, exist_ok=True)
        self.img_count += 1
        ext = (img.ext or "png").lower().replace("jpeg", "jpg")
        name = f"image-{self.img_count}.{ext}"
        with open(os.path.join(self.img_dir, name), "wb") as f:
            f.write(img.data)
        return f"![]({self.img_dir_name.replace(' ', '%20')}/{name})"

    def list_level(self, para):
        if para.list_kind:
            return para.level
        return max(0, round((para.indent - self.base_indent) / LIST_STEP))

    def paragraph(self, para, after_item=False):
        """Return (markdown, kind) where kind tells how to separate it from its neighbours."""
        body = _inline(para.runs)
        prefix = "> " if para.quote else ""
        level = self.heading_level(para)
        if level:
            plain = [dataclasses.replace(r, bold=False) for r in para.runs]  # headings are bold already
            return prefix + "#" * level + " " + _inline(plain).replace("\n", " ").strip(), "block"
        label = "".join(r.text for r in para.label).strip() if para.label else ""
        if label:
            indent = "    " * self.list_level(para)
            num = NUMBER_LABEL.match(label)
            if para.list_kind == "bullet" or BULLET_LABEL.match(label):
                marker = "-"
            elif para.list_kind == "number" or num:
                marker = f"{num.group(1) if num else 1}."
            else:
                # Hebrew/Arabic letters, (a), i. … have no Markdown list form: keep them as text
                text = _escape(label) + " " + body.strip()
                return prefix + LINE_START.sub(r"\1\\\2", text).replace("\n", "\\\n" + prefix), "block"
            cont = prefix + indent + " " * (len(marker) + 1)
            return prefix + indent + marker + " " + body.strip().replace("\n", "\\\n" + cont), "item"
        if after_item and not para.quote and para.indent and para.indent >= self.base_indent:
            # continuation paragraph of a list item
            indent = "    " * (self.list_level(para) + 1)
            text = body.strip()
            return indent + text.replace("\n", "\\\n" + indent), "item-cont"
        text = LINE_START.sub(r"\1\\\2", body.strip())
        return prefix + text.replace("\n", "\\\n" + prefix), "block"

    def code_block(self, paras):
        lines = [p.text.replace(" ", " ") for p in paras]
        while lines and not lines[-1].strip():
            lines.pop()
        fence = "```"
        while any(fence in ln for ln in lines):
            fence += "`"
        common = min((len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()), default=0)
        lines = [ln[common:] for ln in lines]
        return "\n".join([fence, *lines, fence])

    def table(self, t):
        ncols = t.columns
        if not ncols:
            return ""

        def cell(paras):
            return "<br>".join(_inline(p.runs, in_table=True).replace("\n", "<br>").strip() for p in paras) or " "

        rows = [[cell(r[j]) if j < len(r) else " " for j in range(ncols)] for r in t.rows]
        sep = []
        for j in range(ncols):
            a = t.align[j] if t.align and j < len(t.align) else "start"
            sep.append({"center": ":---:", "right": "---:", "left": ":---"}.get(a, "---"))
        lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(sep) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(lines)

    def render(self):
        chunks = []  # (text, kind)
        blocks = self.doc.blocks
        i = 0
        while i < len(blocks):
            b = blocks[i]
            if isinstance(b, Paragraph) and b.code:
                j = i
                while j < len(blocks) and isinstance(blocks[j], Paragraph) and blocks[j].code:
                    j += 1
                chunks.append((self.code_block(blocks[i:j]), "block"))
                i = j
                continue
            if isinstance(b, Paragraph):
                if b.text.strip():
                    after_item = bool(chunks) and chunks[-1][1] in ("item", "item-cont")
                    chunks.append(self.paragraph(b, after_item))
            elif isinstance(b, Image):
                chunks.append((self.image(b), "block"))
            elif isinstance(b, Table):
                chunks.append((self.table(b), "block"))
            elif isinstance(b, Rule):
                chunks.append(("---", "block"))
            elif isinstance(b, PageBreak):
                pass
            i += 1
        out = []
        prev = None
        for text, kind in chunks:
            if out:
                tight = kind == "item" and prev in ("item", "item-cont")
                out.append("\n" if tight else "\n\n")
            out.append(text)
            prev = kind
        return "".join(out).rstrip() + "\n"

    def save(self):
        text = self.render()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)


def write_md(doc, path):
    _MdWriter(doc, path).save()
