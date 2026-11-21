"""Synthetic waybills and goods received notes, rendered and then "photographed".

Everything here is synthetic: the Organization is unnamed, and every project
code, partner, supplier, donor and person is invented. Lebanese place names
are used only as geography. Every document is reproducible from its seed and
comes with ground truth: what is printed on the paper and, when one was
planted, the error the paper contains.

Two documents are drawn:

* a waybill (``DSP-``): stock leaving a warehouse for one of the
  Organization's centres (sometimes a department inside it) or a partner;
* a goods received note (``RCV-``): stock coming in from a supplier under a
  purchase order, or as an in-kind donation.

Quantities are counted in pieces; boxes are a convenience, so each line reads
"boxes x pieces per box + loose pieces = total pieces".
"""

from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .numbers import format_count

PAGE_W, PAGE_H = 1240, 1754
MARGIN = 80
DIFFICULTIES = ("easy", "medium", "hard")
ERROR_TYPES = ("line_total", "digit_changed", "pieces_total", "boxes_total", "line_count",
               "loose_overflow", "expired", "bad_code")

# Column layout of the printed table: (key, left x, right x, alignment).
COLUMNS = [
    ("no", 80, 122, "center"),
    ("code", 122, 290, "left"),
    ("item", 290, 545, "left"),
    ("unit", 545, 632, "left"),
    ("ppb", 632, 722, "right"),
    ("boxes", 722, 810, "right"),
    ("loose", 810, 890, "right"),
    ("total", 890, 1010, "right"),
    ("expiry", 1010, 1160, "center"),
]
HEADINGS = {
    "no": ("No.", ""), "code": ("Item code", ""), "item": ("Item", ""), "unit": ("Unit", ""),
    "ppb": ("Pcs/box", ""), "boxes": ("Boxes", ""), "loose": ("Loose", "pcs"),
    "total": ("Total", "pcs"), "expiry": ("Expiry", ""),
}

# code, name, unit, pieces per box, tracks expiry
ITEMS = [
    ("FOO-RIC-001", "Rice 5 kg bag", "bag", 4, True),
    ("FOO-LEN-002", "Red lentils 1 kg", "pack", 12, True),
    ("FOO-CHE-004", "Canned cheese 200 g", "can", 24, True),
    ("FOO-OIL-003", "Sunflower oil 1 L", "bottle", 12, True),
    ("FOO-SUG-001", "Sugar 1 kg", "pack", 10, True),
    ("FOO-TUN-002", "Canned tuna 160 g", "can", 48, True),
    ("FOO-MLK-001", "Milk powder 400 g", "tin", 12, True),
    ("HYG-SOA-001", "Bar soap 125 g", "piece", 72, True),
    ("HYG-SHA-002", "Shampoo 400 ml", "bottle", 24, True),
    ("HYG-DIA-003", "Baby diapers size 4", "pack", 6, False),
    ("HYG-PAD-001", "Sanitary pads", "pack", 36, False),
    ("MED-PAR-001", "Paracetamol 500 mg", "box", 50, True),
    ("MED-ORS-002", "Oral rehydration salts", "sachet", 100, True),
    ("MED-GLV-003", "Examination gloves M", "box", 10, False),
    ("SHE-TAR-001", "Tarpaulin 4 x 6 m", "piece", 5, False),
    ("SHE-MAT-002", "Foam mattress", "piece", 4, False),
    ("NFI-BLK-001", "Thermal blanket", "piece", 10, False),
    ("NFI-JER-002", "Jerry can 20 L", "piece", 8, False),
    ("NFI-KIT-003", "Kitchen set", "set", 2, False),
    ("NFI-LMP-004", "Solar lamp", "piece", 20, False),
]

WAREHOUSES = [("WH-03", "Beirut Port Road"), ("WH-07", "Zahle Central"), ("WH-11", "Tripoli North"),
              ("WH-14", "Saida Coastal"), ("WH-19", "Baalbek Plain"), ("WH-22", "Jbeil Hills"),
              ("WH-26", "Halba Akkar"), ("WH-31", "Tyre South")]
PROJECTS = ["FA-2026", "EA-2026", "WASH-2026", "INKIND"]
CENTRES = ["Jbeil Centre", "Zahle Centre", "Saida Centre", "Halba Health Centre", "Tripoli Centre",
           "Baalbek Centre"]
