"""Intermediate document model: every reader (PDF, Word, Markdown) builds one, every writer consumes it."""

from dataclasses import dataclass, field


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str | None = None  # "RRGGBB", None = automatic (black)
    size: float | None = None  # points
    font: str | None = None
    mono: bool = False
    strike: bool = False
    link: str | None = None  # hyperlink target

    def same_style(self, other):
        return (self.bold, self.italic, self.underline, self.color, self.size, self.font, self.strike,
                self.link) == (other.bold, other.italic, other.underline, other.color, other.size, other.font,
                               other.strike, other.link)


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    rtl: bool = False
    align: str = "start"  # start | center | end
    indent: float = 0.0  # points from the start margin (right edge for RTL)
    hanging: float = 0.0  # points; label sits at indent - hanging, text at indent
    first_line: float = 0.0  # extra indent of the first line (points)
    label: list[Run] | None = None  # list marker ("1.", "א-", "•"), followed by a tab
    space_before: float = 0.0
    line_spacing: float | None = None  # multiple of single spacing, None = single
    shade: str | None = None  # background fill "RRGGBB"
    code: bool = False
    keep_next: bool = False
    heading: int = 0  # 1..6 = heading level
    quote: bool = False  # block quote
    list_kind: str | None = None  # "bullet" | "number" when the label is a real list marker
    level: int = 0  # list nesting depth (0 = top)

    @property
    def text(self):
        return "".join(r.text for r in self.runs)


@dataclass
class Image:
    data: bytes
    ext: str
    width: float  # points
    height: float
    align: str = "center"
    space_before: float = 0.0


@dataclass
class PageBreak:
    pass


@dataclass
class Rule:
    """Horizontal line between blocks."""


@dataclass
class Table:
    rows: list[list[list[Paragraph]]] = field(default_factory=list)  # row -> cell -> paragraphs
    header_rows: int = 0
    rtl: bool = False
    align: list[str] | None = None  # per column: start | center | end

    @property
    def columns(self):
        return max((len(r) for r in self.rows), default=0)


@dataclass
class Document:
    page_width: float = 595.3
    page_height: float = 841.9
    margins: tuple = (72.0, 72.0, 72.0, 72.0)  # left, right, top, bottom (points)
    blocks: list = field(default_factory=list)
    background: Image | None = None  # full-page letterhead drawn behind every page
    header: list[Paragraph] = field(default_factory=list)
    footer: list[Paragraph] = field(default_factory=list)
    page_numbers: str | None = None  # None | "header" | "footer"
    page_number_align: str = "center"  # left | center | right
    body_font: str = "Arial"
    body_size: float = 11.0
    rtl: bool = False  # dominant direction
    title: str = ""
    warnings: list[str] = field(default_factory=list)
