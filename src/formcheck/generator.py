"""Synthetic Northbridge Relief delivery notes, rendered and then "photographed".

Everything here is synthetic. Northbridge Relief is a fictional NGO and the
drivers are made-up names. Every form is reproducible from its seed and comes
with ground truth: what is printed on the paper, and, when one was planted,
the error the paper contains.
"""

from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .numbers import COMMA, DOT, format_number

PAGE_W, PAGE_H = 1240, 1754
MARGIN = 80
DIFFICULTIES = ("easy", "medium", "hard")
ERROR_TYPES = ("line_total", "grand_total", "digit_changed")

# Column layout of the printed table: (key, left x, right x, alignment).
COLUMNS = [
    ("no", 80, 140, "center"),
    ("item", 140, 545, "left"),
    ("unit", 545, 655, "left"),
    ("qty", 655, 775, "right"),
    ("unit_value", 775, 955, "right"),
    ("line_total", 955, 1160, "right"),
]

ITEMS = [
    # name, unit, price range USD, unit weight kg
    ("Rice, 25 kg bag", "bag", (18, 32), 25.20),
    ("Wheat flour, 50 kg", "sack", (30, 45), 50.30),
    ("Red lentils, 1 kg", "pack", (1.2, 2.4), 1.02),
    ("Sunflower oil, 5 L", "bottle", (7, 11), 4.70),
    ("Sugar, 10 kg", "bag", (8, 12), 10.10),
    ("Family hygiene kit", "kit", (22, 38), 6.40),
    ("Thermal blanket", "pcs", (6, 14), 1.85),
    ("Jerry can, 20 L", "pcs", (3, 6), 0.95),
    ("Tarpaulin, 4 x 6 m", "roll", (12, 20), 3.60),
    ("Water purification tabs", "box", (4, 9), 0.55),
    ("Kitchen set", "set", (25, 40), 5.80),
    ("Solar lamp", "pcs", (9, 16), 0.42),
    ("Foam mattress", "pcs", (15, 28), 3.10),
    ("Infant formula, 400 g", "tin", (7, 12), 0.52),
    ("Chlorine solution, 1 L", "bottle", (1.5, 3), 1.08),
    ("Plastic sheeting", "roll", (60, 95), 18.50),
]

PLACES = {
    "LB": ("Beirut Central Warehouse", ["Zahle Field Office", "Tripoli Hub", "Saida Distribution Point", "Baalbek Field Office"]),
    "KE": ("Nairobi Logistics Hub", ["Garissa Field Office", "Kakuma Distribution Point", "Lodwar Hub", "Dadaab Field Office"]),
    "CO": ("Bogota Central Warehouse", ["Cucuta Field Office", "Riohacha Distribution Point", "Arauca Hub", "Pasto Field Office"]),
}
DRIVERS = {
    "LB": (["Rami", "Nadim", "Fadi", "Ziad", "Samir", "Walid"], ["Khoury", "Mansour", "Saleh", "Aoun", "Nasser", "Daher"]),
    "KE": (["Peter", "Joseph", "Daniel", "Samuel", "Brian", "Kevin"], ["Otieno", "Mwangi", "Kamau", "Odhiambo", "Wanjiru", "Kiptoo"]),
    "CO": (["Andres", "Camilo", "Diego", "Julian", "Mateo", "Sergio"], ["Rojas", "Herrera", "Castro", "Gomez", "Vargas", "Ortiz"]),
}
# Share of notes written with a decimal comma, per country office.
COMMA_SHARE = {"LB": 0.5, "KE": 0.1, "CO": 0.8}

CENT = Decimal("0.01")

INK = (28, 32, 40)
ACCENT = (24, 92, 104)
GRID = (150, 158, 165)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


@dataclass
class Row:
    no: int
    item: str
    unit: str
    qty: Decimal
    unit_value: Decimal
    line_total: Decimal  # as printed (may be wrong when an error is planted)

    def texts(self, style: str) -> dict[str, str]:
        return {
            "no": str(self.no),
            "item": self.item,
            "unit": self.unit,
            "qty": format_number(self.qty, style, 0),
            "unit_value": format_number(self.unit_value, style),
            "line_total": format_number(self.line_total, style),
        }


