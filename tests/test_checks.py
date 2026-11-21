from formcheck.checks import normalise_code, parse_date, run_checks, summary
from formcheck.table import Cell, Note


def box(x, y):
    return [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]


def note(rows, lines, boxes, pieces, doc_date="10/03/2026"):
    """rows: (code, ppb, boxes, loose, total, expiry)"""
    built = []
    for i, (code, ppb, bx, lo, tot, exp) in enumerate(rows):
        y = 100 + 40 * i
        r = {
            "no": Cell(str(i + 1), 0.99, box(10, y)),
            "code": Cell(code, 0.99, box(60, y)),
            "item": Cell("Canned cheese 200 g", 0.99, box(150, y)),
            "ppb": Cell(ppb, 0.99, box(400, y)),
            "boxes": Cell(bx, 0.99, box(480, y)),
            "loose": Cell(lo, 0.99, box(560, y)),
            "total": Cell(tot, 0.99, box(640, y)),
        }
        if exp:
            r["expiry"] = Cell(exp, 0.99, box(720, y))
        built.append(r)
    return Note(doc_type="waybill", header={"date": Cell(doc_date, 0.99, box(200, 20))}, rows=built,
                totals={"lines": Cell(lines, 0.99, box(100, 500)), "boxes": Cell(boxes, 0.99, box(480, 500)),
                        "pieces": Cell(pieces, 0.99, box(640, 540))})


CLEAN = [("FOO-CHE-004", "24", "12", "6", "294", "17/01/2028"),
         ("NFI-BLK-001", "10", "40", "0", "400", "-")]


def test_clean_document_passes():
    flags = run_checks(note(CLEAN, "2", "52", "694"))
    assert flags == []
    assert summary(flags)["ok"]


def test_line_error_message_and_place():
    rows = [("FOO-CHE-004", "24", "12", "6", "249", "17/01/2028"), CLEAN[1]]
    flags = run_checks(note(rows, "2", "52", "649"))
    assert [(f.kind, f.row) for f in flags] == [("line", 1)]
    assert flags[0].message == "Line 1: 12 boxes × 24 + 6 = 294 pieces, the note says 249."
    assert "two digits swapped" in flags[0].hint
    assert "row1.total" in flags[0].cells and len(flags[0].poly) >= 4


def test_one_digit_hint_for_boxes():
    rows = [("FOO-CHE-004", "24", "17", "6", "294", "17/01/2028"), CLEAN[1]]
    flags = run_checks(note(rows, "2", "57", "694"))
    assert flags[0].kind == "line"
    assert "the boxes may be 12" in flags[0].hint


def test_loose_pieces_that_make_a_full_box():
    # 11 boxes + 30 pieces of a 24-piece box adds up (294) but should read 12 boxes + 6.
    rows = [("FOO-CHE-004", "24", "11", "30", "294", "17/01/2028"), CLEAN[1]]
    flags = run_checks(note(rows, "2", "51", "694"))
    assert [(f.kind, f.row) for f in flags] == [("loose", 1)]
    assert "It should read 12 boxes + 6" in flags[0].message


def test_expired_on_the_document_date():
    rows = [("FOO-CHE-004", "24", "12", "6", "294", "02/02/2026"), CLEAN[1]]
    flags = run_checks(note(rows, "2", "52", "694", doc_date="10/03/2026"))
    assert [(f.kind, f.row) for f in flags] == [("expired", 1)]
    assert "02/02/2026 is already past on the document date 10/03/2026" in flags[0].message


def test_bad_item_codes():
    for bad in ("FOO-CHE-04", "FOOCHE-004", "FO-CHE-004", "FOO-CHE-0041", "XYZ-CHE-004"):
        rows = [(bad, "24", "12", "6", "294", "17/01/2028"), CLEAN[1]]
        flags = run_checks(note(rows, "2", "52", "694"))
        assert [(f.kind, f.row) for f in flags] == [("code", 1)], bad


def test_code_lookalikes_are_read_by_position():
    assert normalise_code("F0O-CHE-0O4") == "FOO-CHE-004"
    assert normalise_code("SHE-TAR.001") == "SHE-TAR-001"
    assert normalise_code("hyg-soa-001") == "HYG-SOA-001"
    assert normalise_code("FOO-CHE-04") == "FOO-CHE-04"


def test_totals():
    flags = run_checks(note(CLEAN, "3", "50", "394"))
    kinds = [f.kind for f in flags]
    assert kinds == ["lines_count", "boxes_sum", "pieces_sum"]
    assert flags[2].message == "The lines add up to 694 pieces, the note says 394 (-300)."
    assert "line 1" not in flags[2].hint


def test_left_out_line_hint():
    flags = run_checks(note(CLEAN, "2", "52", "294"))
    assert [f.kind for f in flags] == ["pieces_sum"]
    assert "line 2" in flags[0].hint


def test_thousands_separators():
    rows = [("MED-ORS-002", "100", "53", "0", "5.300", "03/10/2027"), ("HYG-SOA-001", "72", "15", "0", "1,080", "-")]
    assert run_checks(note(rows, "2", "68", "6 380")) == []


def test_unreadable_cell_is_flagged_not_passed():
    rows = [("FOO-CHE-004", "24", "1#", "6", "294", "17/01/2028"), CLEAN[1]]
    flags = run_checks(note(rows, "2", "52", "694"))
    assert [f.kind for f in flags] == ["unreadable"]  # sums are not checked while a line is unreadable
    assert flags[0].cells == ["row1.boxes"]
    assert not summary(flags)["ok"]


def test_unreadable_document_date_skips_expiry_with_a_flag():
    flags = run_checks(note(CLEAN, "2", "52", "694", doc_date="1?/03/2026"))
    assert [f.kind for f in flags] == ["unreadable"]
    assert "expiry dates were not checked" in flags[0].message


def test_parse_date():
    assert str(parse_date("17/01/2028")) == "2028-01-17"
    assert str(parse_date("l7/0l/2O28")) == "2028-01-17"
    assert parse_date("31/02/2026") is None
    assert parse_date("-") is None