DEPARTMENTS = ["Social", "Medical", "Logistics"]
PARTNERS = ["Cedar Hands Association", "Olive Branch Relief Group", "Valley Neighbours Network"]
SUPPLIERS = ["Mountain Grains Trading", "Coastline Hygiene Supplies", "Bekaa Fresh Foods"]
DONORS = ["Friends of the Valley Fund", "St. Jude Parish Appeal", "Harbour Rotary Circle"]
KEEPERS = ["Rami Khoury", "Nadia Saleh", "Fadi Mansour", "Layla Aoun", "Ziad Nasser", "Maya Daher",
           "Samir Haddad", "Rita Fares"]

INK = (28, 32, 40)
ACCENT = (24, 92, 104)
GRID = (150, 158, 165)
LABEL = (90, 98, 108)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


@dataclass
class Row:
    no: int
    code: str
    item: str
    unit: str
    ppb: int
    boxes: int
    loose: int
    total: int          # as printed (may be wrong when an error is planted)
    expiry: date | None

    def texts(self, sep: str) -> dict[str, str]:
        return {
            "no": str(self.no), "code": self.code, "item": self.item, "unit": self.unit,
            "ppb": format_count(self.ppb, sep), "boxes": format_count(self.boxes, sep),
            "loose": format_count(self.loose, sep), "total": format_count(self.total, sep),
            "expiry": self.expiry.strftime("%d/%m/%Y") if self.expiry else "-",
        }


@dataclass
class Form:
    form_id: str
    seed: int
    difficulty: str
    doc_type: str  # "waybill" or "grn"
    sep: str       # thousands separator: "," "." or ""
    header: dict[str, str]
    doc_date: date
    rows: list[Row]
    lines: int
    total_boxes: int
    total_pieces: int
    error: dict | None = None
    extras: dict = field(default_factory=dict)

    def truth(self) -> dict:
        return {
            "form_id": self.form_id, "seed": self.seed, "difficulty": self.difficulty,
            "doc_type": self.doc_type, "thousands_separator": self.sep, "synthetic": True,
            "header": self.header,
            "rows": [{**{k: v for k, v in r.__dict__.items() if k != "expiry"},
                      "expiry": r.expiry.isoformat() if r.expiry else None,
                      "printed": r.texts(self.sep)} for r in self.rows],
            "totals": {"lines": self.lines, "boxes": self.total_boxes, "pieces": self.total_pieces},
            "error": self.error,
            **self.extras,
        }


# ---------------------------------------------------------------- content

def _change_one_digit(value: int, rng: random.Random) -> int:
    s = str(value)
    while True:
        i = rng.randrange(len(s))
        new = rng.choice([d for d in "0123456789" if d != s[i]])
        if i == 0 and new == "0" and len(s) > 1:
            continue
        return int(s[:i] + new + s[i + 1:])


def _slip(value: int, rng: random.Random) -> int:
    """A plausible human slip: swapped digits, or off by a round amount."""
    s = str(value)
    if len(s) >= 2 and rng.random() < 0.5:
        i = rng.randrange(len(s) - 1)
        if s[i] != s[i + 1] and not (i == 0 and s[1] == "0"):
            t = list(s)
            t[i], t[i + 1] = t[i + 1], t[i]
            return int("".join(t))
    step = 10 ** max(0, len(s) - 2)
    delta = step * rng.choice([1, 2, 3, 5])
    return value + delta if rng.random() < 0.6 or value - delta <= 0 else value - delta


