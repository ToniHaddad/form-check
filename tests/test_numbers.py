from decimal import Decimal

import pytest

from formcheck.numbers import COMMA, DOT, format_number, infer_style, looks_numeric, parse_number, style_hint


@pytest.mark.parametrize("text,style,expected", [
    ("1,250.50", DOT, "1250.50"),
    ("1.250,50", COMMA, "1250.50"),
    ("12", DOT, "12"),
    ("1,200", DOT, "1200"),
    ("1.200", COMMA, "1200"),
    ("12,345,678.90", DOT, "12345678.90"),
    ("0,55", COMMA, "0.55"),
    ("25.20", DOT, "25.20"),
    ("USD 1,250.00", DOT, "1250.00"),
    ("30 240,00 kg", COMMA, "30240.00"),
    ("1O5", DOT, "105"),      # letter O read for zero
    ("l2.50", DOT, "12.50"),  # lower-case L read for one
])
def test_parse(text, style, expected):
    assert parse_number(text, style) == Decimal(expected)


@pytest.mark.parametrize("text,style", [
    ("Rice", DOT),
    ("", DOT),
    ("1,25.00", DOT),     # thousands group of two digits: a misread, not a number
    ("1.2.3,00", COMMA),  # thousands groups of one digit
    ("1,250,50", COMMA),  # two decimal commas
    ("12.50.1", DOT),
])
def test_parse_rejects(text, style):
    assert parse_number(text, style) is None


def test_same_text_reads_differently_by_style():
    # The whole point of a page-level style: "1,250" is 1250 or 1.25.
    assert parse_number("1,250", DOT) == Decimal("1250")
    assert parse_number("1,250", COMMA) == Decimal("1.250")


def test_style_hints():
    assert style_hint("1,250.50") == DOT
    assert style_hint("1.250,50") == COMMA
    assert style_hint("12,50") == COMMA
    assert style_hint("1,250") is None
    assert style_hint("120") is None


def test_infer_style_votes_over_page():
    assert infer_style(["120", "1,200", "12,50", "1.500,00"]) == COMMA
    assert infer_style(["120", "1,200", "12.50"]) == DOT
    assert infer_style(["120", "1,200"]) == DOT  # nothing decides: default


def test_format_round_trip():
    for style in (DOT, COMMA):
        for v in (Decimal("0.55"), Decimal("1250.5"), Decimal("1234567.89")):
            assert parse_number(format_number(v, style), style) == v.quantize(Decimal("0.01"))
    assert format_number(Decimal("1200"), COMMA, 0) == "1.200"


def test_looks_numeric():
    assert looks_numeric("1,250.00")
    assert not looks_numeric("(USD)")
    assert not looks_numeric("Rice")


def test_counts():
    from formcheck.numbers import format_count, parse_count
    assert parse_count("1,200") == 1200
    assert parse_count("1.200") == 1200
    assert parse_count("1 200") == 1200
    assert parse_count("O") == 0  # a lone letter O in the loose column
    assert parse_count("1,20") is None
    assert parse_count("12a") is None
    assert format_count(12345, ".") == "12.345"
    assert format_count(12345, "") == "12345"
