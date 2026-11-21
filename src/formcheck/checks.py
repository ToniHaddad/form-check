"""Checks on a rebuilt waybill or goods received note, and the flags a reviewer sees.

Checks:

* each line: boxes x pieces per box + loose pieces = total pieces
* loose pieces are fewer than a box (otherwise they should be another box)
* the expiry date is not already past on the document date
* the item code has the shape FAM-XXX-000 (families FOO, HYG, MED, SHE, NFI)
* the number of lines, total boxes and total pieces equal the sums of the lines

A cell that cannot be read gets its own flag, because a check that cannot run
is not a check that passed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from .numbers import format_count, parse_count
from .table import Note

FAMILIES = ("FOO", "HYG", "MED", "SHE", "NFI")
CODE_RE = re.compile(r"^(FOO|HYG|MED|SHE|NFI)-[A-Z]{3}-\d{3}$")
NAMES = {"ppb": "pieces per box", "boxes": "boxes", "loose": "loose pieces", "total": "total pieces"}

# Look-alike characters, fixed only where the code pattern says a letter or a digit belongs.
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"})
_TO_DIGIT = str.maketrans({"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8",
                           "Z": "2", "G": "6"})


@dataclass
class Flag:
    n: int
    kind: str  # line | loose | expired | code | lines_count | boxes_sum | pieces_sum | unreadable
    severity: str  # error | unreadable
    message: str
    cells: list[str]  # e.g. ["row4.boxes", "row4.total"]
    poly: list[list[float]] | None
    hint: str = ""
    row: int | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def normalise_code(text: str) -> str:
    """Read an item code the way the pattern expects it: letters in the first
    two groups, digits in the last. "F0O-RlC-0O1" becomes "FOO-RIC-001"; a code
    with a group missing or too long stays as it is and fails the check."""
    t = re.sub(r"\s+", "", text.upper())
    t = re.sub(r"[_.,–—]", "-", t)  # a hyphen read as a dot, a comma or a dash
    parts = t.split("-")
    if len(parts) != 3 or len(parts[0]) != 3 or len(parts[1]) != 3 or len(parts[2]) != 3:
        return t
    return "-".join([parts[0].translate(_TO_LETTER), parts[1].translate(_TO_LETTER), parts[2].translate(_TO_DIGIT)])


def parse_date(text: str) -> date | None:
    t = text.strip().translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5"}))
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", t.replace(" ", ""))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _hull(points: list[list[float]]) -> list[list[float]] | None:
    """Convex hull (monotone chain) of the outlines involved in a flag."""
    pts = sorted({(round(p[0], 1), round(p[1], 1)) for p in points})
    if len(pts) < 3:
        return [list(p) for p in pts] or None

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1] + upper[:-1]]


def _one_digit_apart(a: int, b: int) -> bool:
    sa, sb = str(a), str(b)
    return len(sa) == len(sb) and sum(x != y for x, y in zip(sa, sb)) == 1


def _swapped(a: int, b: int) -> bool:
    sa, sb = str(a), str(b)
    return len(sa) == len(sb) and sa != sb and sorted(sa) == sorted(sb)


def _hint(boxes: int, ppb: int, loose: int, total: int) -> str:
    """Say which single number would make the line add up, when one would."""
    expected = boxes * ppb + loose
    options = []
    if _one_digit_apart(expected, total):
        options.append(f"the total may be {format_count(expected)} with one digit misread or miswritten")
    elif _swapped(expected, total):
        options.append(f"the total may be {format_count(expected)} with two digits swapped")
    if ppb and (total - loose) % ppb == 0 and _one_digit_apart((total - loose) // ppb, boxes):
        options.append(f"the boxes may be {(total - loose) // ppb}")
    if 0 <= total - boxes * ppb < max(ppb, 1) and _one_digit_apart(total - boxes * ppb, loose):
        options.append(f"the loose pieces may be {total - boxes * ppb}")
    if not options:
        return ""
    return "Possible reading: " + "; or ".join(options) + "."


def run_checks(note: Note) -> list[Flag]:
    flags: list[Flag] = []

    def add(**kw) -> None:
        flags.append(Flag(n=len(flags) + 1, **kw))

    doc_date = parse_date(note.header["date"].text) if "date" in note.header else None
    sums = {"boxes": [], "total": []}
    complete = {"boxes": True, "total": True}
    expiry_skipped = False

    for i, row in enumerate(note.rows, start=1):
        label = row["no"].text.strip() if "no" in row and row["no"].text.strip().isdigit() else str(i)
        vals: dict[str, int | None] = {}
        for key in ("ppb", "boxes", "loose", "total"):
            c = row.get(key)
            vals[key] = parse_count(c.text) if c and c.text.strip() else None
        missing = [k for k, v in vals.items() if v is None]
        if missing:
            polys = [p for k in missing if row.get(k) and row[k].poly for p in row[k].poly]
            row_poly = note.row_polys[i - 1] if i - 1 < len(note.row_polys) else None
            add(kind="unreadable", severity="unreadable", row=i,
                message=f"Line {label}: could not read the {' and '.join(NAMES[k] for k in missing)}. "
                        "Check the photo and type it in.",
                cells=[f"row{i}.{k}" for k in missing], poly=_hull(polys) if polys else row_poly)
        for key in ("boxes", "total"):
            if vals[key] is None:
                complete[key] = False
            else:
                sums[key].append(vals[key])

        if not missing:
            b, p, lo, t = vals["boxes"], vals["ppb"], vals["loose"], vals["total"]
            expected = b * p + lo
            if expected != t:
                pts = [q for k in ("ppb", "boxes", "loose", "total") for q in (row[k].poly or [])]
                add(kind="line", severity="error", row=i,
                    message=f"Line {label}: {b} boxes × {p} + {lo} = {format_count(expected)} pieces, "
                            f"the note says {format_count(t)}.",
                    hint=_hint(b, p, lo, t), poly=_hull(pts),
                    cells=[f"row{i}.ppb", f"row{i}.boxes", f"row{i}.loose", f"row{i}.total"],
                    extra={"expected": expected, "written": t})
            if p > 1 and lo >= p:
                pts = [q for k in ("ppb", "loose") for q in (row[k].poly or [])]
                add(kind="loose", severity="error", row=i,
                    message=f"Line {label}: {lo} loose pieces is a box or more (a box holds {p}). "
                            f"It should read {b + lo // p} boxes + {lo % p}.",
                    poly=_hull(pts), cells=[f"row{i}.loose", f"row{i}.boxes"])

        code = row.get("code")
        if code and code.text.strip():
            norm = normalise_code(code.text)
            if not CODE_RE.match(norm):
                add(kind="code", severity="error", row=i,
                    message=f"Line {label}: item code \"{code.text.strip()}\" does not have the shape FAM-XXX-000 "
                            f"(families {', '.join(FAMILIES)}).",
                    poly=code.poly, cells=[f"row{i}.code"])
        exp = row.get("expiry")
        if exp and re.search(r"\d", exp.text):
            d = parse_date(exp.text)
            if d is None:
                add(kind="unreadable", severity="unreadable", row=i,
                    message=f"Line {label}: could not read the expiry date \"{exp.text.strip()}\".",
                    poly=exp.poly, cells=[f"row{i}.expiry"])
            elif doc_date is None:
                expiry_skipped = True
            elif d < doc_date:
                add(kind="expired", severity="error", row=i,
                    message=f"Line {label}: expiry {d:%d/%m/%Y} is already past on the document date "
                            f"{doc_date:%d/%m/%Y}.",
                    poly=exp.poly, cells=[f"row{i}.expiry"])
    if expiry_skipped:
        c = note.header.get("date")
        add(kind="unreadable", severity="unreadable", row=None,
            message="Could not read the document date, so expiry dates were not checked.",
            poly=c.poly if c else None, cells=["header.date"])

    labels = {"lines": "number of lines", "boxes": "total boxes", "pieces": "total pieces"}
    for key, kind in (("lines", "lines_count"), ("boxes", "boxes_sum"), ("pieces", "pieces_sum")):
        cell = note.totals.get(key)
        printed = parse_count(cell.text) if cell and cell.text.strip() else None
        if printed is None:
            add(kind="unreadable", severity="unreadable", row=None,
                message=f"Could not read the printed {labels[key]}.", cells=[f"totals.{key}"],
                poly=cell.poly if cell else None)
            continue
        if key == "lines":
            actual, what = len(note.rows), "lines were read"
            if actual != printed:
                add(kind=kind, severity="error", row=None, cells=[f"totals.{key}"], poly=cell.poly,
                    message=f"{actual} {what}, the note says {printed} lines.",
                    hint="A line may be missing from the photo or the count is wrong.",
                    extra={"expected": actual, "written": printed})
            continue
        col = "boxes" if key == "boxes" else "total"
        if not complete[col] or not sums[col]:
            continue  # an unreadable line is already flagged; the sum cannot be checked
        actual = sum(sums[col])
        if actual != printed:
            diff = printed - actual
            add(kind=kind, severity="error", row=None, cells=[f"totals.{key}"], poly=cell.poly,
                message=(f"The lines add up to {format_count(actual)} {'boxes' if key == 'boxes' else 'pieces'}, "
                         f"the note says {format_count(printed)} ({'+' if diff > 0 else '-'}{format_count(abs(diff))})."),
                hint=_sum_hint(note, diff, col),
                extra={"expected": actual, "written": printed})
    return flags


def _sum_hint(note: Note, diff: int, col: str) -> str:
    for i, row in enumerate(note.rows, start=1):
        c = row.get(col)
        v = parse_count(c.text) if c else None
        if v is not None and v + diff == 0:
            return f"The difference equals line {i} exactly: it may have been left out of the total."
    return ""


def summary(flags: list[Flag]) -> dict:
    errors = sum(f.severity == "error" for f in flags)
    unread = sum(f.severity == "unreadable" for f in flags)
    return {"errors": errors, "unreadable": unread, "ok": errors == 0 and unread == 0}
