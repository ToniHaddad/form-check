"""Arithmetic checks on a rebuilt note, and the flags a reviewer sees.

Checks:

* each line: quantity x unit value = line total (to the cent)
* the quantity column adds up to the printed total quantity
* the line totals add up to the printed grand total

A cell that cannot be read as a number gets its own flag, because a check
that cannot run is not a check that passed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

from .numbers import format_number, parse_number
from .table import Note

TOLERANCE = Decimal("0.01")
NAMES = {"qty": "quantity", "unit_value": "unit value", "line_total": "line total"}


@dataclass
class Flag:
    n: int
    kind: str  # line | qty_sum | total_sum | unreadable
    severity: str  # error | unreadable
    message: str
    cells: list[str]  # e.g. ["row4.qty", "row4.line_total"]
    poly: list[list[float]] | None
    hint: str = ""
    row: int | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


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


def _one_digit_apart(a: str, b: str) -> bool:
    da = [c for c in a if c.isdigit()]
    db = [c for c in b if c.isdigit()]
    return len(da) == len(db) and sum(x != y for x, y in zip(da, db)) == 1


def _hint(qty: Decimal, uv: Decimal, total: Decimal, style: str) -> str:
    """Say which single number would make the line add up, if a one-digit change would."""
    options = []
    expected_total = (qty * uv).quantize(TOLERANCE)
    if _one_digit_apart(f"{expected_total:.2f}", f"{total:.2f}"):
        options.append(f"the line total may be a misread or miswritten {format_number(expected_total, style)}")
    if uv != 0:
        q = total / uv
        if q == q.to_integral_value() and _one_digit_apart(f"{q:.0f}", f"{qty:.0f}"):
            options.append(f"the quantity may be {format_number(q, style, 0)}")
    if qty != 0:
        u = (total / qty)
        if u == u.quantize(TOLERANCE) and _one_digit_apart(f"{u:.2f}", f"{uv:.2f}"):
            options.append(f"the unit value may be {format_number(u, style)}")
    if not options:
        return ""
    return "One changed digit would explain it: " + "; or ".join(options) + "."


def run_checks(note: Note) -> list[Flag]:
    style = note.style
    flags: list[Flag] = []

    def fmt(v: Decimal, dec: int = 2) -> str:
        return format_number(v, style, dec)

    def add(**kw) -> None:
        flags.append(Flag(n=len(flags) + 1, **kw))

    qty_values: list[Decimal] = []
    line_values: list[Decimal] = []
    qty_ok = line_ok = True

    for i, row in enumerate(note.rows, start=1):
        label = row["no"].text.strip() if "no" in row and row["no"].text.strip().isdigit() else str(i)
        vals: dict[str, Decimal | None] = {}
        for key in ("qty", "unit_value", "line_total"):
            c = row.get(key)
            vals[key] = parse_number(c.text, style) if c and c.text.strip() else None
        missing = [k for k, v in vals.items() if v is None]
        if missing:
            polys = [p for k in missing if row.get(k) and row[k].poly for p in row[k].poly]
            row_poly = note.row_polys[i - 1] if i - 1 < len(note.row_polys) else None
            what = " and ".join(NAMES[k] for k in missing)
            add(kind="unreadable", severity="unreadable", row=i,
                message=f"Line {label}: could not read the {what}. Check the photo and type it in.",
                cells=[f"row{i}.{k}" for k in missing],
                poly=_hull(polys) if polys else row_poly)
        if vals["qty"] is None:
            qty_ok = False
        else:
            qty_values.append(vals["qty"])
        if vals["line_total"] is None:
            line_ok = False
        else:
            line_values.append(vals["line_total"])
        if missing:
            continue
        q, u, t = vals["qty"], vals["unit_value"], vals["line_total"]
        expected = q * u
        if abs(expected - t) > TOLERANCE:
            pts = [p for k in ("qty", "unit_value", "line_total") for p in (row[k].poly or [])]
            add(kind="line", severity="error", row=i,
                message=f"Line {label}: {fmt(q, 0)} × {fmt(u)} = {fmt(expected)}, the note says {fmt(t)}.",
                hint=_hint(q, u, t, style),
                cells=[f"row{i}.qty", f"row{i}.unit_value", f"row{i}.line_total"],
                poly=_hull(pts),
                extra={"expected": str(expected.quantize(TOLERANCE)), "written": str(t)})

    for key, cell, values, ok, dec, what in (
        ("qty_sum", note.total_qty, qty_values, qty_ok, 0, "quantities"),
        ("total_sum", note.grand_total, line_values, line_ok, 2, "line totals"),
    ):
        ref = "total_qty" if key == "qty_sum" else "grand_total"
        printed = parse_number(cell.text, style) if cell and cell.text.strip() else None
        if printed is None:
            add(kind="unreadable", severity="unreadable", row=None,
                message=f"Could not read the printed {'total quantity' if key == 'qty_sum' else 'grand total'}.",
                cells=[ref], poly=cell.poly if cell else None)
            continue
        if not ok or not values:
            continue  # an unreadable line is already flagged; the sum cannot be checked
        s = sum(values, Decimal(0))
        if abs(s - printed) > TOLERANCE:
            diff = printed - s
            add(kind=key, severity="error", row=None,
                message=(f"The {what} add up to {fmt(s, dec)}, the note says {fmt(printed, dec)} "
                         f"({'+' if diff > 0 else '-'}{fmt(abs(diff), dec)})."),
                hint=_sum_hint(note, diff, key, style),
                cells=[ref], poly=cell.poly,
                extra={"expected": str(s), "written": str(printed)})
    return flags


def _sum_hint(note: Note, diff: Decimal, key: str, style: str) -> str:
    if key != "total_sum":
        return ""
    for i, row in enumerate(note.rows, start=1):
        c = row.get("line_total")
        v = parse_number(c.text, style) if c else None
        if v is not None and abs(v + diff) <= TOLERANCE:
            return f"The difference equals line {i} exactly: it may have been left out of the total."
    return ""


def summary(flags: list[Flag]) -> dict:
    errors = sum(f.severity == "error" for f in flags)
    unread = sum(f.severity == "unreadable" for f in flags)
    return {"errors": errors, "unreadable": unread, "ok": errors == 0 and unread == 0}