@dataclass
class Form:
    form_id: str
    seed: int
    difficulty: str
    style: str
    variant: str  # "price" or "weight"
    header: dict[str, str]
    rows: list[Row]
    total_qty: Decimal
    grand_total: Decimal
    error: dict | None = None
    extras: dict = field(default_factory=dict)

    @property
    def value_label(self) -> tuple[str, str, str]:
        if self.variant == "price":
            return ("Unit price", "Line total", "(USD)")
        return ("Unit wt", "Line wt", "(kg)")

    def truth(self) -> dict:
        return {
            "form_id": self.form_id,
            "seed": self.seed,
            "difficulty": self.difficulty,
            "style": self.style,
            "variant": self.variant,
            "synthetic": True,
            "header": self.header,
            "rows": [
                {
                    "no": r.no, "item": r.item, "unit": r.unit,
                    "qty": str(r.qty), "unit_value": str(r.unit_value), "line_total": str(r.line_total),
                    "printed": r.texts(self.style),
                }
                for r in self.rows
            ],
            "total_qty": str(self.total_qty),
            "grand_total": str(self.grand_total),
            "printed_totals": {
                "total_qty": format_number(self.total_qty, self.style, 0),
                "grand_total": format_number(self.grand_total, self.style),
            },
            "error": self.error,
            **self.extras,
        }


# ---------------------------------------------------------------- content

def _change_one_digit(value: Decimal, rng: random.Random, decimals: int) -> Decimal:
    """Change one digit of ``value`` (as written) to a different digit."""
    s = f"{value:.{decimals}f}"
    positions = [i for i, c in enumerate(s) if c.isdigit()]
    # Leave the leading digit alone if it would turn into a leading zero.
    while True:
        i = rng.choice(positions)
        new = rng.choice([d for d in "0123456789" if d != s[i]])
        if i == positions[0] and new == "0" and len(positions) > 1:
            continue
        return Decimal(s[:i] + new + s[i + 1:])


def _arithmetic_slip(value: Decimal, rng: random.Random) -> Decimal:
    """A plausible human slip: swapped digits, or off by a round amount."""
    s = f"{value:.2f}"
    whole = s.split(".")[0]
    if len(whole) >= 2 and rng.random() < 0.5:
        i = rng.randrange(len(whole) - 1)
        if whole[i] != whole[i + 1] and not (i == 0 and whole[1] == "0"):
            w = list(whole)
            w[i], w[i + 1] = w[i + 1], w[i]
            return Decimal("".join(w) + "." + s.split(".")[1])
    magnitude = Decimal(10) ** max(0, len(whole) - 2)
    delta = magnitude * rng.choice([1, 2, 3, 5, 8])
    return value + delta if rng.random() < 0.6 or value - delta <= 0 else value - delta


def make_form(seed: int, difficulty: str = "easy", error_rate: float = 0.4,
              force_error: str | None = None, n_rows: int | None = None) -> Form:
    rng = random.Random(seed)
    country = rng.choice(list(PLACES))
    style = COMMA if rng.random() < COMMA_SHARE[country] else DOT
    variant = rng.choice(["price", "weight"])
    origin, dests = PLACES[country]
    firsts, lasts = DRIVERS[country]
    header = {
        "waybill": f"NR-{country}-{rng.randint(10000, 99999)}",
        "date": f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2026",
        "origin": origin,
        "destination": rng.choice(dests),
        "driver": f"{rng.choice(firsts)} {rng.choice(lasts)}",
    }
    n = n_rows or rng.randint(4, 9)
    chosen = rng.sample(ITEMS, n)
    rows = []
    for i, (name, unit, (lo, hi), weight) in enumerate(chosen, start=1):
        if rng.random() < 0.3:
            qty = Decimal(rng.randint(100, 2400))
        else:
            qty = Decimal(rng.randint(2, 180))
        if variant == "price":
            uv = (Decimal(str(rng.uniform(lo, hi))) / Decimal("0.05")).quantize(Decimal(1)) * Decimal("0.05")
        else:
            uv = Decimal(str(weight))
        uv = uv.quantize(CENT)
        rows.append(Row(i, name, unit, qty, uv, (qty * uv).quantize(CENT, ROUND_HALF_UP)))
    total_qty = sum((r.qty for r in rows), Decimal(0))
    grand = sum((r.line_total for r in rows), Decimal(0))

    form = Form(f"{difficulty}-{seed}", seed, difficulty, style, variant, header, rows, total_qty, grand)

    err_type = force_error
    if err_type is None and rng.random() < error_rate:
        err_type = rng.choice(ERROR_TYPES)
    if err_type and err_type != "none":
        _plant_error(form, err_type, rng)
    return form


