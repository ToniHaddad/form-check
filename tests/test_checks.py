from formcheck.checks import run_checks, summary
from formcheck.table import Cell, Note


def box(x, y):
    return [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]


def note(rows, total_qty, grand_total, style="dot"):
    built = []
    for i, (q, u, t) in enumerate(rows):
        y = 100 + 40 * i
        built.append({
            "no": Cell(str(i + 1), 0.99, box(10, y)),
            "item": Cell("Rice, 25 kg bag", 0.99, box(60, y)),
            "qty": Cell(q, 0.99, box(300, y)),
            "unit_value": Cell(u, 0.99, box(400, y)),
            "line_total": Cell(t, 0.99, box(500, y)),
        })
    return Note(style=style, header={}, rows=built,
                total_qty=Cell(total_qty, 0.99, box(300, 500)),
                grand_total=Cell(grand_total, 0.99, box(500, 540)))


def test_clean_note_passes():
    n = note([("12", "25.00", "300.00"), ("1,200", "2.50", "3,000.00")], "1,212", "3,300.00")
    flags = run_checks(n)
    assert flags == []
    assert summary(flags)["ok"]


def test_line_error_message_and_place():
    n = note([("12", "25.00", "380.00"), ("4", "10.00", "40.00")], "16", "420.00")
    flags = run_checks(n)
    assert len(flags) == 1
    f = flags[0]
    assert f.kind == "line" and f.row == 1
    assert f.message == "Line 1: 12 × 25.00 = 300.00, the note says 380.00."
    assert "row1.line_total" in f.cells
    assert f.poly and len(f.poly) >= 4  # outline around the three cells on the photo


def test_grand_total_error_and_skipped_line_hint():
    n = note([("12", "25.00", "300.00"), ("4", "10.00", "40.00"), ("2", "5.00", "10.00")], "18", "340.00")
    flags = run_checks(n)
    assert [f.kind for f in flags] == ["total_sum"]
    assert "add up to 350.00, the note says 340.00" in flags[0].message
    assert "line 3" in flags[0].hint


def test_single_digit_hint_for_quantity():
    # 15 x 20.00 = 300.00 was written, but the quantity reads 18: one digit away.
    n = note([("18", "20.00", "300.00")], "18", "300.00")
    flags = run_checks(n)
    assert flags[0].kind == "line"
    assert "quantity may be 15" in flags[0].hint


def test_decimal_comma_note():
    n = note([("1.200", "2,50", "3.000,00"), ("12", "0,55", "6,60")], "1.212", "3.006,60", style="comma")
    assert run_checks(n) == []
    bad = note([("1.200", "2,50", "3.000,00"), ("12", "0,55", "6,60")], "1.212", "3.060,60", style="comma")
    flags = run_checks(bad)
    assert flags[0].kind == "total_sum"
    assert "3.006,60" in flags[0].message and "3.060,60" in flags[0].message


def test_unreadable_cell_is_flagged_not_passed():
    n = note([("12", "25.00", "3#0.00"), ("4", "10.00", "40.00")], "16", "340.00")
    flags = run_checks(n)
    kinds = [f.kind for f in flags]
    assert kinds == ["unreadable"]  # the sum is not checked while a line is unreadable
    assert flags[0].cells == ["row1.line_total"]
    assert not summary(flags)["ok"]


def test_quantity_total_error():
    n = note([("12", "25.00", "300.00"), ("4", "10.00", "40.00")], "61", "340.00")
    flags = run_checks(n)
    assert [f.kind for f in flags] == ["qty_sum"]
    assert flags[0].message.startswith("The quantities add up to 16, the note says 61")


def test_rounding_to_the_cent_is_not_an_error():
    # 3 x 0.333 is not on a note, but 7 x 1.15 = 8.05 exactly; 3 x 33.33 = 99.99
    n = note([("7", "1.15", "8.05"), ("3", "33.33", "99.99")], "10", "108.04")
    assert run_checks(n) == []
