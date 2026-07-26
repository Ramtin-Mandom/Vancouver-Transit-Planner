from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.data_ingestion.parsers import (
    Column,
    DataValidationError,
    iter_converted_rows,
    parse_value,
    validate_header,
)


@pytest.mark.parametrize(
    ("raw", "kind", "expected"),
    [
        ("", "text", None),
        ("  hello  ", "text", "hello"),
        ("42", "integer", 42),
        ("49.123456", "decimal", Decimal("49.123456")),
        ("1", "boolean", True),
        ("0", "boolean", False),
        ("20260701", "date", date(2026, 7, 1)),
        ("25:15:09", "interval", timedelta(hours=25, minutes=15, seconds=9)),
        (" 5:04:00", "interval", timedelta(hours=5, minutes=4)),
    ],
)
def test_parse_value(raw, kind, expected):
    assert parse_value(raw, kind) == expected


@pytest.mark.parametrize(
    ("raw", "kind"),
    [("yes", "boolean"), ("20260230", "date"), ("24:60:00", "interval"), ("x", "integer")],
)
def test_invalid_values(raw, kind):
    with pytest.raises(ValueError):
        parse_value(raw, kind)


def test_required_empty_value():
    with pytest.raises(ValueError, match="may not be empty"):
        parse_value("", "text", nullable=False)


def test_header_mismatch_reports_missing_and_extra():
    with pytest.raises(DataValidationError, match=r"missing columns: b.*unexpected columns: c"):
        validate_header(
            "sample.txt", ["a", "c"], [Column("a"), Column("b", nullable=False)]
        )


def test_implicit_header_supports_known_extra_field(tmp_path):
    path = tmp_path / "directions.txt"
    path.write_text("direction,direction_id\nNORTH,0,last\n", encoding="utf-8")
    rows = list(
        iter_converted_rows(
            path,
            (Column("direction"), Column("direction_id", "integer"), Column("route_do")),
            implicit_headers=("route_do",),
        )
    )
    assert rows == [("NORTH", 0, "last")]


def test_malformed_row_reports_filename_and_row(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("a,b\n1\n", encoding="utf-8")
    with pytest.raises(DataValidationError, match=r"sample.txt: row 2: expected 2 fields"):
        list(iter_converted_rows(path, (Column("a"), Column("b"))))


def test_conversion_error_has_full_context(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("number\nnope\n", encoding="utf-8")
    with pytest.raises(
        DataValidationError,
        match=r"sample.txt: row 2, column 'number', value 'nope', expected integer",
    ):
        list(iter_converted_rows(path, (Column("number", "integer"),)))


def test_optional_column_may_be_absent(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("required\nvalue\n", encoding="utf-8")
    rows = list(
        iter_converted_rows(
            path,
            (Column("required", nullable=False), Column("optional")),
        )
    )
    assert rows == [("value",)]
