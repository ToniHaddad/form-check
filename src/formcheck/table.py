"""Rebuild a waybill or goods received note from OCR word boxes.

Input: word boxes on the flattened page (``OcrPage``). Output: a ``Note`` with
header fields, a table of rows and columns, and the printed totals, each cell
carrying its text, OCR confidence and its outline on the original photo.

The steps:

1. Measure any skew left after flattening from the slope of the long text
   boxes, and rotate box centres so text lines run horizontally.
2. Find the table heading line by its labels (Pcs/box, Boxes, Loose, Total...)
   and use the heading positions as column ranges.
3. Chain the boxes between the heading and the totals into lines.
4. Place each box in the column whose range it overlaps most. Count columns
   are also found from the numbers themselves (they are right-aligned), and a
   box that the OCR merged across two columns is split at its spaces first.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .numbers import looks_numeric, parse_count
from .ocr import OcrPage, Word

COLUMN_KEYS = ["no", "code", "item", "unit", "ppb", "boxes", "loose", "total", "expiry"]
NUMERIC = ("ppb", "boxes", "loose", "total")
HEADER_FIELDS = {
    "doc_no": ["document no.", "document no", "document"],
    "date": ["date"],
    "project": ["project"],
    "warehouse": ["warehouse"],
    "party": ["destination", "supplier / donor", "supplier/donor", "supplier"],
    "keeper": ["keeper"],
}
# Content that names its own column, whatever the headings say.
_DATE_LIKE = re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}")
_CODE_LIKE = re.compile(r"[A-Z0-9]{2,4}[-.,][A-Z0-9]{2,4}[-.,][A-Z0-9]{1,4}", re.I)
TOTAL_LABELS = {
    "lines": ["number of lines", "no. of lines"],
    "boxes": ["total boxes"],
    "pieces": ["total pieces", "total pcs"],
}


@dataclass
class Cell:
    text: str
    conf: float = 1.0
    poly: list[list[float]] | None = None  # outline on the photo

    def to_dict(self) -> dict:
        return {"text": self.text, "conf": self.conf, "poly": self.poly}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Cell | None":
        if d is None:
            return None
        return cls(text=str(d.get("text", "")), conf=float(d.get("conf", 1.0)), poly=d.get("poly"))


@dataclass
class Note:
    doc_type: str  # "waybill", "grn" or "unknown"
    header: dict[str, Cell]
    rows: list[dict[str, Cell]]
    totals: dict[str, Cell]  # "lines", "boxes", "pieces"
    labels: dict[str, str] = field(default_factory=dict)  # printed column names
    row_polys: list[list[list[float]] | None] = field(default_factory=list)
    skew_deg: float = 0.0

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "header": {k: c.to_dict() for k, c in self.header.items()},
            "rows": [{k: c.to_dict() for k, c in r.items()} for r in self.rows],
            "totals": {k: c.to_dict() for k, c in self.totals.items()},
            "labels": self.labels,
            "row_polys": self.row_polys,
            "skew_deg": self.skew_deg,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Note":
        return cls(
            doc_type=d.get("doc_type", "unknown"),
            header={k: Cell.from_dict(c) for k, c in d.get("header", {}).items() if c is not None},
            rows=[{k: Cell.from_dict(c) for k, c in r.items() if c is not None} for r in d.get("rows", [])],
            totals={k: Cell.from_dict(c) for k, c in d.get("totals", {}).items() if c is not None},
            labels=d.get("labels", {}),
            row_polys=d.get("row_polys", []),
            skew_deg=float(d.get("skew_deg", 0.0)),
        )


# Pieces of a label the OCR may return as a separate box ("Waybill" + "No.").
_LABEL_LEFTOVERS = {"no", "no.", "n0", "n0.", "number", ":", "."}

# ---------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9. ]", "", s.lower()).strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class _Box:
    """A word in deskewed page coordinates."""
    word: Word
    text: str
    x0: float
    x1: float
    cy: float
    h: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


def estimate_skew(words: list[Word]) -> float:
    """Median slope, in degrees, of the top edges of long text boxes."""
    angles = []
    for w in words:
        (a, b, _, _) = w.poly
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx > 3 * max(w.height, 1):
            angles.append(math.degrees(math.atan2(dy, dx)))
    if not angles:
        return 0.0
    angles.sort()
    return angles[len(angles) // 2]


def _deskew(words: list[Word], deg: float, centre: tuple[float, float]) -> list[_Box]:
    t = math.radians(-deg)
    c, s = math.cos(t), math.sin(t)
    cx0, cy0 = centre
    boxes = []
    for w in words:
        pts = [((x - cx0) * c - (y - cy0) * s + cx0, (x - cx0) * s + (y - cy0) * c + cy0) for x, y in w.poly]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        boxes.append(_Box(w, w.text.strip(), min(xs), max(xs), sum(ys) / 4, max(w.height, 1.0)))
    return boxes


def _same_line(a: _Box, b: _Box) -> bool:
    return abs(a.cy - b.cy) < 0.6 * max(a.h, b.h)


def _find_label(boxes: list[_Box], variants: list[str], min_ratio: float = 0.8) -> tuple[_Box, str] | None:
    """Find a box whose text starts with one of ``variants``. Returns (box, rest of text)."""
    best = None
    for b in boxes:
        t = _norm(b.text)
        for v in variants:
            head = t[: len(v)]
            r = _similar(head, v)
            if r >= min_ratio and (best is None or r > best[0]):
                rest = b.text.strip()[len(v):].lstrip(" .:;-")
                best = (r, b, rest)
    return (best[1], best[2]) if best else None


# ---------------------------------------------------------------- main

def rebuild(page: OcrPage) -> Note:
    words = [w for w in page.words if w.text.strip()]
    skew = estimate_skew(words)
    centre = (page.flat_size[0] / 2, page.flat_size[1] / 2)
    boxes = _deskew(words, skew, centre)
    med_h = sorted(b.h for b in boxes)[len(boxes) // 2] if boxes else 20.0

    def cell(parts: list[_Box], text: str | None = None) -> Cell:
        pts = [p for b in parts for p in b.word.poly]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        rect = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
        return Cell(
            text=text if text is not None else " ".join(b.text for b in sorted(parts, key=lambda b: b.x0)),
            conf=round(min(b.word.conf for b in parts), 4),
            poly=page.poly_to_photo(rect),
        )

    # --- header fields: label box, value is the rest of the box or the next box to the right
    header: dict[str, Cell] = {}
    for key, variants in HEADER_FIELDS.items():
        found = _find_label(boxes, variants)
        if not found:
            continue
        lab, rest = found
        rest = re.sub(r"^[/\s]*(or\s+)?donor\b[\s:]*", "", rest, flags=re.I)  # rest of "Supplier / donor"
        if len(rest) >= 2:
            header[key] = cell([lab], rest)
            continue
        right = [b for b in boxes if b is not lab and _same_line(lab, b) and b.x0 > lab.x1 - 5
                 and b.x0 - lab.x1 < 8 * med_h and _norm(b.text) not in _LABEL_LEFTOVERS
                 and not re.fullmatch(r"[/\s]*(or\s+)?donor", b.text.strip(), flags=re.I)]
        if right:
            nearest = min(right, key=lambda b: b.x0)
            header[key] = cell([nearest])

    # --- table header line, anchored on the most distinctive label
    columns, labels, head_line = _find_columns(boxes, med_h)

    # --- totals
    totals: dict[str, Cell] = {}
    total_labels = []
    for key, variants in TOTAL_LABELS.items():
        # The line count sits right after its label; do not reach across to the box total.
        c, lab = _labelled_value(boxes, variants, cell, max_gap=6 * med_h if key == "lines" else None)
        if c:
            totals[key] = c
        if lab:
            total_labels.append(lab)

    # --- rows: everything between the header line and the first totals label
    stop_y = min([lab.cy for lab in total_labels] or [float("inf")])
    top_y = head_line if head_line is not None else max(
        [c_y for c_y in (_label_y(boxes, v) for v in HEADER_FIELDS.values()) if c_y is not None] or [0.0])
    body_lines = []
    for line in _group_lines(boxes):
        y = sum(b.cy for b in line) / len(line)
        if top_y + 0.9 * med_h < y < stop_y - 0.5 * med_h and any(re.search(r"\d", b.text) for b in line):
            body_lines.append(line)  # lines with no digit are captions such as "(USD)"

    width = page.flat_size[0]
    left_limit = columns["unit"][1] if "unit" in columns else 0.45 * width
    if head_line is None:
        # No table heading found: keep only lines that carry at least two
        # numbers in the right half, which drops the waybill block above.
        body_lines = [ln for ln in body_lines
                      if sum(looks_numeric(t.text) and t.x1 > left_limit for b in ln for t in _tokens(b)) >= 2]
    columns = _numeric_columns_from_data(body_lines, columns, left_limit, med_h)
    if "item" not in columns and body_lines:
        # Without an "Item" heading, text left of the numbers is the item.
        x_start = columns["no"][1] + 4 if "no" in columns else min(b.x0 for ln in body_lines for b in ln)
        x_end = columns["unit"][0] - 4 if "unit" in columns else left_limit
        if x_end > x_start:
            columns["item"] = (x_start, x_end)
    numeric_cols = {k: v for k, v in columns.items() if k in NUMERIC}
    text_cols = {k: v for k, v in columns.items() if k not in NUMERIC} or columns

    rows: list[dict[str, Cell]] = []
    row_polys = []
    for line in body_lines:
        assigned: dict[str, list[_Box]] = {}
        for b in line:
            for part in (q for piece in _peel(b) for q in _split_across(piece, columns)):
                compact = part.text.replace(" ", "")
                if _DATE_LIKE.fullmatch(compact):
                    assigned.setdefault("expiry", []).append(part)
                    continue
                if _CODE_LIKE.fullmatch(compact):
                    assigned.setdefault("code", []).append(part)
                    continue
                numeric = looks_numeric(part.text) and part.x1 > left_limit
                key = _best_column(part, numeric_cols if numeric and numeric_cols else text_cols)
                if key:
                    assigned.setdefault(key, []).append(part)
        if not any(k in assigned for k in NUMERIC):
            continue
        row = {k: cell(v) for k, v in assigned.items()}
        _fill_blank_loose(row)
        rows.append(row)
        row_polys.append(cell(line).poly)

    return Note(doc_type=_doc_type(boxes, header), header=header, rows=rows, totals=totals,
                labels=labels, row_polys=row_polys, skew_deg=round(skew, 2))


def _peel(b: _Box) -> list[_Box]:
    """Split an item code off the front of a box and a date off its end, for
    when the OCR read "MED-PAR-001 Paracetamol 500 mg" as one box."""
    toks = _tokens(b)
    if len(toks) < 2:
        return [b]
    out = []
    if _CODE_LIKE.fullmatch(toks[0].text):
        out.append(toks.pop(0))
    tail = toks.pop() if len(toks) > 1 and _DATE_LIKE.fullmatch(toks[-1].text) else None
    if toks:
        out.append(_Box(b.word, " ".join(t.text for t in toks), toks[0].x0, toks[-1].x1, b.cy, b.h))
    if tail:
        out.append(tail)
    return out if len(out) > 1 else [b]


def _fill_blank_loose(row: dict[str, Cell]) -> None:
    """The OCR often misses a lone "0" (and a blank loose cell means none). When
    the loose cell is empty and boxes x pieces per box already equals the total,
    write 0 there with a low confidence so the cell is tinted for review."""
    if "loose" in row:
        return
    vals = [parse_count(row[k].text) if k in row else None for k in ("ppb", "boxes", "total")]
    if None not in vals and vals[0] * vals[1] == vals[2]:
        row["loose"] = Cell("0", 0.5, None)


def _doc_type(boxes: list[_Box], header: dict[str, Cell]) -> str:
    """Waybill or goods received note, from the title or the document number."""
    text = " ".join(_norm(b.text) for b in boxes)
    no = header["doc_no"].text.upper() if "doc_no" in header else ""
    if "waybill" in text or no.startswith("DSP"):
        return "waybill"
    if "goods received" in text or "received note" in text or no.startswith("RCV"):
        return "grn"
    return "unknown"


def _label_y(boxes: list[_Box], variants: list[str]) -> float | None:
    found = _find_label(boxes, variants)
    return found[0].cy if found else None


def _numeric_columns_from_data(lines, columns, left_limit, med_h):
    """Counts are right-aligned, so their right edges stack up in clusters.
    The four rightmost clusters are pieces per box, boxes, loose pieces and
    total pieces. This still works when the OCR garbled a column heading."""
    toks = []
    for line in lines:
        for b in line:
            for part in _tokens(b):
                if looks_numeric(part.text) and part.x1 > left_limit:
                    toks.append(part)
    if not toks:
        return columns
    toks.sort(key=lambda t: t.x1)
    clusters = [[toks[0]]]
    for t in toks[1:]:
        if t.x1 - clusters[-1][-1].x1 > 1.5 * med_h:
            clusters.append([t])
        else:
            clusters[-1].append(t)
    strong = [c for c in clusters if len(c) >= max(2, 0.4 * len(lines))]
    hdr = {k: columns[k] for k in NUMERIC if k in columns}
    n = len(NUMERIC)
    if len(strong) == n or (not hdr and len(strong) > n):
        # The usual case: four clear count columns. Their order settles which
        # is which, even when the OCR garbled a heading into another heading.
        return {**columns, **{k: _span(c) for k, c in zip(NUMERIC, strong[-n:])}}
    if not hdr:
        return columns

    # Match each cluster to the heading whose right edge it lines up with.
    out = dict(columns)
    free = []
    for c in strong:
        right = max(t.x1 for t in c)
        k = min(hdr, key=lambda k: abs(hdr[k][1] - right))
        if abs(hdr[k][1] - right) < 2.5 * med_h:
            x0, x1 = _span(c)
            out[k] = (min(out[k][0], x0), max(out[k][1], x1))
        else:
            free.append(c)
    # A heading the OCR missed: take the one free cluster that sits between
    # the neighbouring columns it does know.
    i = 0
    while i < len(NUMERIC):
        if NUMERIC[i] in out:
            i += 1
            continue
        j = i
        while j < len(NUMERIC) and NUMERIC[j] not in out:
            j += 1
        run = NUMERIC[i:j]  # consecutive columns with no heading
        lo = max([out[n][1] for n in NUMERIC[:i] if n in out] or [-1.0])
        hi = min([out[n][0] for n in NUMERIC[j:] if n in out] or [float("inf")])
        fits = [c for c in free if lo < _span(c)[0] and _span(c)[1] < hi]
        if len(fits) == len(run):
            for k, c in zip(run, sorted(fits, key=lambda c: _span(c)[0])):
                out[k] = _span(c)
        i = j
    return out


def _span(c: list[_Box]) -> tuple[float, float]:
    return (min(t.x0 for t in c), max(t.x1 for t in c))


def _tokens(b: _Box) -> list[_Box]:
    words = b.text.split()
    if len(words) < 2:
        return [b]
    total = sum(len(t) for t in words) + len(words) - 1
    width = b.x1 - b.x0
    out, pos = [], 0
    for t in words:
        out.append(_Box(b.word, t, b.x0 + width * pos / total, b.x0 + width * (pos + len(t)) / total, b.cy, b.h))
        pos += len(t) + 1
    return out


def _classify_header(text: str) -> str | None:
    t = _norm(text)
    if not t:
        return None
    if "code" in t or _similar(t, "item code") >= 0.75:
        return "code"
    if re.search(r"(/ ?box|per ?box|pcs ?box|pcsbox|pc/b)", t):
        return "ppb"
    if t.startswith("tot") or _similar(t, "total") >= 0.75:
        return "total"
    if _similar(t, "boxes") >= 0.8:
        return "boxes"
    if _similar(t, "loose") >= 0.75:
        return "loose"
    if t.startswith("exp") or _similar(t, "expiry") >= 0.7:
        return "expiry"
    if _similar(t, "item") >= 0.75 or t.startswith("descr"):
        return "item"
    if _similar(t, "unit") >= 0.75:
        return "unit"
    if t in ("no.", "no", "n0", "#", "n.", "no:"):
        return "no"
    return None


def _find_columns(boxes: list[_Box], med_h: float):
    """Locate the table header line and return column ranges, printed labels and its height."""
    lines = _group_lines(boxes)
    best = None
    for line in lines:
        if not any(_classify_header(b.text) in ("boxes", "loose", "ppb") for b in line):
            continue
        # Two-line headings ("Total" over "pcs") sit a little higher
        # than one-line headings, so take everything near the line's height.
        y = sum(b.cy for b in line) / len(line)
        near = [b for b in boxes if b not in line and abs(b.cy - y) < 0.8 * med_h
                and _classify_header(b.text) is not None]
        line = sorted(line + near, key=lambda b: b.x0)
        found = {}
        for p in _merge_phrases(line, med_h):
            k = _classify_header(p.text)
            if k and k not in found:
                found[k] = p
        if len(found) >= 2 and (best is None or len(found) > len(best[0])):
            best = (found, sum(b.cy for b in line) / len(line))
    if not best:
        return {}, {}, None
    found, y = best
    columns = {k: (p.x0, p.x1) for k, p in found.items()}
    labels = {k: p.text for k, p in found.items()}
    return columns, labels, y


def _merge_phrases(line: list[_Box], med_h: float) -> list[_Box]:
    """Join two neighbouring boxes only when together they name a column, e.g. "Item" + "code"."""
    out: list[_Box] = []
    for b in line:
        if out and b.x0 - out[-1].x1 < 1.2 * med_h:
            p = out[-1]
            joined = _Box(p.word, f"{p.text} {b.text}", p.x0, b.x1, (p.cy + b.cy) / 2, max(p.h, b.h))
            k = _classify_header(joined.text)
            if k in ("code", "ppb") and _classify_header(p.text) != k and _classify_header(b.text) != k:
                out[-1] = joined
                continue
        out.append(b)
    return out


def _group_lines(boxes: list[_Box]) -> list[list[_Box]]:
    """Chain boxes left to right into text lines.

    Each box joins the line whose last box is closest in height, so a line can
    drift up or down across the page (residual perspective, a curled sheet)
    without being cut in two.
    """
    chains: list[list[_Box]] = []
    for b in sorted(boxes, key=lambda b: b.x0):
        best = None
        for ch in chains:
            last = ch[-1]
            if _overlap(last.x0, last.x1, b.x0, b.x1) > 0.3 * min(last.x1 - last.x0, b.x1 - b.x0):
                continue  # stacked above or below, not beside
            d = abs(b.cy - last.cy)
            if d < 0.5 * max(b.h, last.h) and (best is None or d < best[0]):
                best = (d, ch)
        if best:
            best[1].append(b)
        else:
            chains.append([b])
    chains.sort(key=lambda ch: sum(b.cy for b in ch) / len(ch))
    return chains


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _best_column(b: _Box, columns: dict[str, tuple[float, float]]) -> str | None:
    if not columns:
        return None
    scored = [(_overlap(b.x0, b.x1, x0, x1), -abs(b.cx - (x0 + x1) / 2), k) for k, (x0, x1) in columns.items()]
    scored.sort(reverse=True)
    return scored[0][2]


def _split_across(b: _Box, columns: dict[str, tuple[float, float]]) -> list[_Box]:
    """Split a box the OCR merged across columns, e.g. "sachet 100" over Unit and Pcs/box."""
    tokens = b.text.split()
    if len(tokens) < 2 or not columns:
        return [b]
    hit = [k for k, (x0, x1) in columns.items() if _overlap(b.x0, b.x1, x0, x1) > 0.3 * (x1 - x0)]
    numeric_tail = looks_numeric(tokens[-1])
    if len(hit) < 2 and not (numeric_tail and not looks_numeric(tokens[0])):
        return [b]
    # Estimate each token's extent from its share of the characters.
    parts = _tokens(b)
    # Re-join neighbouring tokens that land in the same column ("Rice, 25 kg bag").
    merged: list[_Box] = []
    for p in parts:
        k = _best_column(p, columns)
        if merged and _best_column(merged[-1], columns) == k:
            m = merged[-1]
            merged[-1] = _Box(m.word, f"{m.text} {p.text}", m.x0, p.x1, m.cy, m.h)
        else:
            merged.append(p)
    return merged


def _labelled_value(boxes: list[_Box], labels: list[str], cell,
                    max_gap: float | None = None) -> tuple[Cell | None, _Box | None]:
    found = _find_label(boxes, labels, 0.8)
    if not found:
        return None, None
    lab, rest = found
    tail = rest.split()
    if tail and looks_numeric(tail[-1]):
        return cell([lab], tail[-1]), lab
    # Take the nearest box to the right that has a digit in it, even if it does
    # not parse: a misread total should be shown and flagged, not lost.
    right = [b for b in boxes if b is not lab and _same_line(lab, b) and b.x0 > lab.x1 - 5
             and re.search(r"\d", b.text) and (max_gap is None or b.x0 - lab.x1 < max_gap)]
    if not right:
        return None, lab
    return cell([min(right, key=lambda b: b.x0)]), lab
