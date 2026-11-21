"""Table reconstruction tested against recorded OCR output (no OCR model needed).

The fixtures are real RapidOCR output for generated photos, saved with
``formcheck check photo.jpg --save-ocr``. The truth files are the generator's
ground truth for the same documents.
"""

import copy
import json
import math
from pathlib import Path

import pytest

from formcheck.checks import normalise_code, run_checks
from formcheck.numbers import parse_count
from formcheck.ocr import OcrPage, Word
from formcheck.table import Cell, Note, rebuild

FIX = Path(__file__).parent / "fixtures"
COUNTS = ("ppb", "boxes", "loose", "total")


def load(fid):
    page = OcrPage.load(FIX / f"{fid}.ocr.json")
    truth = json.loads((FIX / f"{fid}.truth.json").read_text())
    return page, truth


def counts(note):
    return [tuple(parse_count(r[k].text) if k in r else None for k in COUNTS) for r in note.rows]


def truth_counts(truth):
    return [tuple(r[k] for k in COUNTS) for r in truth["rows"]]


def squash(s):
    return s.replace(" ", "").lower()


def test_waybill_rebuilds_exactly():
    page, truth = load("easy-3")
    note = rebuild(page)
    assert note.doc_type == "waybill"
    assert counts(note) == truth_counts(truth)
    assert [normalise_code(r["code"].text) for r in note.rows] == [r["code"] for r in truth["rows"]]
    for k, v in truth["header"].items():
        assert squash(note.header[k].text) == squash(v), k
    assert {k: parse_count(c.text) for k, c in note.totals.items()} == truth["totals"]


def test_goods_received_note_header():
    page, truth = load("medium-24")
    note = rebuild(page)
    assert note.doc_type == "grn"
    assert note.header["doc_no"].text.startswith("RCV-2026-")
    # "Supplier / donor" is the label; the value is the donor, not the rest of the label.
    assert squash(note.header["party"].text) == squash(truth["header"]["party"])


@pytest.mark.parametrize("fid,kind", [("easy-3", "line"), ("medium-4", "loose"), ("medium-22", "expired"),
                                      ("easy-23", "code"), ("medium-24", "pieces_sum")])
def test_planted_error_is_flagged_where_it_is(fid, kind):
    page, truth = load(fid)
    flags = run_checks(rebuild(page))
    err = truth["error"]
    assert any(f.kind == kind and (err["row"] is None or f.row == err["row"]) for f in flags), \
        [(f.kind, f.row) for f in flags]


def test_line_error_message():
    page, _ = load("easy-3")
    flags = run_checks(rebuild(page))
    assert [(f.kind, f.row) for f in flags] == [("line", 8)]
    assert flags[0].message == "Line 8: 26 boxes × 24 + 0 = 624 pieces, the note says 604."


def test_missed_zero_in_loose_column_is_filled_and_tinted():
    page, truth = load("easy-3")
    p = copy.deepcopy(page)
    # Drop the "0" the OCR read in line 1's loose column.
    row_y = next(w for w in p.words if w.text == "1,080").cy
    zero = next(w for w in p.words if w.text == "0" and abs(w.cy - row_y) < 10)
    p.words.remove(zero)
    note = rebuild(p)
    assert note.rows[0]["loose"].text == "0" and note.rows[0]["loose"].conf <= 0.5
    assert counts(note) == truth_counts(truth)


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
    page, truth = load("easy-3")
    note = rebuild(_rotate(page, deg))
    assert abs(note.skew_deg - deg) < 1.0
    assert counts(note) == truth_counts(truth)


def test_rows_that_drift_across_the_page_stay_whole():
    page, truth = load("easy-3")
    p = copy.deepcopy(page)
    for w in p.words:
        w.poly = [[x, y + (x / 1240) * (y / 1754) * 30] for x, y in w.poly]
    assert counts(rebuild(p)) == truth_counts(truth)


def test_box_merged_across_columns_is_split():
    page, truth = load("easy-3")
    p = copy.deepcopy(page)
    # Merge line 2's pieces-per-box "12" and boxes "25" into one OCR box.
    row_y = next(w for w in p.words if w.text == "300").cy
    a = next(w for w in p.words if w.text == "12" and abs(w.cy - row_y) < 10)
    b = next(w for w in p.words if w.text == "25" and abs(w.cy - row_y) < 10)
    merged = Word("12 25", min(a.conf, b.conf), [[a.x0, a.y0], [b.x1, a.y0], [b.x1, b.y1], [a.x0, b.y1]])
    p.words = [w for w in p.words if w not in (a, b)] + [merged]
    note = rebuild(p)
    assert note.rows[1]["ppb"].text == "12" and note.rows[1]["boxes"].text == "25"


def test_without_any_table_heading():
    page, truth = load("easy-3")
    p = copy.deepcopy(page)
    heading_y = next(w for w in p.words if w.text.lower().startswith("boxes")).cy
    p.words = [w for w in p.words if abs(w.cy - heading_y) > 25]
    note = rebuild(p)
    assert counts(note) == truth_counts(truth)


def test_note_round_trips_through_json():
    page, _ = load("medium-24")
    note = rebuild(page)
    again = Note.from_dict(json.loads(json.dumps(note.to_dict())))
    assert [f.to_dict() for f in run_checks(again)] == [f.to_dict() for f in run_checks(note)]
    assert isinstance(again.rows[0]["total"], Cell)
