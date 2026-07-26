"""Transactional PostgreSQL bulk loader for the extracted transit feed."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection, connect, sql

from .config import DatabaseConfig
from .parsers import Column, DataValidationError, active_columns, iter_converted_rows

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableSpec:
    """Mapping between one source file and one existing transit table."""

    table: str
    filename: str
    columns: tuple[Column, ...]
    implicit_headers: tuple[str, ...] = ()


def c(name: str, kind: Any = "text", nullable: bool = True, source: str | None = None) -> Column:
    return Column(name, kind, nullable, source)


TABLES: tuple[TableSpec, ...] = (
    TableSpec("agency", "agency.txt", (
        c("agency_id", nullable=False), c("agency_name", nullable=False),
        c("agency_url", nullable=False), c("agency_timezone", nullable=False),
        c("agency_lang"), c("agency_phone"), c("agency_fare_url"),
    )),
    TableSpec("calendar", "calendar.txt", (
        c("service_id", nullable=False),
        *(c(day, "boolean", False) for day in
          ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")),
        c("start_date", "date", False), c("end_date", "date", False),
    )),
    TableSpec("calendar_dates", "calendar_dates.txt", (
        c("service_id", nullable=False), c("service_date", "date", False, "date"),
        c("exception_type", "integer", False),
    )),
    TableSpec("feed_info", "feed_info.txt", (
        c("feed_publisher_name", nullable=False), c("feed_publisher_url", nullable=False),
        c("feed_lang", nullable=False), c("feed_start_date", "date"),
        c("feed_end_date", "date"), c("feed_version"),
    )),
    TableSpec("routes", "routes.txt", (
        c("route_id", nullable=False), c("agency_id", nullable=False), c("route_short_name"),
        c("route_long_name"), c("route_desc"), c("route_type", "integer", False),
        c("route_url"), c("route_color"), c("route_text_color"),
    )),
    TableSpec("stops", "stops.txt", (
        c("stop_id", nullable=False), c("stop_code"), c("stop_name", nullable=False),
        c("stop_desc"), c("stop_lat", "decimal"), c("stop_lon", "decimal"), c("zone_id"),
        c("stop_url"), c("location_type", "integer"), c("parent_station"),
        c("wheelchair_boarding", "integer"),
    )),
    TableSpec("shapes", "shapes.txt", (
        c("shape_id", nullable=False), c("shape_pt_lat", "decimal", False),
        c("shape_pt_lon", "decimal", False), c("shape_pt_sequence", "integer", False),
        c("shape_dist_traveled", "decimal"),
    )),
    TableSpec("trips", "trips.txt", (
        c("route_id", nullable=False), c("service_id", nullable=False),
        c("trip_id", nullable=False), c("trip_headsign"), c("trip_short_name"),
        c("direction_id", "integer"), c("block_id"), c("shape_id"),
        c("wheelchair_accessible", "integer"), c("bikes_allowed", "integer"),
    )),
    TableSpec("stop_times", "stop_times.txt", (
        c("trip_id", nullable=False), c("arrival_time", "interval"),
        c("departure_time", "interval"), c("stop_id", nullable=False),
        c("stop_sequence", "integer", False), c("stop_headsign"),
        c("pickup_type", "integer"), c("drop_off_type", "integer"),
        c("shape_dist_traveled", "decimal"), c("timepoint", "integer"),
    )),
    TableSpec("transfers", "transfers.txt", (
        c("from_stop_id", nullable=False), c("to_stop_id", nullable=False),
        c("transfer_type", "integer", False), c("min_transfer_time", "integer"),
        c("from_trip_id"), c("to_trip_id"),
    )),
    TableSpec("translations", "translations.txt", (
        c("table_name", nullable=False), c("field_name", nullable=False),
        c("language", nullable=False), c("translation", nullable=False),
        c("record_id", nullable=False),
    )),
    TableSpec("signup_periods", "signup_periods.txt", (
        c("sign_id", nullable=False), c("from_date", "date", False),
        c("to_date", "date", False),
    )),
    TableSpec("route_names_exceptions", "route_names_exceptions.txt", (
        c("route_id", nullable=False), c("route_name", nullable=False),
        c("route_do"), c("name_type"),
    )),
    TableSpec("direction_names_exceptions", "direction_names_exceptions.txt", (
        c("route_name", nullable=False), c("direction_id", "integer", False),
        c("direction_name", nullable=False), c("direction_do", "integer", False),
    )),
    TableSpec("directions", "directions.txt", (
        c("direction", nullable=False), c("direction_id", "integer", False),
        c("route_id", nullable=False), c("route_short_name"), c("route_do"),
    ), ("route_do",)),
    TableSpec("stop_order_exceptions", "stop_order_exceptions.txt", (
        c("route_name", nullable=False), c("direction_name", nullable=False),
        c("direction_do", "integer", False), c("stop_id", nullable=False),
        c("stop_name", nullable=False), c("stop_do", "integer", False),
    )),
)


@dataclass
class LoadSummary:
    """Outcome and row counts for an import attempt."""

    successful: dict[str, int]
    skipped: list[str]
    failed: dict[str, str]


class TransitLoader:
    """Validate and transactionally load all schema-backed extracted files."""

    def __init__(self, data_dir: Path, config: DatabaseConfig | None = None) -> None:
        self.data_dir = data_dir
        self.config = config

    def _validate_paths(self) -> None:
        if not self.data_dir.is_dir():
            raise DataValidationError(f"Data directory does not exist: {self.data_dir}")
        missing = [spec.filename for spec in TABLES if not (self.data_dir / spec.filename).is_file()]
        if missing:
            raise DataValidationError("Missing required source files: " + ", ".join(missing))

    def dry_run(self) -> LoadSummary:
        """Validate every source file and conversion without opening PostgreSQL."""
        self._validate_paths()
        counts: dict[str, int] = {}
        for spec in TABLES:
            LOGGER.info("Validating %s from %s", spec.table, spec.filename)
            counts[spec.table] = sum(
                1 for _ in iter_converted_rows(
                    self.data_dir / spec.filename,
                    spec.columns,
                    implicit_headers=spec.implicit_headers,
                )
            )
        return LoadSummary(counts, [], {})

    def load(self, *, replace: bool = False) -> LoadSummary:
        """Load every table in one transaction; rollback on any failure."""
        self._validate_paths()
        if self.config is None:
            raise ValueError("Database configuration is required for an import")
        successful: dict[str, int] = {}
        with connect(**self.config.connection_kwargs()) as connection:
            with connection.transaction():
                self._ensure_schema_tables(connection)
                if replace:
                    self._truncate(connection)
                else:
                    populated = self._populated_tables(connection)
                    if populated:
                        raise RuntimeError(
                            "Import refused because target tables already contain data: "
                            + ", ".join(populated)
                            + ". Re-run with --replace only if replacement is intended."
                        )
                connection.execute("SET CONSTRAINTS ALL DEFERRED")
                for spec in TABLES:
                    LOGGER.info("Loading %s from %s", spec.table, spec.filename)
                    successful[spec.table] = self._copy_table(connection, spec)
        return LoadSummary(successful, [], {})

    @staticmethod
    def _ensure_schema_tables(connection: Connection[Any]) -> None:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'transit'"
        ).fetchall()
        existing = {row[0] for row in rows}
        missing = [spec.table for spec in TABLES if spec.table not in existing]
        if missing:
            raise RuntimeError(
                "Required transit tables do not exist: " + ", ".join(missing)
                + ". Apply database/schema.sql manually before importing."
            )

    @staticmethod
    def _populated_tables(connection: Connection[Any]) -> list[str]:
        populated = []
        for spec in TABLES:
            query = sql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{} LIMIT 1)").format(
                sql.Identifier("transit"), sql.Identifier(spec.table)
            )
            if connection.execute(query).fetchone()[0]:
                populated.append(spec.table)
        return populated

    @staticmethod
    def _truncate(connection: Connection[Any]) -> None:
        targets = sql.SQL(", ").join(
            sql.Identifier("transit", spec.table) for spec in TABLES
        )
        connection.execute(
            sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(targets)
        )

    def _copy_table(self, connection: Connection[Any], spec: TableSpec) -> int:
        selected_columns = active_columns(
            self.data_dir / spec.filename,
            spec.columns,
            implicit_headers=spec.implicit_headers,
        )
        columns = sql.SQL(", ").join(
            sql.Identifier(column.name) for column in selected_columns
        )
        statement = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
            sql.Identifier("transit"), sql.Identifier(spec.table), columns
        )
        count = 0
        with connection.cursor() as cursor:
            with cursor.copy(statement) as copy:
                for row in iter_converted_rows(
                    self.data_dir / spec.filename,
                    spec.columns,
                    implicit_headers=spec.implicit_headers,
                ):
                    copy.write_row(row)
                    count += 1
        return count
