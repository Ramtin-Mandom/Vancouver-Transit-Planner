"""Repeatable snapshot load and route-search benchmark."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import date, timedelta
from pathlib import Path

from src.routing.snapshot import (
    RoutingSnapshot,
    SnapshotPlanner,
    _geographic_heuristic_metadata,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="routing snapshot directory")
    parser.add_argument("--origin", help="origin stop ID")
    parser.add_argument("--destination", help="destination stop ID")
    parser.add_argument("--service-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--departure-seconds", type=int, default=28_800)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--revalidate-legacy-metadata", action="store_true")
    return parser


def timed_route(
    planner: SnapshotPlanner,
    args: argparse.Namespace,
    algorithm: str,
    alternatives: bool,
):
    started = time.perf_counter()
    routed = planner.get_ranked_route_result(
        args.origin,
        args.destination,
        args.service_date,
        timedelta(seconds=args.departure_seconds),
        algorithm=algorithm,
        include_alternatives=alternatives,
        include_diagnostics=True,
        timeout_seconds=args.timeout_seconds,
    )
    return (time.perf_counter() - started) * 1_000, routed


def main() -> None:
    args = build_parser().parse_args()
    if (args.origin is None) != (args.destination is None):
        raise SystemExit("--origin and --destination must be provided together")
    if args.iterations < 1:
        raise SystemExit("--iterations must be at least 1")

    started = time.perf_counter()
    snapshot = RoutingSnapshot(args.path)
    try:
        report = {
            "snapshot_size_bytes": sum(
                path.stat().st_size for path in args.path.iterdir()
            ),
            "load_ms": (time.perf_counter() - started) * 1_000,
            "runtime_timetable_sql_queries": 0,
        }
        query = str(snapshot.arrays["stop_names"][0])[:3]
        started = time.perf_counter()
        snapshot.search_stops(query)
        report["first_stop_search_ms"] = (time.perf_counter() - started) * 1_000

        if args.origin and args.destination:
            if args.revalidate_legacy_metadata:
                snapshot.heuristic_metadata = _geographic_heuristic_metadata(
                    snapshot.arrays
                )
            planner = SnapshotPlanner(snapshot)
            runs = {}
            for alternatives in (False, True):
                for algorithm in ("dijkstra", "astar"):
                    samples = [
                        timed_route(planner, args, algorithm, alternatives)
                        for _ in range(args.iterations)
                    ]
                    counters = samples[-1][1].diagnostics.counters
                    key = f"{algorithm}_{'alternatives' if alternatives else 'single'}"
                    runs[key] = {
                        "median_total_ms": statistics.median(
                            item[0] for item in samples
                        ),
                        "labels_pushed": counters.states_pushed,
                        "labels_popped": counters.states_popped,
                        "connections_examined": counters.connections_examined,
                        "transfer_records_examined": counters.transfer_edges_examined,
                        "heuristic_enabled": counters.geographic_heuristic_enabled,
                        "heuristic_values_computed": counters.heuristic_evaluations,
                        "heuristic_cache_hits": counters.heuristic_cache_hits,
                        "candidate_collection_complete": counters.candidate_collection_complete,
                        "candidate_truncated": counters.candidate_truncated,
                        "termination_reason": counters.termination_reason,
                    }
            report["runs"] = runs
        print(json.dumps(report, indent=2))
    finally:
        snapshot.close()


if __name__ == "__main__":
    main()
