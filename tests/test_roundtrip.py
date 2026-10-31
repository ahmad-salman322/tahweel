"""Write a known document to .docx, print it to PDF with LibreOffice, convert it back, compare."""
import os
import shutil
import subprocess
import tempfile
import unittest

from tahweel.extract import read_pdf
from tahweel.model import Document, Paragraph, Run
from tahweel.writer import write_docx

TEXTS = [
    "שאלה מס. 4 – עקרון 1 Single Responsibility Principle:",
    "יש לעלות את הקובץ לתיבת ההגשה. שם הקובץ הוא Q4_123456.ZIP.",
    "سؤال رقم 2: اشرح مبدأ Open-Closed باختصار.",
    "What differences has the web made to software engineering?",
]
CODE = ["class Square", "{", "    public number area()", "}"]


@unittest.skipUnless(shutil.which("soffice"), "LibreOffice is needed to print the PDF")
class RoundTrip(unittest.TestCase):
    def test_roundtrip(self):
        src = Document(rtl=True, margins=(56, 56, 72, 72))
        for t in TEXTS:
            rtl = not t.startswith("What")
            src.blocks.append(Paragraph(runs=[Run(t)], rtl=rtl, space_before=8))
        src.blocks.append(Paragraph(runs=[Run("שלב ראשון")], rtl=True, label=[Run("1.")], indent=36, hanging=18,
                                    space_before=8))
        for line in CODE:
            src.blocks.append(Paragraph(runs=[Run(line, font="Liberation Mono", mono=True)], code=True,
                                        shade="F2F2F2"))
        with tempfile.TemporaryDirectory() as tmp:
            docx = os.path.join(tmp, "src.docx")
            write_docx(src, docx)
            subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, docx],
                           check=True, capture_output=True, timeout=120)
            doc = read_pdf(os.path.join(tmp, "src.pdf"))
        paras = [b for b in doc.blocks if isinstance(b, Paragraph)]
        texts = [p.text.strip() for p in paras]
        for t in TEXTS:
            self.assertIn(t, texts)
        item = next(p for p in paras if p.label)
        self.assertEqual("".join(r.text for r in item.label), "1.")
        self.assertEqual(item.text, "שלב ראשון")
        code = [p for p in paras if p.code]
        self.assertEqual([p.text.rstrip() for p in code], CODE)
        self.assertTrue(all(p.shade == "F2F2F2" for p in code))


if __name__ == "__main__":
    unittest.main()
