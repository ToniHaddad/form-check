"""The one test that runs the real OCR model: photo in, flags out."""

from decimal import Decimal

from formcheck.checks import run_checks
from formcheck.generator import make_form, photograph, render
from formcheck.numbers import parse_number
from formcheck.ocr import ocr_photo
from formcheck.table import rebuild


def test_photo_to_flag():
    form = make_form(24, "easy", force_error="line_total", n_rows=4)
    page, _ = render(form)
    photo, _ = photograph(page, "easy", 24)

    ocr = ocr_photo(photo)
    assert ocr.page_corners is not None, "the sheet should be found on the desk"
    note = rebuild(ocr)
    assert len(note.rows) == 4
    assert note.header["waybill"].text == form.header["waybill"]
    for got, want in zip(note.rows, form.rows):
        assert parse_number(got["line_total"].text, note.style) == want.line_total

    flags = run_checks(note)
    errors = [f for f in flags if f.severity == "error"]
    assert len(errors) == 1
    assert errors[0].kind == "line" and errors[0].row == form.error["row"]
    assert Decimal(errors[0].extra["written"]) == Decimal(form.error["written"])
    # The outline sits on the photo, inside the image bounds.
    w, h = ocr.photo_size
    assert all(0 <= x <= w and 0 <= y <= h for x, y in errors[0].poly)