def _plant_error(form: Form, err_type: str, rng: random.Random) -> None:
    if err_type == "line_total":
        r = rng.choice(form.rows)
        correct = r.line_total
        r.line_total = _arithmetic_slip(correct, rng)
        # The person adding up the page used the wrong line total too, so the
        # grand total agrees with the lines and only the line check can see it.
        form.grand_total = sum((x.line_total for x in form.rows), Decimal(0))
        form.error = {"type": err_type, "row": r.no, "field": "line_total",
                      "correct": str(correct), "written": str(r.line_total)}
    elif err_type == "grand_total":
        correct = form.grand_total
        if rng.random() < 0.4 and len(form.rows) > 2:
            skipped = rng.choice(form.rows)
            written = correct - skipped.line_total
        else:
            written = _arithmetic_slip(correct, rng)
        form.grand_total = written
        form.error = {"type": err_type, "row": None, "field": "grand_total",
                      "correct": str(correct), "written": str(written)}
    elif err_type == "digit_changed":
        r = rng.choice(form.rows)
        fld = rng.choice(["qty", "unit_value", "line_total"])
        correct = getattr(r, fld)
        written = _change_one_digit(correct, rng, 0 if fld == "qty" else 2)
        setattr(r, fld, written)
        form.error = {"type": err_type, "row": r.no, "field": fld,
                      "correct": str(correct), "written": str(written)}
    else:
        raise ValueError(f"unknown error type {err_type}")


# ---------------------------------------------------------------- rendering

def _text(draw: ImageDraw.ImageDraw, xy, text, size, fill=INK, anchor="ls", bold=False):
    draw.text(xy, text, font=_font(size), fill=fill, anchor=anchor,
              stroke_width=1 if bold else 0, stroke_fill=fill)


