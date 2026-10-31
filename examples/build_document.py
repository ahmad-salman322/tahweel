"""Build a Word document by hand with tahweel's model and writer (no PDF parsing involved).

Shows RTL paragraphs, a heading, a numbered list with hanging indents, a link, a table,
an English paragraph and a code block. Usage:

    PYTHONPATH=. python examples/build_document.py out.docx      # or out.pdf / out.md
"""

import sys

import tahweel
from tahweel.model import Document, Paragraph, Rule, Run, Table

doc = Document(rtl=True, title="דוגמה")
add = doc.blocks.append

add(Paragraph(runs=[Run("سجل المشروع")], rtl=True, heading=1))
add(Paragraph(runs=[Run("هذا المستند مكتوب يدوياً بنموذج "), Run("tahweel", bold=True),
                    Run(". النص العربي يبقى من اليمين، والكلمات الإنجليزية مثل Open Source في مكانها الصحيح.")],
              rtl=True, space_before=6))
for n, text in enumerate(["إعداد البيئة", "كتابة الاختبارات", "رفع المشروع إلى GitHub"], 1):
    add(Paragraph(runs=[Run(text)], rtl=True, label=[Run(f"{n}.")], list_kind="number",
                  indent=22, hanging=18, space_before=2))
add(Paragraph(runs=[Run("פסקה בעברית עם "), Run("קישור", link="https://github.com"), Run(".")],
              rtl=True, space_before=8))


def cell(text, rtl=True):
    return [Paragraph(runs=[Run(text)], rtl=rtl)]


add(Table(rows=[[cell("المهمة"), cell("الحالة")],
                [cell("القارئ"), cell("جاهز")],
                [cell("الواجهة"), cell("قيد العمل")]], header_rows=1, rtl=True))
add(Rule())
add(Paragraph(runs=[Run("Mixed-direction documents need logical order, not visual order.", italic=True)],
              space_before=6))
for line in ["def hello(name):", "    return f\"שלום {name}\""]:
    add(Paragraph(runs=[Run(line, mono=True)], code=True, shade="F4F5F7"))

out = sys.argv[1] if len(sys.argv) > 1 else "example.docx"
tahweel.write(doc, out)
print(out)
