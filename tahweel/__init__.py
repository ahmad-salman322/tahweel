"""tahweel — convert between PDF, Word (.docx) and Markdown, with Hebrew/Arabic right-to-left text kept intact."""

import os

__version__ = "1.1.0"

FORMATS = ("docx", "pdf", "md")
EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".md": "md", ".markdown": "md", ".mdown": "md", ".mkd": "md"}
DEFAULT_TARGET = {"pdf": "docx", "docx": "pdf", "md": "docx"}
ALIASES = {"word": "docx", "doc": "docx", "markdown": "md", "pdf": "pdf", "docx": "docx", "md": "md"}


def detect(path):
    """'pdf' | 'docx' | 'md' from the file name, or None."""
    return EXTENSIONS.get(os.path.splitext(str(path))[1].lower())


def normalize(fmt):
    if fmt is None:
        return None
    try:
        return ALIASES[fmt.lower().lstrip(".")]
    except KeyError:
        raise ValueError(f"unknown format: {fmt}") from None


def read(path, progress=None, keep_background=True):
    """Read any supported file into a tahweel.model.Document."""
    fmt = detect(path)
    if fmt == "pdf":
        from .extract import read_pdf
        return read_pdf(path, progress=progress, keep_background=keep_background)
    if fmt == "docx":
        from .docx_reader import read_docx
        return read_docx(path, progress=progress)
    if fmt == "md":
        from .markdown_io import read_md
        return read_md(path, progress=progress)
    raise ValueError("unsupported")


def write(doc, path, fmt=None):
    """Write a Document as .docx, .md or .pdf (PDF goes through LibreOffice)."""
    fmt = normalize(fmt) or detect(path)
    if fmt == "docx":
        from .writer import write_docx
        write_docx(doc, path)
    elif fmt == "md":
        from .markdown_io import write_md
        write_md(doc, path)
    elif fmt == "pdf":
        import tempfile

        from .office import to_pdf
        from .writer import write_docx
        with tempfile.TemporaryDirectory(prefix="tahweel-") as tmp:
            docx = os.path.join(tmp, "doc.docx")
            write_docx(doc, docx)
            to_pdf(docx, path)
    else:
        raise ValueError("unsupported")


def convert(src, dest, progress=None, keep_background=True, to=None):
    """Convert `src` (pdf/docx/md) to `dest`. The target format is `to`, else dest's extension.

    Returns the Document that was read (for warnings such as "no-text").
    """
    from .model import Document

    src_fmt = detect(src)
    if src_fmt is None:
        raise ValueError("unsupported")
    to = normalize(to) or detect(dest) or DEFAULT_TARGET[src_fmt]
    if to == src_fmt:
        raise ValueError("same-format")
    if src_fmt == "docx" and to == "pdf":  # LibreOffice reads Word files directly: best fidelity
        from .office import to_pdf
        if progress:
            progress(0, 1)
        to_pdf(src, dest)
        if progress:
            progress(1, 1)
        return Document()
    doc = read(src, progress=progress, keep_background=keep_background)
    write(doc, dest, to)
    return doc


def output_path(src, out_dir=None, overwrite=False, to=None):
    """Default output path next to `src` (or in `out_dir`), never clobbering unless asked."""
    to = normalize(to) or DEFAULT_TARGET.get(detect(src), "docx")
    folder = out_dir or os.path.dirname(os.path.abspath(src))
    stem = os.path.splitext(os.path.basename(src))[0]
    ext = "." + to
    path = os.path.join(folder, stem + ext)
    n = 1
    while not overwrite and os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return path