def render(form: Form) -> tuple[Image.Image, dict]:
    """Draw the note. Returns the image and the printed boxes of every field."""
    img = Image.new("RGB", (PAGE_W, PAGE_H), (250, 248, 242))
    d = ImageDraw.Draw(img)
    boxes: dict[str, list[float]] = {}

    # Letterhead
    d.ellipse((MARGIN, 70, MARGIN + 70, 140), fill=ACCENT)
    _text(d, (MARGIN + 35, 106), "N", 44, fill=(250, 248, 242), anchor="mm", bold=True)
    _text(d, (MARGIN + 95, 112), "NORTHBRIDGE RELIEF", 44, bold=True)
    _text(d, (MARGIN + 95, 148), "Warehouse Delivery Note", 26, fill=ACCENT)
    _text(d, (PAGE_W - MARGIN, 112), "Form WH-07", 20, fill=GRID, anchor="rs")
    d.rectangle((MARGIN, 180, PAGE_W - MARGIN, 184), fill=ACCENT)

    fields = [
        ("waybill", "Waybill No.", MARGIN, 250),
        ("date", "Date", MARGIN, 305),
        ("driver", "Driver", MARGIN, 360),
        ("origin", "Origin", 640, 250),
        ("destination", "Destination", 640, 305),
    ]
    for key, label, x, y in fields:
        _text(d, (x, y), label, 21, fill=(90, 98, 108))
        _text(d, (x + 175, y), form.header[key], 25)
        boxes[f"header.{key}"] = list(d.textbbox((x + 175, y), form.header[key], font=_font(25), anchor="ls"))
        d.line((x + 170, y + 8, x + 540 if x == MARGIN else PAGE_W - MARGIN, y + 8), fill=(205, 208, 210), width=1)

    # Table
    top = 430
    head_h = 70
    row_h = 58
    v_label, t_label, unit_label = form.value_label
    headers = {"no": ("No.", ""), "item": ("Item", ""), "unit": ("Unit", ""), "qty": ("Qty", ""),
               "unit_value": (v_label, unit_label), "line_total": (t_label, unit_label)}
    d.rectangle((MARGIN, top, PAGE_W - MARGIN, top + head_h), fill=(226, 236, 236))
    for key, x0, x1, align in COLUMNS:
        l1, l2 = headers[key]
        ax, anchor = _anchor_x(x0, x1, align)
        _text(d, (ax, top + (32 if l2 else 44)), l1, 21, bold=True, anchor=anchor)
        if l2:
            _text(d, (ax, top + 58), l2, 18, fill=(90, 98, 108), anchor=anchor)
    y = top + head_h
    for r in form.rows:
        texts = r.texts(form.style)
        for key, x0, x1, align in COLUMNS:
            ax, anchor = _anchor_x(x0, x1, align)
            size = 23 if key != "item" else 22
            _text(d, (ax, y + 38), texts[key], size, anchor=anchor)
            boxes[f"row{r.no}.{key}"] = list(d.textbbox((ax, y + 38), texts[key], font=_font(size), anchor=anchor))
        y += row_h
        d.line((MARGIN, y, PAGE_W - MARGIN, y), fill=GRID, width=1)
    bottom = y
    d.rectangle((MARGIN, top, PAGE_W - MARGIN, bottom), outline=INK, width=2)
    for _, x0, _, _ in COLUMNS[1:]:
        d.line((x0, top, x0, bottom), fill=GRID, width=1)

    # Totals
    qty_col = COLUMNS[3]
    tot_col = COLUMNS[5]
    y1 = bottom + 60
    _text(d, (qty_col[1] - 30, y1), "Total quantity", 22, bold=True, anchor="rs")
    s = format_number(form.total_qty, form.style, 0)
    _text(d, (qty_col[2] - 12, y1), s, 24, bold=True, anchor="rs")
    boxes["total_qty"] = list(d.textbbox((qty_col[2] - 12, y1), s, font=_font(24), anchor="rs"))
    y2 = y1 + 60
    label = "Grand total (USD)" if form.variant == "price" else "Total weight (kg)"
    _text(d, (tot_col[1] - 30, y2), label, 22, bold=True, anchor="rs")
    s = format_number(form.grand_total, form.style)
    _text(d, (tot_col[2] - 12, y2), s, 24, bold=True, anchor="rs")
    boxes["grand_total"] = list(d.textbbox((tot_col[2] - 12, y2), s, font=_font(24), anchor="rs"))
    d.line((tot_col[1] - 10, y2 + 12, PAGE_W - MARGIN, y2 + 12), fill=INK, width=2)

    # Signatures and footer
    fy = PAGE_H - 230
    for i, lab in enumerate(["Dispatched by", "Received by", "Signature"]):
        x = MARGIN + i * 360
        d.line((x, fy, x + 320, fy), fill=INK, width=1)
        _text(d, (x, fy + 30), lab, 19, fill=(90, 98, 108))
    _text(d, (PAGE_W // 2, PAGE_H - 70), "SYNTHETIC SAMPLE - generated for testing, not a real document",
          16, fill=GRID, anchor="ms")
    return img, boxes


def _anchor_x(x0: int, x1: int, align: str) -> tuple[int, str]:
    if align == "left":
        return x0 + 12, "ls"
    if align == "right":
        return x1 - 12, "rs"
    return (x0 + x1) // 2, "ms"


# ---------------------------------------------------------------- photograph

PHOTO = {
    #           rot deg, corner jitter, light, blur sigma, noise, jpeg q, out scale
    "easy":   dict(rot=1.5, jitter=0.015, light=0.12, blur=0.6, noise=2.5, jpeg=88, scale=0.95),
    "medium": dict(rot=4.0, jitter=0.040, light=0.30, blur=1.1, noise=5.0, jpeg=65, scale=0.85),
    "hard":   dict(rot=7.0, jitter=0.070, light=0.45, blur=1.6, noise=8.0, jpeg=45, scale=0.72),
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
             error_rate: float = 0.4) -> list[Path]:
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
