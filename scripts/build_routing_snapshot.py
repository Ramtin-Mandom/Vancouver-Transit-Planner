"""Build the production routing artifact outside FastAPI.

Usage: ``python -m scripts.build_routing_snapshot --output data/routing_snapshot``.
The server-side cursor keeps PostgreSQL result batches bounded; raw delay
observations are never selected.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from src.data_ingestion.config import DatabaseConfig
from src.routing.snapshot import build_snapshot_from_rows

CONNECTION_SQL = """
WITH ordered AS (
 SELECT st.trip_id, st.stop_id, st.departure_time, st.stop_sequence,
        st.pickup_type,
        LEAD(st.stop_id) OVER w AS next_stop_id,
        LEAD(st.arrival_time) OVER w AS next_arrival_time,
        LEAD(st.drop_off_type) OVER w AS next_drop_off_type
 FROM transit.stop_times st
 WINDOW w AS (PARTITION BY st.trip_id ORDER BY st.stop_sequence)
)
SELECT o.stop_id AS from_stop_id, o.next_stop_id AS to_stop_id,
       EXTRACT(EPOCH FROM o.departure_time)::bigint AS departure_seconds,
       EXTRACT(EPOCH FROM o.next_arrival_time)::bigint AS arrival_seconds,
       o.stop_sequence, t.trip_id, t.route_id, t.service_id, t.direction_id,
       o.pickup_type, o.next_drop_off_type AS drop_off_type,
       COALESCE(r.route_short_name, r.route_long_name, r.route_id) AS route_name
FROM ordered o JOIN transit.trips t ON t.trip_id=o.trip_id
JOIN transit.routes r ON r.route_id=t.route_id
WHERE o.departure_time IS NOT NULL AND o.next_arrival_time IS NOT NULL
ORDER BY t.trip_id, o.stop_sequence
"""


def rows(cursor, batch_size: int):
    while batch := cursor.fetchmany(batch_size):
        yield from batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact mmap routing snapshot")
    parser.add_argument("--output", type=Path, default=Path("data/routing_snapshot"))
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument(
        "--max-peak-rss-mb",
        type=int,
        default=int(os.getenv("ROUTING_SNAPSHOT_BUILD_MAX_RSS_MB", "450")),
        help="fail when measured process peak RSS exceeds this many MiB",
    )
    parser.add_argument("--fixture-json", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.fixture_json is not None:
        fixture = json.loads(args.fixture_json.read_text(encoding="utf-8"))
        report = build_snapshot_from_rows(
            args.output,
            stops=fixture["stops"],
            connections=fixture["connections"],
            source_version=str(fixture.get("source_version", "render-fixture")),
            reliability_profiles=fixture.get("reliability_profiles", ()),
            reliability_fallback_profiles=fixture.get(
                "reliability_fallback_profiles", ()
            ),
            max_peak_rss_bytes=args.max_peak_rss_mb * 1024 * 1024,
        )
    else:
        config = DatabaseConfig.from_environment()
        with psycopg.connect(
            **config.connection_kwargs(),
            row_factory=dict_row,
            options="-c default_transaction_read_only=on",
        ) as connection:
            stops = connection.execute("""SELECT s.stop_id,s.stop_name,s.stop_code,s.stop_lat,s.stop_lon,s.parent_station
          FROM transit.stops s WHERE EXISTS (SELECT 1 FROM transit.stop_times st WHERE st.stop_id=s.stop_id)
          ORDER BY s.stop_id""").fetchall()
            version = connection.execute("""SELECT COALESCE(MAX(feed_version),
          CONCAT(MAX(feed_start_date)::text,':',MAX(feed_end_date)::text),'empty-feed') version
          FROM transit.feed_info""").fetchone()["version"]
            calendars = connection.execute(
                "SELECT * FROM transit.calendar ORDER BY service_id"
            ).fetchall()
            calendar_dates = connection.execute(
                "SELECT service_id, service_date, exception_type FROM transit.calendar_dates ORDER BY service_date, service_id"
            ).fetchall()
            reliability_profiles = connection.execute(
                "SELECT route_id, direction_id, time_window, reliability_probability, sample_count FROM transit.route_direction_reliability ORDER BY route_id, direction_id, time_window"
            ).fetchall()
            fallback_profiles = connection.execute(
                "SELECT profile_level, route_key, direction_key, reliability_probability, on_time_probability, sample_count, distinct_service_dates FROM transit.reliability_fallback_profiles ORDER BY profile_level, route_key, direction_key"
            ).fetchall()
            transfers = connection.execute(
                "SELECT from_stop_id, to_stop_id, transfer_type, min_transfer_time FROM transit.transfers ORDER BY from_stop_id, to_stop_id"
            ).fetchall()
            with connection.cursor(name="routing_snapshot_connections") as cursor:
                cursor.execute(CONNECTION_SQL)
                report = build_snapshot_from_rows(
                    args.output,
                    stops=stops,
                    connections=rows(cursor, max(1, args.batch_size)),
                    source_version=str(version),
                    calendars=calendars,
                    calendar_dates=calendar_dates,
                    reliability_profiles=reliability_profiles,
                    reliability_fallback_profiles=fallback_profiles,
                    transfers=transfers,
                    max_peak_rss_bytes=args.max_peak_rss_mb * 1024 * 1024,
                )
    build = report["build"]
    print(
        f"built {report['counts']} in {build['duration_seconds']:.3f}s; "
        f"{build['size_bytes']} bytes; peak RSS={build['peak_rss_bytes']}"
    )


if __name__ == "__main__":
    main()
