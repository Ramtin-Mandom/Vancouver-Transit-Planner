"""CSV validation and PostgreSQL-compatible GTFS value conversion."""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

ValueType = Literal["text", "integer", "decimal", "boolean", "date", "interval"]


class DataValidationError(ValueError):
    """A source file cannot be mapped safely to the database schema."""


@dataclass(frozen=True)
class Column:
    """A target column and its conversion rules."""

    name: str
    value_type: ValueType = "text"
    nullable: bool = True
    source_name: str | None = None

    @property
    def source(self) -> str:
        return self.source_name or self.name


def parse_value(value: str, value_type: ValueType, *, nullable: bool = True) -> Any:
    """Convert one stripped GTFS field to a value psycopg can adapt."""
    value = value.strip()
    if value == "":
        if nullable:
            return None
        raise ValueError("value may not be empty")
    if value_type == "text":
        return value
    if value_type == "integer":
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("expected an integer") from exc
    if value_type == "decimal":
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("expected a decimal number") from exc
    if value_type == "boolean":
        if value not in {"0", "1"}:
            raise ValueError("expected GTFS boolean 0 or 1")
        return value == "1"
    if value_type == "date":
        if len(value) != 8 or not value.isdigit():
            raise ValueError("expected a valid GTFS date in YYYYMMDD format")
        try:
            return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
        except ValueError as exc:
            raise ValueError("expected a valid GTFS date in YYYYMMDD format") from exc
    if value_type == "interval":
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError("expected a GTFS time in H+:MM:SS format")
        try:
            hours, minutes, seconds = (int(part) for part in parts)
        except ValueError as exc:
            raise ValueError("expected a GTFS time in H+:MM:SS format") from exc
        if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
            raise ValueError("expected non-negative hours and minutes/seconds below 60")
        return timedelta(hours=hours, minutes=minutes, seconds=seconds)
    raise AssertionError(f"Unsupported value type: {value_type}")


def validate_header(
    filename: str,
    actual: Sequence[str],
    columns: Sequence[Column],
    *,
    implicit_headers: Sequence[str] = (),
) -> list[str]:
    """Validate a CSV header and return the effective source header."""
    expected = [column.source for column in columns]
    effective = list(actual) + list(implicit_headers)
    missing = [
        column.source
        for column in columns
        if column.source not in effective and not column.nullable
    ]
    extra = [name for name in effective if name not in expected]
    duplicates = sorted({name for name in effective if effective.count(name) > 1})
    if missing or extra or duplicates:
        details = []
        if missing:
            details.append(f"missing columns: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected columns: {', '.join(extra)}")
        if duplicates:
            details.append(f"duplicate columns: {', '.join(duplicates)}")
        raise DataValidationError(f"{filename}: header mismatch ({'; '.join(details)})")
    return effective


def active_columns(
    path: Path,
    columns: Sequence[Column],
    *,
    implicit_headers: Sequence[str] = (),
) -> tuple[Column, ...]:
    """Return target columns represented by a validated source header."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise DataValidationError(f"{path.name}: file is empty") from exc
    header = validate_header(
        path.name, raw_header, columns, implicit_headers=implicit_headers
    )
    return tuple(column for column in columns if column.source in header)


def iter_converted_rows(
    path: Path,
    columns: Sequence[Column],
    *,
    implicit_headers: Sequence[str] = (),
) -> Iterator[tuple[Any, ...]]:
    """Yield validated rows with detailed location-aware conversion errors."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise DataValidationError(f"{path.name}: file is empty") from exc
        header = validate_header(
            path.name, raw_header, columns, implicit_headers=implicit_headers
        )
        selected_columns = tuple(
            column for column in columns if column.source in header
        )
        indexes: Mapping[str, int] = {name: index for index, name in enumerate(header)}
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise DataValidationError(
                    f"{path.name}: row {row_number}: expected {len(header)} fields "
                    f"from the effective header, found {len(row)}"
                )
            converted: list[Any] = []
            for column in selected_columns:
                raw = row[indexes[column.source]]
                try:
                    converted.append(
                        parse_value(raw, column.value_type, nullable=column.nullable)
                    )
                except ValueError as exc:
                    raise DataValidationError(
                        f"{path.name}: row {row_number}, column {column.source!r}, "
                        f"value {raw!r}, expected {column.value_type}: {exc}"
                    ) from exc
            yield tuple(converted)
