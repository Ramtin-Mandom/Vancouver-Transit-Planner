"""PostgreSQL persistence and reads for reliability data."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any
from threading import RLock

import psycopg
from psycopg.rows import dict_row

from src.data_ingestion.config import DatabaseConfig

from .models import DelayObservation, ReliabilityProfile, ScheduledStop
from .classification import EARLY_THRESHOLD_SECONDS, LATE_THRESHOLD_SECONDS
from .policy import DEFAULT_MINIMUM_SAMPLES


class ReliabilityDatabase:
    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or DatabaseConfig.from_environment()
        self._lookup_connection = None
        self._profile_connection = None
        self._schedule_cache: dict[str, list[ScheduledStop]] = {}
        self._statement_timeout_ms = 30_000
        self._profile_lock = RLock()

    def set_statement_timeout(self, milliseconds: int) -> None:
        self._statement_timeout_ms = max(1, milliseconds)
        if self._profile_connection is not None:
            self._profile_connection.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (str(self._statement_timeout_ms),),
            )

    def close(self) -> None:
        if self._profile_connection is not None:
            self._profile_connection.close()
        self._profile_connection = None

    def _profile_session(self):
        if self._profile_connection is None or self._profile_connection.closed:
            self._profile_connection = psycopg.connect(
                **self.config.connection_kwargs(),
                row_factory=dict_row,
                autocommit=True,
                options=(
                    "-c default_transaction_read_only=on "
                    f"-c statement_timeout={self._statement_timeout_ms}"
                ),
            )
        return self._profile_connection

    @contextmanager
    def connection(self, *, readonly: bool = True) -> Iterator:
        options = (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={self._statement_timeout_ms}"
            if readonly else None
        )
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
        row = self._profile_session().execute(query, params).fetchone()
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

    def aggregate_profiles(
        self,
        *,
        full_rebuild: bool = False,
        early_threshold: int = -120,
        late_threshold: int = 300,
        shrinkage_strength: float = 20.0,
        minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
    ) -> tuple[int, int, int, int]:
        sample_upsert = """
            WITH latest AS (
                SELECT DISTINCT ON (
                    trip_id, stop_id, stop_sequence, service_date
                )
                    trip_id, stop_id, stop_sequence, service_date,
                    scheduled_arrival, observed_at, delay_seconds
                FROM transit.delay_observations
                ORDER BY trip_id, stop_id, stop_sequence, service_date,
                         observed_at DESC
            ), samples AS (
                SELECT
                    latest.trip_id, latest.service_date,
                    t.route_id,
                    t.direction_id,
                    transit.reliability_time_window(latest.scheduled_arrival)
                        AS time_window,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY latest.delay_seconds
                    )::double precision AS representative_delay_seconds,
                    COUNT(*)::integer AS eligible_stop_count,
                    MAX(latest.observed_at) AS source_max_observed_at
                FROM latest
                JOIN transit.trips AS t ON t.trip_id = latest.trip_id
                WHERE latest.scheduled_arrival IS NOT NULL
                GROUP BY latest.trip_id, latest.service_date, t.route_id,
                         t.direction_id, time_window
            )
            INSERT INTO transit.trip_reliability_samples (
                trip_id, service_date, time_window, route_id, direction_id,
                representative_delay_seconds, eligible_stop_count,
                source_max_observed_at, updated_at
            )
            SELECT trip_id, service_date, time_window, route_id, direction_id,
                   representative_delay_seconds, eligible_stop_count,
                   source_max_observed_at, CURRENT_TIMESTAMP
            FROM samples
            ON CONFLICT (trip_id, service_date, time_window)
            DO UPDATE SET
                route_id = EXCLUDED.route_id,
                direction_id = EXCLUDED.direction_id,
                representative_delay_seconds =
                    EXCLUDED.representative_delay_seconds,
                eligible_stop_count = EXCLUDED.eligible_stop_count,
                source_max_observed_at = EXCLUDED.source_max_observed_at,
                updated_at = CASE WHEN
                    transit.trip_reliability_samples.source_max_observed_at
                        IS DISTINCT FROM EXCLUDED.source_max_observed_at
                    OR transit.trip_reliability_samples.representative_delay_seconds
                        IS DISTINCT FROM EXCLUDED.representative_delay_seconds
                    THEN CURRENT_TIMESTAMP
                    ELSE transit.trip_reliability_samples.updated_at
                END
        """
        rebuild_fallbacks = """
            DELETE FROM transit.reliability_fallback_profiles;
            WITH network AS (
                SELECT COUNT(*)::integer n, COUNT(DISTINCT service_date)::integer d,
                       AVG((representative_delay_seconds BETWEEN %s AND %s)::int)
                           ::double precision p
                FROM transit.trip_reliability_samples
            )
            INSERT INTO transit.reliability_fallback_profiles
            SELECT 'network', '*', -1, n, d, p, p, CURRENT_TIMESTAMP
            FROM network WHERE n > 0;

            WITH grouped AS (
                SELECT route_id, COUNT(*)::integer n,
                       COUNT(DISTINCT service_date)::integer d,
                       AVG((representative_delay_seconds BETWEEN %s AND %s)::int)
                           ::double precision p
                FROM transit.trip_reliability_samples GROUP BY route_id
            ), network AS (
                SELECT reliability_probability p
                FROM transit.reliability_fallback_profiles
                WHERE profile_level='network'
            )
            INSERT INTO transit.reliability_fallback_profiles
            SELECT 'route', g.route_id, -1, g.n, g.d, g.p,
                   g.n::double precision/(g.n+%s)*g.p
                   + %s/(g.n+%s)*network.p, CURRENT_TIMESTAMP
            FROM grouped g CROSS JOIN network;

            WITH grouped AS (
                SELECT route_id, COALESCE(direction_id, -1) direction_key,
                       COUNT(*)::integer n,
                       COUNT(DISTINCT service_date)::integer d,
                       AVG((representative_delay_seconds BETWEEN %s AND %s)::int)
                           ::double precision p
                FROM transit.trip_reliability_samples
                GROUP BY route_id, COALESCE(direction_id, -1)
            )
            INSERT INTO transit.reliability_fallback_profiles
            SELECT 'route_direction', g.route_id, g.direction_key, g.n, g.d, g.p,
                   g.n::double precision/(g.n+%s)*g.p
                   + %s/(g.n+%s)*r.reliability_probability, CURRENT_TIMESTAMP
            FROM grouped g JOIN transit.reliability_fallback_profiles r
              ON r.profile_level='route' AND r.route_key=g.route_id;
        """
        rebuild_exact = """
            DELETE FROM transit.route_direction_reliability;
            WITH grouped AS (
                SELECT route_id, direction_id, COALESCE(direction_id, -1) direction_key,
                       time_window, COUNT(*)::integer n,
                       COUNT(DISTINCT service_date)::integer d,
                       AVG(representative_delay_seconds)::double precision mean_delay,
                       AVG(ABS(representative_delay_seconds))::double precision mean_abs,
                       STDDEV_SAMP(representative_delay_seconds)::double precision stddev,
                       PERCENTILE_CONT(.5) WITHIN GROUP
                           (ORDER BY representative_delay_seconds)::double precision p50,
                       PERCENTILE_CONT(.9) WITHIN GROUP
                           (ORDER BY ABS(representative_delay_seconds))::double precision p90_abs,
                       AVG((representative_delay_seconds < %s)::int)::double precision early,
                       AVG((representative_delay_seconds BETWEEN %s AND %s)::int)::double precision ontime,
                       AVG((representative_delay_seconds > %s)::int)::double precision late
                FROM transit.trip_reliability_samples
                GROUP BY route_id, direction_id, time_window
            )
            INSERT INTO transit.route_direction_reliability
            SELECT g.route_id, g.direction_key, g.direction_id, g.time_window,
                   g.n, g.d, g.mean_delay, g.mean_abs, g.stddev, g.p50,
                   g.p90_abs, g.early, g.ontime, g.late,
                   g.n::double precision/(g.n+%s)*g.ontime
                     + %s/(g.n+%s)*parent.reliability_probability,
                   'route_direction', g.n < %s, CURRENT_TIMESTAMP
            FROM grouped g
            JOIN transit.reliability_fallback_profiles parent
              ON parent.profile_level='route_direction'
             AND parent.route_key=g.route_id
             AND parent.direction_key=g.direction_key
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
            if full_rebuild:
                connection.execute("DELETE FROM transit.route_direction_reliability")
                connection.execute("DELETE FROM transit.reliability_fallback_profiles")
                connection.execute("DELETE FROM transit.trip_reliability_samples")
            observations = connection.execute(count_latest).fetchone()["count"]
            connection.execute(sample_upsert)
            samples = connection.execute(
                "SELECT COUNT(*) count FROM transit.trip_reliability_samples"
            ).fetchone()["count"]
            s = shrinkage_strength
            fallback_statements = [
                statement.strip()
                for statement in rebuild_fallbacks.split(";")
                if statement.strip()
            ]
            connection.execute(fallback_statements[0])
            connection.execute(
                fallback_statements[1], (early_threshold, late_threshold)
            )
            connection.execute(
                fallback_statements[2],
                (early_threshold, late_threshold, s, s, s),
            )
            connection.execute(
                fallback_statements[3],
                (early_threshold, late_threshold, s, s, s),
            )
            exact_statements = [
                statement.strip()
                for statement in rebuild_exact.split(";")
                if statement.strip()
            ]
            connection.execute(exact_statements[0])
            connection.execute(
                exact_statements[1],
                (early_threshold, early_threshold, late_threshold,
                 late_threshold, s, s, s, minimum_samples),
            )
            rows = connection.execute(
                "SELECT sample_count FROM transit.route_direction_reliability"
            ).fetchall()
        return int(observations), int(samples), len(rows), sum(
            row["sample_count"] < minimum_samples for row in rows
        )

    def profile(
        self, route_id: str, direction_id: int | None, time_window: str
    ) -> ReliabilityProfile | None:
        query = """
            SELECT route_id, direction_id, time_window, sample_count,
                   distinct_service_dates, mean_delay_seconds,
                   mean_absolute_delay_seconds, delay_stddev_seconds,
                   p50_delay_seconds, p90_absolute_delay_seconds,
                   early_probability, on_time_probability, late_probability,
                   reliability_probability
            FROM transit.route_direction_reliability
            WHERE route_id=%s AND direction_key=COALESCE(%s, -1)
              AND time_window=%s
        """
        row = self._profile_session().execute(
            query, (route_id, direction_id, time_window)
        ).fetchone()
        return self._profile_from_row(row) if row else None

    @staticmethod
    def _profile_from_row(row: dict[str, Any]) -> ReliabilityProfile:
        return ReliabilityProfile(
            route_id=row["route_id"],
            stop_id=None,
            weekday=None,
            hour_of_day=None,
            sample_count=row["sample_count"],
            mean_delay_seconds=row["mean_delay_seconds"],
            mean_absolute_delay_seconds=row["mean_absolute_delay_seconds"],
            delay_stddev_seconds=row["delay_stddev_seconds"],
            p50_delay_seconds=row["p50_delay_seconds"],
            p90_delay_seconds=row["p90_absolute_delay_seconds"],
            early_probability=row["early_probability"],
            on_time_probability=row["on_time_probability"],
            late_probability=row["late_probability"],
            direction_id=row["direction_id"],
            time_window=row["time_window"],
            p90_absolute_delay_seconds=row["p90_absolute_delay_seconds"],
            reliability_probability=row["reliability_probability"],
            distinct_service_dates=row["distinct_service_dates"],
        )

    def bulk_profile_data(
        self, keys: set[tuple[str, int | None, str]]
    ) -> tuple[
        dict[tuple[str, int | None, str], ReliabilityProfile],
        dict[tuple[str, str, int], dict[str, Any]],
    ]:
        """Load exact cells and all applicable parents in two queries."""
        if not keys:
            return {}, {}
        routes = sorted({route_id for route_id, _, _ in keys})
        windows = sorted({window for _, _, window in keys})
        exact_query = """
            SELECT route_id, direction_id, time_window, sample_count,
                   distinct_service_dates, mean_delay_seconds,
                   mean_absolute_delay_seconds, delay_stddev_seconds,
                   p50_delay_seconds, p90_absolute_delay_seconds,
                   early_probability, on_time_probability, late_probability,
                   reliability_probability
            FROM transit.route_direction_reliability
            WHERE route_id = ANY(%s::text[])
              AND time_window = ANY(%s::text[])
        """
        fallback_query = """
            SELECT profile_level, route_key, direction_key, sample_count,
                   distinct_service_dates, on_time_probability,
                   reliability_probability
            FROM transit.reliability_fallback_profiles
            WHERE route_key = ANY(%s::text[]) OR route_key = '*'
        """
        with self._profile_lock:
            session = self._profile_session()
            exact_rows = session.execute(exact_query, (routes, windows)).fetchall()
            fallback_rows = session.execute(fallback_query, (routes,)).fetchall()
        exact = {
            (row["route_id"], row["direction_id"], row["time_window"]):
                self._profile_from_row(row)
            for row in exact_rows
        }
        fallbacks = {
            (row["profile_level"], row["route_key"], row["direction_key"]): row
            for row in fallback_rows
        }
        return exact, fallbacks

    def fallback_profile(
        self, level: str, route_id: str | None, direction_id: int | None
    ) -> dict[str, Any] | None:
        query = """
            SELECT * FROM transit.reliability_fallback_profiles
            WHERE profile_level=%s AND route_key=%s AND direction_key=%s
        """
        return self._profile_session().execute(
            query,
            (level, route_id or "*", -1 if direction_id is None else direction_id),
        ).fetchone()

    def count_profiles_below(self, minimum_samples: int) -> int:
        query = """
            SELECT COUNT(*) AS count
            FROM transit.route_direction_reliability
            WHERE sample_count < %s
        """
        with self.connection() as connection:
            row = connection.execute(query, (minimum_samples,)).fetchone()
        return int(row["count"])
