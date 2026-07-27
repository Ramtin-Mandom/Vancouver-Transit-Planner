"""PostgreSQL persistence and reads for reliability data."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from src.data_ingestion.config import DatabaseConfig

from .models import DelayObservation, ReliabilityProfile, ScheduledStop


class ReliabilityDatabase:
    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or DatabaseConfig.from_environment()
        self._lookup_connection = None
        self._schedule_cache: dict[str, list[ScheduledStop]] = {}

    @contextmanager
    def connection(self, *, readonly: bool = True) -> Iterator:
        options = "-c default_transaction_read_only=on" if readonly else None
        with psycopg.connect(
            **self.config.connection_kwargs(),
            row_factory=dict_row,
            options=options,
        ) as connection:
            yield connection

    @contextmanager
    def lookup_session(self) -> Iterator[None]:
        """Reuse one read-only connection while parsing a feed snapshot."""
        if self._lookup_connection is not None:
            yield
            return
        with psycopg.connect(
            **self.config.connection_kwargs(),
            row_factory=dict_row,
            autocommit=True,
            options="-c default_transaction_read_only=on",
        ) as connection:
            self._lookup_connection = connection
            self._schedule_cache = {}
            try:
                yield
            finally:
                self._lookup_connection = None
                self._schedule_cache = {}

    def preload_schedules(self, trip_ids: Iterable[str]) -> None:
        """Load all referenced trip schedules with one parameterized query."""
        identifiers = sorted({item for item in trip_ids if item})
        if not identifiers:
            return
        query = """
            SELECT trip_id, stop_id, stop_sequence,
                   COALESCE(arrival_time, departure_time) AS scheduled_arrival,
                   departure_time AS scheduled_departure
            FROM transit.stop_times
            WHERE trip_id = ANY(%s::text[])
              AND COALESCE(arrival_time, departure_time) IS NOT NULL
            ORDER BY trip_id, stop_sequence
        """
        if self._lookup_connection is None:
            with self.lookup_session():
                self.preload_schedules(identifiers)
            return
        rows = self._lookup_connection.execute(query, (identifiers,)).fetchall()
        self._schedule_cache = {trip_id: [] for trip_id in identifiers}
        for row in rows:
            self._schedule_cache[row["trip_id"]].append(ScheduledStop(**row))

    def scheduled_stop(
        self, trip_id: str, stop_id: str | None, stop_sequence: int | None
    ) -> ScheduledStop | None:
        if not stop_id and stop_sequence is None:
            return None
        if self._lookup_connection is not None:
            if trip_id not in self._schedule_cache:
                self.preload_schedules((trip_id,))
            for scheduled in self._schedule_cache.get(trip_id, ()):
                if stop_id is not None and scheduled.stop_id != stop_id:
                    continue
                if (
                    stop_sequence is not None
                    and scheduled.stop_sequence != stop_sequence
                ):
                    continue
                return scheduled
            return None
        query = """
            SELECT trip_id, stop_id, stop_sequence,
                   COALESCE(arrival_time, departure_time) AS scheduled_arrival,
                   departure_time AS scheduled_departure
            FROM transit.stop_times
            WHERE trip_id = %s
              AND (%s::text IS NULL OR stop_id = %s)
              AND (%s::integer IS NULL OR stop_sequence = %s)
            ORDER BY stop_sequence
            LIMIT 1
        """
        params = (trip_id, stop_id, stop_id, stop_sequence, stop_sequence)
        with self.connection() as connection:
            row = connection.execute(query, params).fetchone()
        if not row or row["scheduled_arrival"] is None:
            return None
        return ScheduledStop(**row)

    def insert_observations(
        self, observations: Iterable[DelayObservation]
    ) -> tuple[int, int]:
        values = list(observations)
        if not values:
            return 0, 0
        query = """
            INSERT INTO transit.delay_observations (
                trip_id, stop_id, stop_sequence, service_date,
                scheduled_arrival, observed_at, delay_seconds, source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (
                trip_id, stop_id, stop_sequence, service_date, observed_at
            ) DO NOTHING
        """
        rows = [
            (
                item.trip_id,
                item.stop_id,
                item.stop_sequence,
                item.service_date,
                item.scheduled_arrival,
                item.observed_at,
                item.delay_seconds,
                item.source,
            )
            for item in values
        ]
        with self.connection(readonly=False) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(query, rows)
                inserted = cursor.rowcount
        return inserted, len(values) - inserted

    def aggregate_profiles(self) -> tuple[int, int, int]:
        latest = """
            WITH latest AS (
                SELECT DISTINCT ON (
                    trip_id, stop_id, stop_sequence, service_date
                )
                    trip_id, stop_id, stop_sequence, service_date,
                    scheduled_arrival, delay_seconds
                FROM transit.delay_observations
                ORDER BY trip_id, stop_id, stop_sequence, service_date,
                         observed_at DESC
            ),
            aggregated AS (
                SELECT
                    t.route_id,
                    latest.stop_id,
                    EXTRACT(ISODOW FROM latest.service_date)::integer - 1
                        AS weekday,
                    MOD(
                        FLOOR(EXTRACT(EPOCH FROM latest.scheduled_arrival) / 3600),
                        24
                    )::integer AS hour_of_day,
                    COUNT(*)::integer AS sample_count,
                    AVG(latest.delay_seconds)::double precision
                        AS mean_delay_seconds,
                    STDDEV_SAMP(latest.delay_seconds)::double precision
                        AS delay_stddev_seconds,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY latest.delay_seconds
                    )::double precision AS p50_delay_seconds,
                    PERCENTILE_CONT(0.9) WITHIN GROUP (
                        ORDER BY latest.delay_seconds
                    )::double precision AS p90_delay_seconds,
                    AVG(
                        CASE WHEN latest.delay_seconds <= 300
                             THEN 1.0 ELSE 0.0 END
                    )::double precision AS on_time_probability
                FROM latest
                JOIN transit.trips AS t ON t.trip_id = latest.trip_id
                GROUP BY t.route_id, latest.stop_id, weekday, hour_of_day
            )
            INSERT INTO transit.route_reliability (
                route_id, stop_id, weekday, hour_of_day, sample_count,
                mean_delay_seconds, delay_stddev_seconds, p50_delay_seconds,
                p90_delay_seconds, on_time_probability, updated_at
            )
            SELECT route_id, stop_id, weekday, hour_of_day, sample_count,
                   mean_delay_seconds, delay_stddev_seconds,
                   p50_delay_seconds, p90_delay_seconds,
                   on_time_probability, CURRENT_TIMESTAMP
            FROM aggregated
            ON CONFLICT (route_id, stop_id, weekday, hour_of_day)
            DO UPDATE SET
                sample_count = EXCLUDED.sample_count,
                mean_delay_seconds = EXCLUDED.mean_delay_seconds,
                delay_stddev_seconds = EXCLUDED.delay_stddev_seconds,
                p50_delay_seconds = EXCLUDED.p50_delay_seconds,
                p90_delay_seconds = EXCLUDED.p90_delay_seconds,
                on_time_probability = EXCLUDED.on_time_probability,
                updated_at = CURRENT_TIMESTAMP
            RETURNING sample_count
        """
        count_latest = """
            SELECT COUNT(*) AS count
            FROM (
                SELECT DISTINCT ON (
                    trip_id, stop_id, stop_sequence, service_date
                ) 1
                FROM transit.delay_observations
                ORDER BY trip_id, stop_id, stop_sequence, service_date,
                         observed_at DESC
            ) AS latest
        """
        with self.connection(readonly=False) as connection:
            connection.execute("DELETE FROM transit.route_reliability")
            observations = connection.execute(count_latest).fetchone()["count"]
            rows = connection.execute(latest).fetchall()
        return int(observations), len(rows), sum(row["sample_count"] < 20 for row in rows)

    def profile(
        self,
        route_id: str | None,
        stop_id: str | None,
        weekday: int | None,
        hour: int | None,
    ) -> ReliabilityProfile | None:
        query = """
            WITH latest AS (
                SELECT DISTINCT ON (
                    observation.trip_id, observation.stop_id,
                    observation.stop_sequence, observation.service_date
                )
                    observation.*, trip.route_id
                FROM transit.delay_observations AS observation
                JOIN transit.trips AS trip
                  ON trip.trip_id = observation.trip_id
                ORDER BY observation.trip_id, observation.stop_id,
                         observation.stop_sequence, observation.service_date,
                         observation.observed_at DESC
            )
            SELECT
                %s::text AS route_id, %s::text AS stop_id,
                %s::integer AS weekday, %s::integer AS hour_of_day,
                COUNT(*)::integer AS sample_count,
                AVG(delay_seconds)::double precision AS mean_delay_seconds,
                STDDEV_SAMP(delay_seconds)::double precision
                    AS delay_stddev_seconds,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delay_seconds)
                    ::double precision AS p50_delay_seconds,
                PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY delay_seconds)
                    ::double precision AS p90_delay_seconds,
                AVG(CASE WHEN delay_seconds <= 300 THEN 1.0 ELSE 0.0 END)
                    ::double precision AS on_time_probability
            FROM latest
            WHERE (%s::text IS NULL OR route_id = %s)
              AND (%s::text IS NULL OR stop_id = %s)
              AND (
                  %s::integer IS NULL
                  OR EXTRACT(ISODOW FROM service_date)::integer - 1 = %s
              )
              AND (
                  %s::integer IS NULL
                  OR MOD(
                      FLOOR(EXTRACT(EPOCH FROM scheduled_arrival) / 3600), 24
                  )::integer = %s
              )
        """
        params = (
            route_id, stop_id, weekday, hour,
            route_id, route_id, stop_id, stop_id,
            weekday, weekday, hour, hour,
        )
        with self.connection() as connection:
            row = connection.execute(query, params).fetchone()
        if not row or row["sample_count"] == 0:
            return None
        return ReliabilityProfile(**row)

    def route_profiles(self, route_id: str) -> list[dict[str, Any]]:
        query = """
            SELECT * FROM transit.route_reliability
            WHERE route_id = %s
            ORDER BY stop_id, weekday, hour_of_day
        """
        with self.connection() as connection:
            return list(connection.execute(query, (route_id,)).fetchall())

    def count_profiles_below(self, minimum_samples: int) -> int:
        query = """
            SELECT COUNT(*) AS count
            FROM transit.route_reliability
            WHERE sample_count < %s
        """
        with self.connection() as connection:
            row = connection.execute(query, (minimum_samples,)).fetchone()
        return int(row["count"])
