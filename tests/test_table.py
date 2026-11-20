"""Table reconstruction tested against recorded OCR output (no OCR model needed).

The fixtures are real RapidOCR output for generated photos, saved with
``formcheck check photo.jpg --save-ocr``. The truth files are the generator's
ground truth for the same notes.
"""

import copy
import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

from formcheck.checks import run_checks
from formcheck.numbers import parse_number
from formcheck.ocr import OcrPage, Word
from formcheck.table import Cell, Note, rebuild

FIX = Path(__file__).parent / "fixtures"


def load(fid):
    page = OcrPage.load(FIX / f"{fid}.ocr.json")
    truth = json.loads((FIX / f"{fid}.truth.json").read_text())
    return page, truth


def numbers(note):
    return [
        tuple(parse_number(r[k].text, note.style) if k in r else None for k in ("qty", "unit_value", "line_total"))
        for r in note.rows
    ]


def truth_numbers(truth):
    return [(Decimal(r["qty"]), Decimal(r["unit_value"]), Decimal(r["line_total"])) for r in truth["rows"]]


@pytest.mark.parametrize("fid", ["easy-5000", "easy-5003"])
def test_easy_notes_rebuild_exactly(fid):
    page, truth = load(fid)
    note = rebuild(page)
    assert note.style == truth["style"]
    assert numbers(note) == truth_numbers(truth)
    assert {k: c.text for k, c in note.header.items()} == truth["header"] or all(
        c.text.replace(" ", "") == truth["header"][k].replace(" ", "") for k, c in note.header.items())
    assert parse_number(note.grand_total.text, note.style) == Decimal(truth["grand_total"])
    assert parse_number(note.total_qty.text, note.style) == Decimal(truth["total_qty"])


def test_planted_line_error_is_flagged_at_its_line():
    page, truth = load("easy-5000")
    flags = run_checks(rebuild(page))
    assert truth["error"]["type"] == "line_total"
    assert [(f.kind, f.row) for f in flags] == [("line", truth["error"]["row"])]
    assert flags[0].message == "Line 2: 8 × 3,60 = 28,80, the note says 27,80."


def test_clean_note_raises_no_flag():
    page, truth = load("easy-5003")
    assert truth["error"] is None
    assert run_checks(rebuild(page)) == []


def test_missing_column_heading_is_recovered_from_number_positions():
    # On this photo the OCR did not read the "Unit price" heading at all.
    page, truth = load("medium-6001")
    texts = " ".join(w.text for w in page.words).lower()
    assert "price" not in texts
    note = rebuild(page)
    assert "unit_value" not in note.labels
    assert numbers(note) == truth_numbers(truth)
    flags = run_checks(note)
    assert [f.kind for f in flags] == ["total_sum"]  # the planted grand-total error, nothing else
    assert "line 1" in flags[0].hint  # the total leaves line 1 out


def test_decimal_comma_note_with_planted_error():
    page, truth = load("medium-6000")
    note = rebuild(page)
    assert note.style == "comma"
    assert len(note.rows) == len(truth["rows"])
    flags = run_checks(note)
    planted_row = truth["error"]["row"]
    assert any(f.kind == "line" and f.row == planted_row for f in flags)


def _rotate(page: OcrPage, deg: float) -> OcrPage:
    p = copy.deepcopy(page)
    t = math.radians(deg)
    cx, cy = p.flat_size[0] / 2, p.flat_size[1] / 2
    c, s = math.cos(t), math.sin(t)
    for w in p.words:
        w.poly = [[(x - cx) * c - (y - cy) * s + cx, (x - cx) * s + (y - cy) * c + cy] for x, y in w.poly]
    return p


@pytest.mark.parametrize("deg", [-4.0, 2.5, 5.0])
def test_skewed_page_still_rebuilds(deg):
    # As if the sheet had not been flattened: the whole page turned by a few degrees.
    page, truth = load("easy-5003")
    note = rebuild(_rotate(page, deg))
    assert abs(note.skew_deg - deg) < 1.0
    assert numbers(note) == truth_numbers(truth)


def test_rows_that_drift_across_the_page_stay_whole():
    # Residual perspective: the right side of every line sits lower than the left,
    # more so towards the bottom. Rows must not be cut in two.
    page, truth = load("easy-5003")
    p = copy.deepcopy(page)
    for w in p.words:
        w.poly = [[x, y + (x / 1240) * (y / 1754) * 30] for x, y in w.poly]
    note = rebuild(p)
    assert numbers(note) == truth_numbers(truth)


def test_box_merged_across_columns_is_split():
    page, truth = load("easy-5003")
    p = copy.deepcopy(page)
    # Merge the unit "pcs" and the quantity "19" of line 1 into one OCR box.
    unit = next(w for w in p.words if w.text == "pcs")
    qty = next(w for w in p.words if w.text == "19")
    merged = Word("pcs 19", min(unit.conf, qty.conf),
                  [[unit.x0, unit.y0], [qty.x1, unit.y0], [qty.x1, qty.y1], [unit.x0, qty.y1]])
    p.words = [w for w in p.words if w not in (unit, qty)] + [merged]
    note = rebuild(p)
    assert note.rows[0]["unit"].text == "pcs"
    assert note.rows[0]["qty"].text == "19"


def test_lost_decimal_mark_is_put_back_and_marked_unsure():
    page, truth = load("easy-5003")
    p = copy.deepcopy(page)
    w = next(w for w in p.words if w.text == "58.90")
    w.text = "5890"
    note = rebuild(p)
    cell = note.rows[0]["line_total"]
    assert cell.text == "58.90"
    assert cell.conf <= 0.8
    assert run_checks(note) == []


def test_without_any_table_heading():
    page, truth = load("easy-5003")
    p = copy.deepcopy(page)
    heading = {"No.", "Item", "Unit", "Unlt", "Qty", "Unitwt", "Line wt", "(kg)"}
    p.words = [w for w in p.words if w.text not in heading]
    note = rebuild(p)
    assert numbers(note) == truth_numbers(truth)


def test_hard_photo_flags_the_misread_instead_of_passing_it():
    # On this hard photo the OCR read 487.20 as 457.20. The tool cannot know
    # which is right, but the line no longer multiplies out, so it is flagged.
    page, truth = load("hard-7001")
    note = rebuild(page)
    flags = run_checks(note)
    assert any(f.kind == "line" and f.row == 2 for f in flags)
    assert any(f.kind == "total_sum" for f in flags)  # the planted grand-total error


def test_note_round_trips_through_json():
    page, _ = load("medium-6000")
    note = rebuild(page)
    again = Note.from_dict(json.loads(json.dumps(note.to_dict())))
    assert [f.to_dict() for f in run_checks(again)] == [f.to_dict() for f in run_checks(note)]
    assert isinstance(again.rows[0]["qty"], Cell)
