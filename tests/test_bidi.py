import unittest

from tahweel.bidi import ltr_visual_to_logical, visual_to_logical


def rtl(visual, seq=None):
    """visual: the line as drawn left-to-right; seq: optional stream index per visual position."""
    items = list(range(len(visual)))
    key = lambda i: visual[i]  # noqa: E731
    order = visual_to_logical(items, key, (lambda i: seq[i]) if seq else None)
    return "".join(visual[i] for i in order)


class BidiTest(unittest.TestCase):
    def test_pure_hebrew(self):
        self.assertEqual(rtl("םולש"), "שלום")

    def test_latin_inside_rtl(self):
        self.assertEqual(rtl("Java ב השאלה"), "הלאשה ב Java")

    def test_numbers_stay_ltr(self):
        self.assertEqual(rtl("2026 תנש"), "שנת 2026")
        self.assertEqual(rtl("3.14 :יאפ"), "פאי: 3.14")

    def test_separate_numbers_read_right_to_left(self):
        # Word draws "מס. 1, 2, 3" as "3 ,2 ,1 .סמ"
        self.assertEqual(rtl("3 ,2 ,1 .סמ"), "מס. 1, 2, 3")

    def test_number_before_latin_uses_stream_order(self):
        # drawn: "Single 1 ןורקע"; written to the stream as ע ק ר ו ן, 1, S i n g l e
        visual = "Single 1 ןורקע"
        seq = [6, 7, 8, 9, 10, 11, 5, 5, 5, 4, 3, 2, 1, 0]
        self.assertEqual(rtl(visual, seq), "עקרון 1 Single")

    def test_number_after_latin_uses_stream_order(self):
        visual = "Java 8 םע"
        seq = [0, 1, 2, 3, 9, 10, 9, 9, 9]  # "8" written after "Java"
        self.assertEqual(rtl(visual, seq), "עם Java 8")

    def test_trailing_punctuation_belongs_to_rtl(self):
        self.assertEqual(rtl(".ZIP ץבוק"), "קובץ ZIP.")

    def test_ltr_line_with_hebrew_word(self):
        visual = "the word םולש means peace"
        items = list(range(len(visual)))
        out = ltr_visual_to_logical(items, lambda i: visual[i])
        self.assertEqual("".join(visual[i] for i in out), "the word שלום means peace")


if __name__ == "__main__":
    unittest.main()