def make_form(seed: int, difficulty: str = "easy", error_rate: float = 0.5,
              force_error: str | None = None, n_rows: int | None = None) -> Form:
    rng = random.Random(seed)
    doc_type = rng.choice(["waybill", "grn"])
    sep = rng.choices([",", ".", ""], weights=[6, 3, 1])[0]
    doc_date = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 330))
    wh_code, wh_name = rng.choice(WAREHOUSES)
    project = rng.choice(PROJECTS)
    if doc_type == "waybill":
        doc_no = f"DSP-2026-{rng.randint(1, 1500):05d}"
        if rng.random() < 0.75:
            party = rng.choice(CENTRES)
            if rng.random() < 0.6:
                party += f" / {rng.choice(DEPARTMENTS)}"
        else:
            party = rng.choice(PARTNERS)
    else:
        doc_no = f"RCV-2026-{rng.randint(1, 1500):05d}"
        if project == "INKIND" or rng.random() < 0.3:
            party = f"In-kind: {rng.choice(DONORS)}"
        else:
            party = f"{rng.choice(SUPPLIERS)}, PO-{rng.randint(100, 999)}"
    header = {
        "doc_no": doc_no,
        "date": doc_date.strftime("%d/%m/%Y"),
        "warehouse": f"{wh_code} {wh_name}",
        "project": project,
        "party": party,
        "keeper": rng.choice(KEEPERS),
    }
    n = n_rows or rng.randint(4, 9)
    rows = []
    for i, (code, name, unit, ppb, tracks) in enumerate(rng.sample(ITEMS, n), start=1):
        boxes = rng.randint(1, 60) if ppb > 1 else 0
        loose = rng.randint(0, ppb - 1) if ppb > 1 and rng.random() < 0.6 else 0
        if ppb == 1:
            loose = rng.randint(5, 120)
        expiry = doc_date + timedelta(days=rng.randint(90, 900)) if tracks else None
        rows.append(Row(i, code, name, unit, ppb, boxes, loose, boxes * ppb + loose, expiry))
    form = Form(f"{difficulty}-{seed}", seed, difficulty, doc_type, sep, header, doc_date, rows,
                len(rows), sum(r.boxes for r in rows), sum(r.total for r in rows))

    err_type = force_error
    if err_type is None and rng.random() < error_rate:
        err_type = rng.choice(ERROR_TYPES)
    if err_type and err_type != "none":
        _plant_error(form, err_type, rng)
    return form


def _plant_error(form: Form, err_type: str, rng: random.Random) -> None:
    boxed = [r for r in form.rows if r.ppb > 1]
    r = rng.choice(boxed or form.rows)
    if err_type == "line_total":
        correct = r.total
        r.total = _slip(correct, rng)
        # The person adding up the page used the wrong figure too, so only the
        # line check can see it.
        form.total_pieces = sum(x.total for x in form.rows)
        form.error = {"type": err_type, "row": r.no, "field": "total", "correct": correct, "written": r.total}
    elif err_type == "digit_changed":
        fld = rng.choice(["boxes", "loose", "total"] if r.loose else ["boxes", "total"])
        correct = getattr(r, fld)
        written = _change_one_digit(correct, rng)
        setattr(r, fld, written)
        form.error = {"type": err_type, "row": r.no, "field": fld, "correct": correct, "written": written}
    elif err_type == "pieces_total":
        correct = form.total_pieces
        if rng.random() < 0.4 and len(form.rows) > 2:
            written = correct - rng.choice(form.rows).total  # a line left out
        else:
            written = _slip(correct, rng)
        form.total_pieces = written
        form.error = {"type": err_type, "row": None, "field": "pieces", "correct": correct, "written": written}
    elif err_type == "boxes_total":
        correct = form.total_boxes
        written = _slip(correct, rng)
        form.total_boxes = written
        form.error = {"type": err_type, "row": None, "field": "boxes", "correct": correct, "written": written}
    elif err_type == "line_count":
        correct = form.lines
        written = correct + rng.choice([-1, 1, 2])
        form.lines = written
        form.error = {"type": err_type, "row": None, "field": "lines", "correct": correct, "written": written}
    elif err_type == "loose_overflow":
        # "10 boxes + 30 pieces" of a 24-piece box: the total still adds up,
        # but 24 of the loose pieces should have been written as one more box.
        extra = r.ppb + rng.randint(0, r.ppb - 1)
        old_boxes, old_loose = r.boxes, r.loose
        r.boxes = max(0, r.boxes - 1)
        r.loose = r.total - r.boxes * r.ppb
        if r.loose < r.ppb:  # only possible when boxes was 0
            r.loose += extra
            r.total = r.boxes * r.ppb + r.loose
            form.total_pieces = sum(x.total for x in form.rows)
        form.total_boxes = sum(x.boxes for x in form.rows)
        form.error = {"type": err_type, "row": r.no, "field": "loose",
                      "correct": f"{old_boxes} boxes + {old_loose}", "written": f"{r.boxes} boxes + {r.loose}"}
    elif err_type == "expired":
        with_expiry = [x for x in form.rows if x.expiry] or [r]
        r = rng.choice(with_expiry)
        correct = r.expiry
        r.expiry = form.doc_date - timedelta(days=rng.randint(3, 200))
        form.error = {"type": err_type, "row": r.no, "field": "expiry",
                      "correct": correct.isoformat() if correct else None, "written": r.expiry.isoformat()}
    elif err_type == "bad_code":
        correct = r.code
        fam, mid, num = correct.split("-")
        written = rng.choice([f"{fam}-{mid}-{num[1:]}", f"{fam}{mid}-{num}", f"{fam[:2]}-{mid}-{num}",
                              f"{fam}-{mid}-{num}{rng.randint(0, 9)}"])
        r.code = written
        form.error = {"type": err_type, "row": r.no, "field": "code", "correct": correct, "written": written}
    else:
        raise ValueError(f"unknown error type {err_type}")


