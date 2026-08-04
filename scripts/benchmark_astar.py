"""Reproducible warmed three-algorithm routing comparison."""

from __future__ import annotations

import argparse
import json
from datetime import date
from statistics import median
from time import perf_counter

from src.reliability.database import ReliabilityDatabase
from src.reliability.profiles import ProfileResolver
from src.routing.cache import (
    DEFAULT_ROUTING_CACHE_MODE,
    CacheConfiguration,
    RoutingCacheManager,
)
from src.routing.cli import parse_gtfs_time
from src.routing.database import TransitDatabase
from src.routing.planner import TransitPlanner
from src.routing.warmup import RoutingWarmupCoordinator, WarmupConfiguration


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


def measurement(
    result,
    samples,
    cold,
    startup_to_ready_ms=0.0,
    warmup_state=None,
    startup_initialization_ms=0.0,
):
    diagnostics = result.diagnostics
    counters = diagnostics.counters
    cache = diagnostics.cache_statistics
    cold_cache = cold.diagnostics.cache_statistics
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
    cold_query_count = sum(
        (
            cold_cache.bulk_departure_query_count,
            cold_cache.bulk_transfer_query_count,
            cold_cache.bulk_trip_query_count,
            cold_cache.bulk_profile_query_count,
            cold_cache.departure_query_count,
            cold_cache.trip_query_count,
            cold_cache.transfer_query_count,
        )
    )
    return {
        "route_signatures": route_signatures(result),
        "candidate_count": len(result.alternatives),
        "median_total_ms": median(item.timing.total_ms for item in samples),
        "cold_total_ms": cold.timing.total_ms,
        "cold_loading_ms": cold.timing.data_loading_ms,
        "cold_search_ms": cold.timing.search_ms,
        "median_loading_ms": median(item.timing.data_loading_ms for item in samples),
        "median_search_ms": median(item.timing.search_ms for item in samples),
        "stops_expanded": counters.stops_expanded,
        "labels_expanded": counters.queue_pops - counters.stale_labels_skipped,
        "queue_pops": counters.queue_pops,
        "rounds": counters.rounds_executed,
        "route_scans": counters.route_pattern_scans,
        "trips_considered": counters.trips_considered or counters.trips_examined,
        "connections_examined": (
            counters.stop_time_entries_scanned or counters.connections_examined
        ),
        "labels_created": counters.labels_created,
        "trips_loaded": cache.unique_trips_loaded,
        "database_query_count": query_count,
        "cold_database_query_count": cold_query_count,
        "cold_trips_queried": cold_cache.unique_trips_loaded,
        "cold_connections_loaded": cold_cache.unique_connections_loaded,
        "connections_loaded": cache.unique_connections_loaded,
        "cache_hits": {
            "trip_request": cache.trip_request_cache_hits,
            "trip_shared": cache.trip_shared_cache_hits,
            "daily": cache.daily_index_hits,
            "reliability": cache.reliability_cache_hits,
            "heuristic": cache.heuristic_cache_hits,
        },
        "cache_misses": {
            "trip_shared": cache.trip_shared_cache_misses,
            "daily": cache.daily_index_misses,
            "reliability": cache.reliability_cache_misses,
            "heuristic": cache.heuristic_cache_misses,
        },
        "cache_evictions": cache.cache_evictions,
        "approximate_request_memory_bytes": cache.request_index_memory_estimate_bytes,
        "approximate_shared_cache_memory_bytes": cache.shared_cache_memory_estimate_bytes,
        "cold_warm_results_equal": route_signatures(cold) == route_signatures(result),
        "startup_to_ready_ms": startup_to_ready_ms,
        "startup_initialization_ms": startup_initialization_ms,
        "warmup": vars(warmup_state) if warmup_state is not None else None,
        "cold_components_ms": {
            "gtfs_version_lookup": cold.diagnostics.timings_ms.gtfs_version_lookup_ms,
            "static_snapshot_build": cold.diagnostics.timings_ms.static_snapshot_build_ms,
            "daily_departure_index_build": cold.diagnostics.timings_ms.daily_departure_index_build_ms,
            "daily_departure_query": cold.diagnostics.timings_ms.daily_departure_query_ms,
            "daily_departure_grouping": cold.diagnostics.timings_ms.daily_departure_grouping_ms,
            "daily_departure_sorting": cold.diagnostics.timings_ms.daily_departure_sorting_ms,
            "trip_connection_query": cold.diagnostics.timings_ms.frontier_trip_query_ms,
            "reliability_snapshot_build": cold.diagnostics.timings_ms.reliability_snapshot_build_ms,
            "astar_search": cold.timing.search_ms,
            "ranking": cold.timing.ranking_ms,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="3334")
    parser.add_argument("--destination", default="1875")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--departure", type=parse_gtfs_time, default=parse_gtfs_time("08:00:00")
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-extra-minutes", type=int, default=10)
    parser.add_argument(
        "--algorithms",
        nargs="+",
        choices=("baseline", "astar", "mc_raptor"),
        default=("baseline", "astar", "mc_raptor"),
    )
    parser.add_argument(
        "--warmup",
        choices=("disabled", "essential", "skytrain"),
        default="disabled",
    )
    parser.add_argument(
        "--trip-loading-mode", choices=("frontier", "eager"), default="frontier"
    )
    parser.add_argument(
        "--cache-mode",
        choices=("request", "shared"),
        default=DEFAULT_ROUTING_CACHE_MODE,
        help="request uses the previous request-local caches; shared uses process caches",
    )
    args = parser.parse_args()

    initialization_started = perf_counter()
    transit = TransitDatabase()
    transit.initialize()
    reliability = ReliabilityDatabase()
    startup_initialization_ms = (perf_counter() - initialization_started) * 1000
    # Disable the response layer here so the benchmark measures the underlying
    # daily/trip/profile/heuristic caches rather than returning stored timings.
    cache_manager = RoutingCacheManager(CacheConfiguration(response_enabled=False))
    planner = TransitPlanner(transit, cache_manager=cache_manager)

    def resolver():
        return ProfileResolver(
            reliability,
            shared_cache=(cache_manager if args.cache_mode == "shared" else None),
            profile_version=reliability.profile_version(),
        )

    try:
        results = {}
        for algorithm in args.algorithms:
            cache_manager.clear()  # cold-process measurement starts here
            startup_to_ready_ms = startup_initialization_ms
            warmup_state = None
            if args.cache_mode == "shared" and args.warmup != "disabled":
                coordinator = RoutingWarmupCoordinator(
                    cache_manager,
                    transit,
                    reliability,
                    WarmupConfiguration(
                        enabled=True,
                        block_readiness=True,
                        today_index=True,
                        skytrain=args.warmup == "skytrain",
                        tomorrow_index=False,
                    ),
                )
                startup_started = perf_counter()
                warmup_state = coordinator.warm_essential(args.date)
                startup_to_ready_ms = (
                    startup_initialization_ms
                    + (perf_counter() - startup_started) * 1000
                )
            cold = planner.get_ranked_route_result(
                args.origin,
                args.destination,
                args.date,
                args.departure,
                resolver(),
                algorithm=algorithm,
                cache_mode=args.cache_mode,
                trip_loading_mode=args.trip_loading_mode,
                include_diagnostics=True,
                timeout_seconds=120,
                max_extra_minutes=args.max_extra_minutes,
            )
            samples = [
                planner.get_ranked_route_result(
                    args.origin,
                    args.destination,
                    args.date,
                    args.departure,
                    resolver(),
                    algorithm=algorithm,
                    cache_mode=args.cache_mode,
                    trip_loading_mode=args.trip_loading_mode,
                    include_diagnostics=True,
                    timeout_seconds=120,
                    max_extra_minutes=args.max_extra_minutes,
                )
                for _ in range(max(1, args.runs))
            ]
            results[algorithm] = measurement(
                samples[-1],
                samples,
                cold,
                startup_to_ready_ms,
                warmup_state,
                startup_initialization_ms,
            )
        results["signatures_identical"] = (
            len(
                {
                    json.dumps(results[name]["route_signatures"], sort_keys=True)
                    for name in args.algorithms
                }
            )
            == 1
        )
        print(json.dumps(results, indent=2))
        if not results["signatures_identical"]:
            raise SystemExit(1)
    finally:
        reliability.close()
        transit.close()


if __name__ == "__main__":
    main()
