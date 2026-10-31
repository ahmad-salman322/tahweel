"""Turn visually ordered glyphs (as positioned on a PDF page) back into logical reading order.

PDF pages store every glyph at an x position, so sorting a line by x gives *visual* order.
ToUnicode maps already give the logical character (a mirrored "(" is still "("), so only the
order has to be undone: an RTL line is read right-to-left, except that Latin runs and numbers
inside it were laid out left-to-right and must be flipped back.

The Unicode bidi algorithm is not invertible: "עקרון 1 Single" and "עקרון Single 1" look the
same on the page. For that one case (a number next to a Latin run) the order in which glyphs
were written to the content stream breaks the tie, since PDF producers emit runs in logical order.
"""

import re

RTL_RE = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
LETTER_RE = re.compile(r"[A-Za-zÀ-ɏͰ-ϿЀ-ӿ]")
DIGIT_RE = re.compile(r"[0-9٠-٩۰-۹]")
NUM_SEP = ".,:/"


def is_rtl(ch):
    return bool(RTL_RE.match(ch))


def is_letter(ch):
    return bool(LETTER_RE.match(ch))


def is_digit(ch):
    return bool(DIGIT_RE.match(ch))


def is_ltr(ch):
    return is_letter(ch) or is_digit(ch)


def strong_counts(text):
    return len(RTL_RE.findall(text)), len(LETTER_RE.findall(text))


def _number_end(chars, i):
    """End of the number token starting at i (digits with single separators between digits)."""
    j = i
    n = len(chars)
    while j < n:
        if is_digit(chars[j]):
            j += 1
        elif chars[j] in NUM_SEP and j + 1 < n and is_digit(chars[j + 1]) and j > i:
            j += 1
        else:
            break
    return j


def _latin_run_end(chars, i):
    """Extend a Latin run from i over letters, digits and neutrals; stop at RTL, drop trailing neutrals."""
    j = i
    while j < len(chars) and not is_rtl(chars[j]):
        j += 1
    while j > i and not is_ltr(chars[j - 1]):
        j -= 1
    return j


def visual_to_logical(items, key=lambda it: it, seq=None):
    """Reorder a visually ordered (left-to-right) list for an RTL line.

    `key(item)` gives the character, `seq(item)` (optional) its content-stream index.
    Returns a new list in logical order.
    """
    rev = list(reversed(items))
    chars = [key(it) for it in rev]
    out = []
    i, n = 0, len(rev)
    while i < n:
        ch = chars[i]
        if is_letter(ch):
            j = _latin_run_end(chars, i)
            out.extend(reversed(rev[i:j]))
            i = j
        elif is_digit(ch):
            k = _number_end(chars, i)
            # a number directly followed (in reading order) by a Latin run: is it that run's tail?
            m = k
            while m < n and not is_ltr(chars[m]) and not is_rtl(chars[m]):
                m += 1
            if m < n and is_letter(chars[m]) and seq is not None and seq(rev[i]) > seq(rev[m]):
                j = _latin_run_end(chars, i)
                out.extend(reversed(rev[i:j]))
                i = j
            else:
                out.extend(reversed(rev[i:k]))
                i = k
        else:
            out.append(rev[i])
            i += 1
    return out


def ltr_visual_to_logical(items, key=lambda it: it, seq=None):
    """Reorder an LTR line: only embedded RTL runs (e.g. a Hebrew word) need flipping."""
    chars = [key(it) for it in items]
    out = []
    i, n = 0, len(items)
    while i < n:
        if not is_rtl(chars[i]):
            out.append(items[i])
            i += 1
            continue
        j = i
        while j < n and not is_letter(chars[j]):
            j += 1
        while j > i and not is_rtl(chars[j - 1]):
            j -= 1
        # inside the RTL run, numbers keep their LTR order
        out.extend(visual_to_logical(items[i:j], key, seq))
        i = j
    return out