# ---------------------------------------------------------------- rendering

def _text(draw: ImageDraw.ImageDraw, xy, text, size, fill=INK, anchor="ls", bold=False):
    draw.text(xy, text, font=_font(size), fill=fill, anchor=anchor,
              stroke_width=1 if bold else 0, stroke_fill=fill)


def render(form: Form) -> tuple[Image.Image, dict]:
    """Draw the document. Returns the image and the printed boxes of the fields."""
    img = Image.new("RGB", (PAGE_W, PAGE_H), (250, 248, 242))
    d = ImageDraw.Draw(img)
    boxes: dict[str, list[float]] = {}
    title = "Waybill (dispatch)" if form.doc_type == "waybill" else "Goods Received Note"

    # Letterhead
    d.rounded_rectangle((MARGIN, 72, MARGIN + 66, 138), radius=12, fill=ACCENT)
    _text(d, (MARGIN + 33, 106), "O", 40, fill=(250, 248, 242), anchor="mm", bold=True)
    _text(d, (MARGIN + 90, 106), "The Organization", 40, bold=True)
    _text(d, (MARGIN + 90, 142), title, 27, fill=ACCENT)
    _text(d, (PAGE_W - MARGIN, 104), form.header["doc_no"], 30, anchor="rs", bold=True)
    _text(d, (PAGE_W - MARGIN, 136), "Original copy", 18, fill=GRID, anchor="rs")
    d.rectangle((MARGIN, 172, PAGE_W - MARGIN, 176), fill=ACCENT)

    party_label = "Destination" if form.doc_type == "waybill" else "Supplier / donor"
    fields = [
        ("doc_no", "Document No.", MARGIN, 240),
        ("date", "Date", MARGIN, 292),
        ("project", "Project", MARGIN, 344),
        ("warehouse", "Warehouse", 600, 240),
        ("party", party_label, 600, 292),
        ("keeper", "Keeper", 600, 344),
    ]
    for key, label, x, y in fields:
        _text(d, (x, y), label, 20, fill=LABEL)
        vx = x + (175 if x == MARGIN else 185)
        _text(d, (vx, y), form.header[key], 23)
        boxes[f"header.{key}"] = list(d.textbbox((vx, y), form.header[key], font=_font(23), anchor="ls"))
        d.line((vx - 5, y + 8, 540 if x == MARGIN else PAGE_W - MARGIN, y + 8), fill=(205, 208, 210), width=1)

    # Table
    top, head_h, row_h = 410, 66, 56
    d.rectangle((MARGIN, top, PAGE_W - MARGIN, top + head_h), fill=(226, 236, 236))
    for key, x0, x1, align in COLUMNS:
        l1, l2 = HEADINGS[key]
        ax, anchor = _anchor_x(x0, x1, align)
        _text(d, (ax, top + (30 if l2 else 42)), l1, 19, bold=True, anchor=anchor)
        if l2:
            _text(d, (ax, top + 54), l2, 16, fill=LABEL, anchor=anchor)
    y = top + head_h
    for r in form.rows:
        texts = r.texts(form.sep)
        for key, x0, x1, align in COLUMNS:
            ax, anchor = _anchor_x(x0, x1, align)
            size = {"code": 19, "item": 20, "expiry": 19}.get(key, 21)
            _text(d, (ax, y + 36), texts[key], size, anchor=anchor)
            boxes[f"row{r.no}.{key}"] = list(d.textbbox((ax, y + 36), texts[key], font=_font(size), anchor=anchor))
        y += row_h
        d.line((MARGIN, y, PAGE_W - MARGIN, y), fill=GRID, width=1)
    bottom = y
    d.rectangle((MARGIN, top, PAGE_W - MARGIN, bottom), outline=INK, width=2)
    for _, x0, _, _ in COLUMNS[1:]:
        d.line((x0, top, x0, bottom), fill=GRID, width=1)

    # Totals
    col = {k: (x0, x1) for k, x0, x1, _ in COLUMNS}
    y1 = bottom + 58
    _text(d, (MARGIN, y1), "Number of lines", 21, bold=True)
    s = str(form.lines)
    _text(d, (MARGIN + 190, y1), s, 23, bold=True)
    boxes["totals.lines"] = list(d.textbbox((MARGIN + 190, y1), s, font=_font(23), anchor="ls"))
    _text(d, (col["ppb"][0] - 10, y1), "Total boxes", 21, bold=True, anchor="rs")
    s = format_count(form.total_boxes, form.sep)
    _text(d, (col["boxes"][1] - 12, y1), s, 23, bold=True, anchor="rs")
    boxes["totals.boxes"] = list(d.textbbox((col["boxes"][1] - 12, y1), s, font=_font(23), anchor="rs"))
    y2 = y1 + 56
    _text(d, (col["loose"][0] - 10, y2), "Total pieces", 21, bold=True, anchor="rs")
    s = format_count(form.total_pieces, form.sep)
    _text(d, (col["total"][1] - 12, y2), s, 23, bold=True, anchor="rs")
    boxes["totals.pieces"] = list(d.textbbox((col["total"][1] - 12, y2), s, font=_font(23), anchor="rs"))
    d.line((col["total"][0], y2 + 12, col["total"][1], y2 + 12), fill=INK, width=2)

    # Signature boxes
    fy = PAGE_H - 330
    for i, lab in enumerate(["Warehouse keeper", "Received by", "Driver"]):
        x = MARGIN + i * 365
        d.rectangle((x, fy, x + 330, fy + 150), outline=GRID, width=1)
        _text(d, (x + 14, fy + 30), lab, 19, fill=LABEL, bold=True)
        _text(d, (x + 14, fy + 85), "Name", 16, fill=LABEL)
        _text(d, (x + 14, fy + 132), "Signature and date", 16, fill=LABEL)
    _text(d, (PAGE_W // 2, PAGE_H - 70), "SYNTHETIC SAMPLE - generated for testing, not a real document",
          16, fill=GRID, anchor="ms")
    return img, boxes


def _anchor_x(x0: int, x1: int, align: str) -> tuple[int, str]:
    if align == "left":
        return x0 + 10, "ls"
    if align == "right":
        return x1 - 10, "rs"
    return (x0 + x1) // 2, "ms"


# ---------------------------------------------------------------- photograph

PHOTO = {
    #           rot deg, corner jitter, light, blur sigma, noise, jpeg q, out scale
    "easy":   dict(rot=1.5, jitter=0.015, light=0.12, blur=0.6, noise=2.5, jpeg=88, scale=0.95),
    "medium": dict(rot=4.0, jitter=0.040, light=0.30, blur=1.1, noise=5.0, jpeg=65, scale=0.85),
    "hard":   dict(rot=7.0, jitter=0.070, light=0.45, blur=1.4, noise=8.0, jpeg=50, scale=0.72),
}


def photograph(page: Image.Image, difficulty: str, seed: int, *, glare: bool = False,
               shadow: bool | None = None, **override) -> tuple[bytes, list[list[float]]]:
    """Simulate a phone photo of ``page``. Returns JPEG bytes and the page corners in it."""
    p = {**PHOTO[difficulty], **override}
    rng = np.random.default_rng(seed + 7919)
    pw, ph = page.size
    cw, ch = int(pw * 1.28), int(ph * 1.20)

    bg = _background(cw, ch, rng)

    s = rng.uniform(0.82, 0.88)
    cx, cy = cw / 2 + rng.uniform(-0.03, 0.03) * cw, ch / 2 + rng.uniform(-0.02, 0.02) * ch
    ang = np.deg2rad(rng.uniform(-p["rot"], p["rot"]))
    half = np.array([[-pw, -ph], [pw, -ph], [pw, ph], [-pw, ph]], dtype=np.float64) * s / 2
    rot = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    corners = half @ rot.T + [cx, cy]
    corners += rng.uniform(-p["jitter"], p["jitter"], size=(4, 2)) * pw * s
    src = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(src, corners.astype(np.float32))

    page_np = np.asarray(page, dtype=np.uint8)
    warped = cv2.warpPerspective(page_np, H, (cw, ch), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), H, (cw, ch))
    mask = cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32)[..., None] / 255.0
    img = warped.astype(np.float32) * mask + bg * (1 - mask)

    # Uneven light: a gradient across the sheet plus a vignette, computed on a
    # coarse grid (light varies slowly) and scaled up.
    k = 8
    gh, gw = ch // k + 1, cw // k + 1
    yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float32) * k
    theta = rng.uniform(0, 2 * np.pi)
    g = (np.cos(theta) * (xx / cw - 0.5) + np.sin(theta) * (yy / ch - 0.5)) + 0.5
    light = 1.0 - p["light"] * np.clip(g, 0, 1)
    r2 = ((xx / cw - 0.5) ** 2 + (yy / ch - 0.5) ** 2)
    light *= 1.0 - 0.35 * p["light"] * r2 / 0.5
    if shadow if shadow is not None else difficulty == "hard":
        sx, sy = rng.uniform(0.2, 0.8) * cw, rng.uniform(0.2, 0.8) * ch
        rad = rng.uniform(0.18, 0.3) * cw
        d2 = ((xx - sx) ** 2 + (yy - sy) ** 2) / rad ** 2
        light *= 1.0 - 0.32 * np.exp(-d2)
    glare_map = None
    if glare:
        gx, gy = override.get("glare_xy", (rng.uniform(0.4, 0.7) * cw, rng.uniform(0.45, 0.7) * ch))
        rad = override.get("glare_r", 0.13 * cw)
        d2 = ((xx - gx) ** 2 + (yy - gy) ** 2) / rad ** 2
        glare_map = cv2.resize((255 * 1.25 * np.exp(-d2 ** 1.5)).astype(np.float32), (cw, ch))
    light = cv2.resize(light.astype(np.float32), (cw, ch), interpolation=cv2.INTER_LINEAR)
    img *= light[..., None]
    if glare_map is not None:
        img += glare_map[..., None]

    img = np.clip(img, 0, 255).astype(np.uint8)
    if p["blur"] > 0:
        img = cv2.GaussianBlur(img, (0, 0), p["blur"])
    out_w, out_h = int(cw * p["scale"]), int(ch * p["scale"])
    img = cv2.resize(img, (out_w, out_h), interpolation=cv2.INTER_AREA)
    img = img.astype(np.float32) + rng.normal(0, p["noise"], img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=int(p["jpeg"]))
    return buf.getvalue(), (corners * p["scale"]).round(1).tolist()


