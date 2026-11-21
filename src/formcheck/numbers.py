"""Parse numbers and counts the way they are written on stock documents.

Two writing styles appear on the same kind of paper:

* ``dot``   : 1,250.50  (comma groups thousands, dot marks decimals)
* ``comma`` : 1.250,50  (dot groups thousands, comma marks decimals)

A lone ``1,250`` or ``1.250`` is ambiguous, so the style is decided once per
document from the numbers that are not ambiguous (any value with exactly two
decimals settles it), and every number on the page is then read in that style.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

DOT = "dot"
COMMA = "comma"

# OCR often confuses these characters inside numbers.
_OCR_DIGIT_FIXES = str.maketrans({
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "l": "1", "I": "1", "|": "1", "i": "1", "!": "1",
    "S": "5", "s": "5",
    "B": "8",
    "Z": "2", "z": "2",
    "g": "9",
})

_UNIT_WORDS = re.compile(r"(usd|us\$|kg|kgs|\$|€|lbp)", re.IGNORECASE)


def _clean(text: str) -> str:
    t = text.strip()
    t = _UNIT_WORDS.sub("", t)
    t = t.replace(" ", "").replace(" ", "").replace("'", "")
    # Only translate look-alike letters when the token is mostly digits:
    # "Rice" must stay a word, "1O5" should become 105.
    digits = sum(c.isdigit() for c in t)
    if t and digits >= max(1, len(t) // 2):
        t = t.translate(_OCR_DIGIT_FIXES)
    return t.strip(":;")


def looks_numeric(text: str) -> bool:
    t = _clean(text)
    return bool(t) and bool(re.fullmatch(r"-?[\d.,]+", t)) and any(c.isdigit() for c in t)


def style_hint(text: str) -> str | None:
    """Return the style a single token proves, or None when it is ambiguous."""
    t = _clean(text)
    if not re.fullmatch(r"-?[\d.,]+", t):
        return None
    # Two (or one) digits after the last separator: that separator is decimal.
    m = re.search(r"([.,])(\d{1,2})$", t)
    if m:
        return DOT if m.group(1) == "." else COMMA
    # Both separators present: the last one is the decimal mark.
    if "," in t and "." in t:
        return DOT if t.rfind(".") > t.rfind(",") else COMMA
    return None


def infer_style(texts: list[str]) -> str:
    """Vote over all tokens on a page. Defaults to DOT when nothing decides."""
    votes = {DOT: 0, COMMA: 0}
    for t in texts:
        h = style_hint(t)
        if h:
            votes[h] += 1
    return COMMA if votes[COMMA] > votes[DOT] else DOT


def parse_number(text: str, style: str = DOT) -> Decimal | None:
    """Parse ``text`` in the given style. Returns None when it is not a number."""
    t = _clean(text)
    if not t or not re.fullmatch(r"-?[\d.,]+", t) or not any(c.isdigit() for c in t):
        return None
    thousands, decimal = (",", ".") if style == DOT else (".", ",")
    if t.count(decimal) > 1:
        return None
    if decimal in t:
        whole, frac = t.rsplit(decimal, 1)
        if thousands in frac:
            return None
    else:
        whole, frac = t, ""
    if thousands in whole:
        groups = whole.lstrip("-").split(thousands)
        # Thousand groups must be three digits; otherwise it is a misread.
        if any(len(g) != 3 for g in groups[1:]) or not groups[0]:
            return None
        whole = whole.replace(thousands, "")
    try:
        return Decimal(f"{whole}.{frac}" if frac else whole)
    except InvalidOperation:
        return None


def format_number(value: Decimal | float | int, style: str = DOT, decimals: int = 2) -> str:
    """Write a number the way the note would, e.g. 1,250.50 or 1.250,50."""
    q = Decimal(value).quantize(Decimal(1).scaleb(-decimals)) if decimals else Decimal(value).quantize(Decimal(1))
    s = f"{q:,.{decimals}f}"
    if style == COMMA:
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return s


# ---------------------------------------------------------------- counts

def format_count(value: int, sep: str = ",") -> str:
    """Write a whole number of pieces with the document's thousands separator."""
    s = f"{int(value):,}"
    return s.replace(",", sep)


def parse_count(text: str) -> int | None:
    """Read a whole count of pieces or boxes. Thousands may be grouped with a
    comma, a dot or a space ("1,200", "1.200", "1 200"); there are no decimals."""
    t = _clean(text)
    if len(t) == 1:
        t = t.translate(_OCR_DIGIT_FIXES)  # a lone "O" in the loose column is a zero
    if not t or not re.fullmatch(r"\d{1,3}([.,]\d{3})+|\d+", t):
        return None
    return int(re.sub(r"[.,]", "", t))
