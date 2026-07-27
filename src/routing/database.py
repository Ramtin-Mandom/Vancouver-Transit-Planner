"""Read-only PostgreSQL access used by scheduled routing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from src.data_ingestion.config import DatabaseConfig

from .models import Connection, Stop


def _route_name(row: dict[str, Any]) -> str:
    return row["route_short_name"] or row["route_long_name"] or row["route_id"]


def _stop(row: dict[str, Any]) -> Stop:
    return Stop(
        stop_id=row["stop_id"],
        stop_name=row["stop_name"],
        stop_code=row.get("stop_code"),
        stop_lat=row.get("stop_lat"),
        stop_lon=row.get("stop_lon"),
    )


def _connection(row: dict[str, Any]) -> Connection:
    # psycopg maps PostgreSQL INTERVAL to timedelta. Keeping that type is
    # essential because datetime.time cannot represent GTFS service-day times
    # such as 25:10:00.
    return Connection(
        trip_id=row["trip_id"],
        service_id=row["service_id"],
        route_id=row["route_id"],
        route_name=_route_name(row),
        from_stop_id=row["from_stop_id"],
        to_stop_id=row["to_stop_id"],
        departure_time=row["departure_time"],
        arrival_time=row["arrival_time"],
        from_stop_sequence=row["from_stop_sequence"],
        to_stop_sequence=row["to_stop_sequence"],
    )


class TransitDatabase:
    """Small read-only repository over the existing ``transit`` schema."""

    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or DatabaseConfig.from_environment()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(
            **self.config.connection_kwargs(),
            row_factory=dict_row,
            options="-c default_transaction_read_only=on",
        ) as connection:
            yield connection

    def find_stop(self, stop_id: str) -> Stop | None:
        query = """
            SELECT stop_id, stop_name, stop_code, stop_lat, stop_lon
            FROM transit.stops
            WHERE stop_id = %s
        """
        with self._connection() as connection:
            row = connection.execute(query, (stop_id,)).fetchone()
        return _stop(row) if row else None

    def search_stops(self, stop_name: str, limit: int = 20) -> list[Stop]:
        query = """
            SELECT stop_id, stop_name, stop_code, stop_lat, stop_lon
            FROM transit.stops
            WHERE stop_name ILIKE %s
            ORDER BY stop_name, stop_id
            LIMIT %s
        """
        with self._connection() as connection:
            rows = connection.execute(
                query, (f"%{stop_name}%", max(1, limit))
            ).fetchall()
        return [_stop(row) for row in rows]

    def departures_from(
        self, stop_id: str, earliest_time: timedelta
    ) -> list[Connection]:
        """Return boardable one-stop hops, not the complete stop_times table."""
        query = """
            SELECT
                t.trip_id, t.service_id, t.route_id,
                r.route_short_name, r.route_long_name,
                current.stop_id AS from_stop_id,
                following.stop_id AS to_stop_id,
                current.departure_time,
                following.arrival_time,
                current.stop_sequence AS from_stop_sequence,
                following.stop_sequence AS to_stop_sequence
            FROM transit.stop_times AS current
            JOIN transit.trips AS t ON t.trip_id = current.trip_id
            JOIN transit.routes AS r ON r.route_id = t.route_id
            JOIN LATERAL (
                SELECT stop_id, arrival_time, stop_sequence
                FROM transit.stop_times
                WHERE trip_id = current.trip_id
                  AND stop_sequence > current.stop_sequence
                ORDER BY stop_sequence
                LIMIT 1
            ) AS following ON TRUE
            WHERE current.stop_id = %s
              AND current.departure_time >= %s
              AND current.departure_time IS NOT NULL
              AND following.arrival_time IS NOT NULL
              AND COALESCE(current.pickup_type, 0) <> 1
              AND COALESCE(following.drop_off_type, 0) <> 1
            ORDER BY current.departure_time
        """
        with self._connection() as connection:
            rows = connection.execute(query, (stop_id, earliest_time)).fetchall()
        return [_connection(row) for row in rows]

    def trip_connections(
        self, trip_id: str, from_stop_sequence: int
    ) -> list[Connection]:
        """Return only consecutive hops at/after a position on one trip."""
        query = """
            SELECT
                t.trip_id, t.service_id, t.route_id,
                r.route_short_name, r.route_long_name,
                current.stop_id AS from_stop_id,
                following.stop_id AS to_stop_id,
                current.departure_time,
                following.arrival_time,
                current.stop_sequence AS from_stop_sequence,
                following.stop_sequence AS to_stop_sequence
            FROM transit.stop_times AS current
            JOIN transit.stop_times AS following
              ON following.trip_id = current.trip_id
             AND following.stop_sequence = (
                 SELECT MIN(next_time.stop_sequence)
                 FROM transit.stop_times AS next_time
                 WHERE next_time.trip_id = current.trip_id
                   AND next_time.stop_sequence > current.stop_sequence
             )
            JOIN transit.trips AS t ON t.trip_id = current.trip_id
            JOIN transit.routes AS r ON r.route_id = t.route_id
            WHERE current.trip_id = %s
              AND current.stop_sequence >= %s
              AND current.departure_time IS NOT NULL
              AND following.arrival_time IS NOT NULL
            ORDER BY current.stop_sequence
        """
        with self._connection() as connection:
            rows = connection.execute(
                query, (trip_id, from_stop_sequence)
            ).fetchall()
        return [_connection(row) for row in rows]

    def transfers_from(self, stop_id: str) -> list[dict[str, Any]]:
        query = """
            SELECT to_stop_id, transfer_type, min_transfer_time,
                   from_trip_id, to_trip_id
            FROM transit.transfers
            WHERE from_stop_id = %s
            ORDER BY to_stop_id
        """
        with self._connection() as connection:
            return list(connection.execute(query, (stop_id,)).fetchall())

    def calendar_rule(self, service_id: str) -> dict[str, Any] | None:
        query = """
            SELECT service_id, monday, tuesday, wednesday, thursday,
                   friday, saturday, sunday, start_date, end_date
            FROM transit.calendar
            WHERE service_id = %s
        """
        with self._connection() as connection:
            return connection.execute(query, (service_id,)).fetchone()

    def calendar_exception(
        self, service_id: str, service_date: date
    ) -> int | None:
        query = """
            SELECT exception_type
            FROM transit.calendar_dates
            WHERE service_id = %s AND service_date = %s
        """
        with self._connection() as connection:
            row = connection.execute(query, (service_id, service_date)).fetchone()
        return row["exception_type"] if row else None