def _background(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    base = np.array([
        [112, 82, 58],    # wooden desk
        [84, 86, 90],     # grey bench
        [58, 66, 78],     # dark table
        [140, 128, 110],  # cardboard
    ][rng.integers(0, 4)], dtype=np.float32)
    low = cv2.resize(rng.normal(0, 1, (8, 8)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    grain = cv2.resize(rng.normal(0, 1, (h // 6, w // 40)).astype(np.float32), (w, h))
    bg = base[None, None, :] * (1 + 0.08 * low[..., None] + 0.05 * grain[..., None])
    return bg


# ---------------------------------------------------------------- batch

def generate(out_dir: Path, count: int, difficulty: str, start_seed: int = 1000,
             error_rate: float = 0.5) -> list[Path]:
    """Write ``count`` photos and their ground truth JSON into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k in range(count):
        seed = start_seed + k
        form = make_form(seed, difficulty, error_rate)
        page, boxes = render(form)
        photo, corners = photograph(page, difficulty, seed)
        stem = out_dir / form.form_id
        stem.with_suffix(".jpg").write_bytes(photo)
        truth = form.truth()
        truth["page_corners"] = corners
        stem.with_suffix(".json").write_text(json.dumps(truth, indent=2))
        paths.append(stem.with_suffix(".jpg"))
    return paths
