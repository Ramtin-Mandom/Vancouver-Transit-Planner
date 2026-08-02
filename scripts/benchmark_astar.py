"""Reproducible warmed baseline-versus-A* routing comparison."""

from __future__ import annotations

import argparse
import json
from datetime import date
from statistics import median

from src.reliability.database import ReliabilityDatabase
from src.reliability.profiles import ProfileResolver
from src.routing.cli import parse_gtfs_time
from src.routing.database import TransitDatabase
from src.routing.planner import TransitPlanner


def route_signatures(result):
    return [
        [
            [
                leg.trip_id,
                leg.route_id,
                leg.origin.stop_id,
                leg.destination.stop_id,
                str(leg.departure_time),
                str(leg.arrival_time),
            ]
            for leg in alternative.itinerary.legs
        ]
        for alternative in result.alternatives
    ]


def measurement(result, samples):
    diagnostics = result.diagnostics
    counters = diagnostics.counters
    cache = diagnostics.cache_statistics
    query_count = sum(
        (
            cache.bulk_departure_query_count,
            cache.bulk_transfer_query_count,
            cache.bulk_trip_query_count,
            cache.bulk_profile_query_count,
            cache.departure_query_count,
            cache.trip_query_count,
            cache.transfer_query_count,
        )
    )
    return {
        "route_signatures": route_signatures(result),
        "candidate_count": len(result.alternatives),
        "median_total_ms": median(item.timing.total_ms for item in samples),
        "median_loading_ms": median(item.timing.data_loading_ms for item in samples),
        "median_search_ms": median(item.timing.search_ms for item in samples),
        "stops_expanded": counters.stops_expanded,
        "labels_expanded": counters.queue_pops - counters.stale_labels_skipped,
        "queue_pops": counters.queue_pops,
        "trips_loaded": cache.unique_trips_loaded,
        "database_query_count": query_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="3334")
    parser.add_argument("--destination", default="1875")
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 7, 31))
    parser.add_argument("--departure", type=parse_gtfs_time, default=parse_gtfs_time("08:00:00"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--trip-loading-mode", choices=("frontier", "eager"), default="frontier")
    args = parser.parse_args()

    transit = TransitDatabase()
    reliability = ReliabilityDatabase()
    planner = TransitPlanner(transit)
    try:
        results = {}
        for algorithm in ("baseline", "astar"):
            # One unmeasured warm-up followed by identical measured requests.
            planner.get_ranked_route_result(
                args.origin, args.destination, args.date, args.departure,
                ProfileResolver(reliability), algorithm=algorithm,
                trip_loading_mode=args.trip_loading_mode, include_diagnostics=True,
                timeout_seconds=120,
            )
            samples = [
                planner.get_ranked_route_result(
                    args.origin, args.destination, args.date, args.departure,
                    ProfileResolver(reliability), algorithm=algorithm,
                    trip_loading_mode=args.trip_loading_mode,
                    include_diagnostics=True, timeout_seconds=120,
                )
                for _ in range(max(1, args.runs))
            ]
            results[algorithm] = measurement(samples[-1], samples)
        results["signatures_identical"] = (
            results["baseline"]["route_signatures"]
            == results["astar"]["route_signatures"]
        )
        print(json.dumps(results, indent=2))
        if not results["signatures_identical"]:
            raise SystemExit(1)
    finally:
        reliability.close()
        transit.close()


if __name__ == "__main__":
    main()
