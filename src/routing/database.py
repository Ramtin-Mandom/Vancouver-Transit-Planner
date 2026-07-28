"""Read-only PostgreSQL access used by scheduled routing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from src.data_ingestion.config import DatabaseConfig

from .models import Connection, RouteLeg, Stop


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
        direction_id=row.get("direction_id"),
    )


class TransitDatabase:
    """Small read-only repository over the existing ``transit`` schema."""

    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or DatabaseConfig.from_environment()
        self._session: psycopg.Connection[dict[str, Any]] | None = None

    def initialize(self) -> None:
        """Open the reusable read-only session eagerly."""
        if self._session is None or self._session.closed:
            self._session = psycopg.connect(
                **self.config.connection_kwargs(),
                row_factory=dict_row,
                autocommit=True,
                options="-c default_transaction_read_only=on",
            )

    def close(self) -> None:
        if self._session is not None and not self._session.closed:
            self._session.close()
        self._session = None

    def set_statement_timeout(self, milliseconds: int) -> None:
        """Bound each PostgreSQL statement for a reliable-search request."""
        self.initialize()
        if self._session is not None:
            self._session.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (str(max(1, milliseconds)),),
            )

    def __enter__(self) -> "TransitDatabase":
        self.initialize()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        self.initialize()
        if self._session is None:  # pragma: no cover - initialize guarantees it
            raise RuntimeError("PostgreSQL session initialization failed")
        yield self._session

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
        self,
        stop_id: str,
        earliest_time: timedelta,
        *,
        limit: int = 64,
        offset: int = 0,
        service_ids: set[str] | None = None,
    ) -> list[Connection]:
        """Return one deterministic departure batch, not an entire service day."""
        if service_ids is not None and not service_ids:
            return []
        query = """
            SELECT
                t.trip_id, t.service_id, t.route_id, t.direction_id,
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
                SELECT stop_id, arrival_time, stop_sequence, drop_off_type
                FROM transit.stop_times
                WHERE trip_id = current.trip_id
                  AND stop_sequence > current.stop_sequence
                ORDER BY stop_sequence
                LIMIT 1
            ) AS following ON TRUE
            WHERE current.stop_id = %s
              AND current.departure_time >= %s
              AND (%s::text[] IS NULL OR t.service_id = ANY(%s::text[]))
              AND current.departure_time IS NOT NULL
              AND following.arrival_time IS NOT NULL
              AND COALESCE(current.pickup_type, 0) <> 1
              AND COALESCE(following.drop_off_type, 0) <> 1
            ORDER BY current.departure_time, current.trip_id,
                     current.stop_sequence
            LIMIT %s OFFSET %s
        """
        service_filter = (
            sorted(service_ids) if service_ids is not None else None
        )
        with self._connection() as connection:
            rows = connection.execute(
                query,
                (
                    stop_id,
                    earliest_time,
                    service_filter,
                    service_filter,
                    max(1, limit),
                    max(0, offset),
                ),
            ).fetchall()
        return [_connection(row) for row in rows]

    def departures_in_window(
        self,
        stop_id: str,
        earliest_time: timedelta,
        latest_time: timedelta,
        *,
        service_ids: set[str] | None = None,
    ) -> list[Connection]:
        """Fetch one stop's bounded departures without OFFSET pagination."""
        if service_ids is not None and not service_ids:
            return []
        query = """
            SELECT t.trip_id, t.service_id, t.route_id, t.direction_id,
                   r.route_short_name, r.route_long_name,
                   current.stop_id AS from_stop_id,
                   following.stop_id AS to_stop_id,
                   current.departure_time, following.arrival_time,
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
                ORDER BY stop_sequence LIMIT 1
            ) AS following ON TRUE
            WHERE current.stop_id = %s
              AND current.departure_time BETWEEN %s AND %s
              AND (%s::text[] IS NULL OR t.service_id = ANY(%s::text[]))
              AND COALESCE(current.pickup_type, 0) <> 1
              AND current.departure_time IS NOT NULL
              AND following.arrival_time IS NOT NULL
            ORDER BY current.departure_time, current.trip_id,
                     current.stop_sequence
        """
        services = sorted(service_ids) if service_ids is not None else None
        with self._connection() as connection:
            rows = connection.execute(
                query,
                (stop_id, earliest_time, latest_time, services, services),
            ).fetchall()
        return [_connection(row) for row in rows]

    def trip_connections(
        self, trip_id: str, from_stop_sequence: int
    ) -> list[Connection]:
        """Return only consecutive hops at/after a position on one trip."""
        query = """
            SELECT
                t.trip_id, t.service_id, t.route_id, t.direction_id,
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

    def active_service_ids(self, service_date: date) -> set[str]:
        """Return all services operating on one date in a single query."""
        query = """
            SELECT c.service_id
            FROM transit.calendar AS c
            LEFT JOIN transit.calendar_dates AS exception
              ON exception.service_id = c.service_id
             AND exception.service_date = %s
            WHERE exception.exception_type = 1
               OR (
                    exception.exception_type IS DISTINCT FROM 2
                    AND %s BETWEEN c.start_date AND c.end_date
                    AND CASE EXTRACT(ISODOW FROM %s::date)
                        WHEN 1 THEN c.monday
                        WHEN 2 THEN c.tuesday
                        WHEN 3 THEN c.wednesday
                        WHEN 4 THEN c.thursday
                        WHEN 5 THEN c.friday
                        WHEN 6 THEN c.saturday
                        WHEN 7 THEN c.sunday
                    END
               )
            ORDER BY c.service_id
        """
        with self._connection() as connection:
            rows = connection.execute(
                query, (service_date, service_date, service_date)
            ).fetchall()
        return {row["service_id"] for row in rows}

    def integration_case_rows(self, limit: int) -> list[dict[str, Any]]:
        """Select deterministic real journeys and one valid service date each."""
        query = """
            WITH ranked_trips AS MATERIALIZED (
                SELECT trip_id, service_id, route_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY route_id ORDER BY trip_id
                       ) AS route_trip_rank
                FROM transit.trips
            ),
            sampled_trips AS MATERIALIZED (
                SELECT trip_id, service_id, route_id
                FROM ranked_trips
                WHERE route_trip_rank = 1
                ORDER BY route_id, trip_id
                LIMIT 500
            ),
            active_service AS MATERIALIZED (
                SELECT sampled.service_id, active.service_date
                FROM (
                    SELECT DISTINCT service_id
                    FROM sampled_trips
                ) AS sampled
                JOIN transit.calendar AS c
                  ON c.service_id = sampled.service_id
                JOIN LATERAL (
                    SELECT candidate.service_date
                    FROM (
                        SELECT service_day::date AS service_date
                        FROM generate_series(
                            c.start_date, c.end_date, INTERVAL '1 day'
                        ) AS service_day
                        LEFT JOIN transit.calendar_dates AS exception
                          ON exception.service_id = c.service_id
                         AND exception.service_date = service_day::date
                        WHERE exception.exception_type IS DISTINCT FROM 2
                          AND CASE EXTRACT(ISODOW FROM service_day)
                              WHEN 1 THEN c.monday
                              WHEN 2 THEN c.tuesday
                              WHEN 3 THEN c.wednesday
                              WHEN 4 THEN c.thursday
                              WHEN 5 THEN c.friday
                              WHEN 6 THEN c.saturday
                              WHEN 7 THEN c.sunday
                          END
                        UNION
                        SELECT added.service_date
                        FROM transit.calendar_dates AS added
                        WHERE added.service_id = c.service_id
                          AND added.exception_type = 1
                    ) AS candidate
                    ORDER BY candidate.service_date
                    LIMIT 1
                ) AS active ON TRUE
            ),
            candidate AS (
                SELECT
                    origin.stop_id AS origin_stop_id,
                    origin_stop.stop_name AS origin_stop_name,
                    destination.stop_id AS destination_stop_id,
                    destination_stop.stop_name AS destination_stop_name,
                    active.service_date,
                    origin.departure_time,
                    t.trip_id AS source_trip_id,
                    t.route_id AS source_route_id,
                    COALESCE(
                        r.route_short_name, r.route_long_name, r.route_id
                    ) AS source_route_name,
                    destination.arrival_time AS scheduled_source_arrival,
                    ROW_NUMBER() OVER (
                        PARTITION BY origin.stop_id, destination.stop_id
                        ORDER BY active.service_date, origin.departure_time,
                                 t.trip_id, origin.stop_sequence
                    ) AS pair_rank
                FROM sampled_trips AS t
                JOIN active_service AS active
                  ON active.service_id = t.service_id
                JOIN transit.routes AS r ON r.route_id = t.route_id
                JOIN transit.stop_times AS origin ON origin.trip_id = t.trip_id
                JOIN transit.stops AS origin_stop
                  ON origin_stop.stop_id = origin.stop_id
                JOIN LATERAL (
                    SELECT later.stop_id, later.arrival_time,
                           later.stop_sequence
                    FROM transit.stop_times AS later
                    WHERE later.trip_id = origin.trip_id
                      AND later.stop_sequence > origin.stop_sequence
                      AND later.stop_id <> origin.stop_id
                      AND later.arrival_time IS NOT NULL
                      AND COALESCE(later.drop_off_type, 0) <> 1
                    ORDER BY later.stop_sequence DESC
                    LIMIT 1
                ) AS destination ON TRUE
                JOIN transit.stops AS destination_stop
                  ON destination_stop.stop_id = destination.stop_id
                WHERE origin.departure_time IS NOT NULL
                  AND COALESCE(origin.pickup_type, 0) <> 1
            )
            , unique_pairs AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY source_route_id
                           ORDER BY service_date, departure_time,
                                    source_trip_id, origin_stop_id,
                                    destination_stop_id
                       ) AS route_rank
                FROM candidate
                WHERE pair_rank = 1
            )
            SELECT origin_stop_id, origin_stop_name,
                   destination_stop_id, destination_stop_name,
                   service_date, departure_time, source_trip_id,
                   source_route_id, source_route_name,
                   scheduled_source_arrival
            FROM unique_pairs
            WHERE route_rank = 1
            ORDER BY
                (departure_time >= INTERVAL '24 hours') DESC,
                service_date, source_route_id, source_trip_id,
                origin_stop_id, destination_stop_id
            LIMIT %s
        """
        with self._connection() as connection:
            return list(connection.execute(query, (limit,)).fetchall())

    def itinerary_leg_exists(self, leg: RouteLeg) -> bool:
        """Confirm a returned trip/route/stop combination in real GTFS data."""
        query = """
            SELECT EXISTS (
                SELECT 1
                FROM transit.trips AS t
                JOIN transit.stop_times AS origin
                  ON origin.trip_id = t.trip_id
                JOIN transit.stop_times AS destination
                  ON destination.trip_id = t.trip_id
                 AND destination.stop_sequence > origin.stop_sequence
                JOIN transit.stops AS origin_stop
                  ON origin_stop.stop_id = origin.stop_id
                JOIN transit.stops AS destination_stop
                  ON destination_stop.stop_id = destination.stop_id
                WHERE t.trip_id = %s
                  AND t.route_id = %s
                  AND origin.stop_id = %s
                  AND destination.stop_id = %s
                  AND origin_stop.stop_name = %s
                  AND destination_stop.stop_name = %s
                  AND origin.departure_time = %s
                  AND destination.arrival_time = %s
            ) AS present
        """
        parameters = (
            leg.trip_id,
            leg.route_id,
            leg.origin.stop_id,
            leg.destination.stop_id,
            leg.origin.stop_name,
            leg.destination.stop_name,
            leg.departure_time,
            leg.arrival_time,
        )
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return bool(row and row["present"])

    def route_count_between(self, origin_stop_id: str, destination_stop_id: str) -> int:
        """Count scheduled routes that serve an ordered stop pair."""
        query = """
            SELECT COUNT(DISTINCT t.route_id) AS route_count
            FROM transit.stop_times AS origin
            JOIN transit.stop_times AS destination
              ON destination.trip_id = origin.trip_id
             AND destination.stop_sequence > origin.stop_sequence
            JOIN transit.trips AS t ON t.trip_id = origin.trip_id
            WHERE origin.stop_id = %s
              AND destination.stop_id = %s
        """
        with self._connection() as connection:
            row = connection.execute(
                query, (origin_stop_id, destination_stop_id)
            ).fetchone()
        return int(row["route_count"]) if row else 0

    def next_operating_date_for_trip(
        self, trip_id: str, after_date: date
    ) -> date | None:
        """Find a later active date for deterministic calendar coverage."""
        query = """
            SELECT candidate.service_date
            FROM transit.trips AS t
            JOIN transit.calendar AS c ON c.service_id = t.service_id
            JOIN LATERAL (
                SELECT service_day::date AS service_date
                FROM generate_series(
                    %s::date + 1, c.end_date, INTERVAL '1 day'
                ) AS service_day
                LEFT JOIN transit.calendar_dates AS exception
                  ON exception.service_id = c.service_id
                 AND exception.service_date = service_day::date
                WHERE exception.exception_type = 1
                   OR (
                        exception.exception_type IS DISTINCT FROM 2
                        AND CASE EXTRACT(ISODOW FROM service_day)
                            WHEN 1 THEN c.monday
                            WHEN 2 THEN c.tuesday
                            WHEN 3 THEN c.wednesday
                            WHEN 4 THEN c.thursday
                            WHEN 5 THEN c.friday
                            WHEN 6 THEN c.saturday
                            WHEN 7 THEN c.sunday
                        END
                   )
                ORDER BY service_day
                LIMIT 1
            ) AS candidate ON TRUE
            WHERE t.trip_id = %s
        """
        with self._connection() as connection:
            row = connection.execute(query, (after_date, trip_id)).fetchone()
        return row["service_date"] if row else None
