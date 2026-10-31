"""Markdown and Word readers/writers, and the format dispatch in tahweel.convert."""
import os
import shutil
import tempfile
import unittest

import docx

import tahweel
from tahweel.docx_reader import read_docx
from tahweel.markdown_io import _escape, read_md
from tahweel.model import Paragraph, Rule, Table

SAMPLE = """# עקרונות SOLID

עקרון **Single Responsibility** אומר שלכל מחלקה יש *סיבה אחת* לשינוי. ראו [ויקיפדיה](https://he.wikipedia.org/wiki/SOLID).

## مبادئ التصميم

هذا نص عربي فيه كلمة `Open-Closed` ورقم 42.

1. שלב ראשון
2. שלב שני
    - תת סעיף
    - עוד אחד
3. שלב שלישי

> ציטוט חשוב

- English item with **bold**
- Second ~~old~~ item

```
class Square {
    public int area() { return s * s; }
}
```

| שם | ציון | הערה |
| --- | :---: | --- |
| אחמד | 95 | מצוין |
| Sara | 88 | good |

---

סוף.
"""


class Formats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tahweel-test-")
        self.md = os.path.join(self.tmp, "sample.md")
        with open(self.md, "w", encoding="utf-8") as f:
            f.write(SAMPLE)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_read_md(self):
        doc = read_md(self.md)
        paras = [b for b in doc.blocks if isinstance(b, Paragraph)]
        self.assertTrue(doc.rtl)
        self.assertEqual(doc.title, "עקרונות SOLID")
        self.assertEqual([p.heading for p in paras[:2]], [1, 0])
        self.assertTrue(paras[0].rtl)
        link = next(r for p in paras for r in p.runs if r.link)
        self.assertEqual(link.text, "ויקיפדיה")
        items = [p for p in paras if p.label]
        self.assertEqual(["".join(r.text for r in p.label) for p in items][:5], ["1.", "2.", "◦", "◦", "3."])
        self.assertEqual([p.level for p in items][:5], [0, 0, 1, 1, 0])
        self.assertTrue(any(p.quote for p in paras))
        code = [p.text for p in paras if p.code and p.text]
        self.assertEqual(code[1], "    public int area() { return s * s; }")
        table = next(b for b in doc.blocks if isinstance(b, Table))
        self.assertEqual((len(table.rows), table.columns, table.header_rows, table.rtl), (3, 3, 1, True))
        self.assertTrue(any(isinstance(b, Rule) for b in doc.blocks))

    def test_markdown_word_markdown_is_stable(self):
        tahweel.convert(self.md, self.path("a.docx"))
        tahweel.convert(self.path("a.docx"), self.path("a.md"))
        tahweel.convert(self.path("a.md"), self.path("b.docx"))
        tahweel.convert(self.path("b.docx"), self.path("b.md"))
        with open(self.path("a.md"), encoding="utf-8") as f:
            first = f.read()
        with open(self.path("b.md"), encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, second)
        for piece in ("# עקרונות SOLID", "**Single Responsibility**", "[ויקיפדיה](https://he.wikipedia.org/wiki/SOLID)",
                      "1. שלב ראשון", "    - תת סעיף", "> ציטוט חשוב", "~~old~~", "`Open-Closed`",
                      "| אחמד | 95 | מצוין |", "| --- | :---: | --- |", "\n---\n"):
            self.assertIn(piece, first)

    def test_word_numbering(self):
        d = docx.Document()
        d.add_heading("כותרת", 1)
        for t in ("first", "second"):
            d.add_paragraph(t, style="List Number")
        d.add_paragraph("dot", style="List Bullet")
        d.save(self.path("n.docx"))
        doc = read_docx(self.path("n.docx"))
        paras = [b for b in doc.blocks if isinstance(b, Paragraph)]
        self.assertEqual(paras[0].heading, 1)
        self.assertEqual([("".join(r.text for r in p.label), p.list_kind) for p in paras[1:]],
                         [("1.", "number"), ("2.", "number"), ("•", "bullet")])

    def test_escape(self):
        self.assertEqual(_escape("tar_bait1_x.docx"), "tar_bait1_x.docx")
        self.assertEqual(_escape("_a_ *b* [c]"), r"\_a\_ \*b\* \[c\]")
        self.assertEqual(_escape("a|b"), "a|b")
        self.assertEqual(_escape("a|b", in_table=True), r"a\|b")

    def test_dispatch(self):
        self.assertEqual(tahweel.detect("x.MD"), "md")
        self.assertEqual(tahweel.normalize("Word"), "docx")
        self.assertEqual(os.path.basename(tahweel.output_path(self.md)), "sample.docx")
        self.assertEqual(os.path.basename(tahweel.output_path(self.md, to="md")), "sample (1).md")
        with self.assertRaises(ValueError):
            tahweel.convert(self.md, self.path("x.md"))

    @unittest.skipUnless(shutil.which("soffice"), "LibreOffice is needed for PDF output")
    def test_markdown_to_pdf_and_back(self):
        tahweel.convert(self.md, self.path("s.pdf"))
        tahweel.convert(self.path("s.pdf"), self.path("s.md"))
        with open(self.path("s.md"), encoding="utf-8") as f:
            text = f.read()
        for piece in ("# עקרונות SOLID", "1. שלב ראשון", "هذا نص عربي فيه كلمة `Open-Closed` ورقم 42.",
                      "    public int area() { return s * s; }"):
            self.assertIn(piece, text)


if __name__ == "__main__":
    unittest.main()
