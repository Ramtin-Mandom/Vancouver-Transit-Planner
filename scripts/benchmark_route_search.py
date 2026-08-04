"""Read-only benchmark and normalized behavior comparison for reliable search."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter

from src.api.serializers import serialize_result
from src.reliability.database import ReliabilityDatabase
from src.reliability.profiles import ProfileResolver
from src.routing.database import TransitDatabase
from src.routing.planner import TransitPlanner


class LegacyTransitRepository:
    """Compatibility view used only to compare the pre-bulk query path."""

    _hidden = {
        "bulk_departures_in_window",
        "bulk_transfers",
        "bulk_trip_connections",
        "bulk_search_data",
    }

    def __init__(self, database: TransitDatabase) -> None:
        self._database = database

    def __getattr__(self, name):
        if name in self._hidden:
            raise AttributeError(name)
        return getattr(self._database, name)


def run(
    database,
    reliability,
    *,
    origin_id: str,
    destination_id: str,
    service_date: date,
    departure: timedelta,
    route_count: int,
    max_extra_minutes: int,
    timeout_seconds: float,
    mode: str = "frontier",
    diagnostics: bool = True,
):
    resolver = ProfileResolver(reliability)
    started = perf_counter()
    result = TransitPlanner(database).get_ranked_route_result(
        origin_id,
        destination_id,
        service_date,
        departure,
        resolver,
        route_number=route_count,
        max_extra_minutes=max_extra_minutes,
        timeout_seconds=timeout_seconds,
        include_diagnostics=diagnostics,
        trip_loading_mode=mode,
    )
    elapsed_ms = (perf_counter() - started) * 1000
    origin = database.find_stop(origin_id)
    destination = database.find_stop(destination_id)
    response = serialize_result(result, origin, destination, service_date, departure)
    payload = response.model_dump(mode="json")
    normalized = dict(payload)
    normalized.pop("timing", None)
    normalized.pop("diagnostics", None)
    return elapsed_ms, result, normalized


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="3334", help="origin stop ID")
    parser.add_argument("--destination", default="1875", help="destination stop ID")
    parser.add_argument("--service-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--departure-seconds", type=int, default=28_800)
    parser.add_argument("--route-count", type=int, default=5)
    parser.add_argument("--max-extra-minutes", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--compare-legacy", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    transit = TransitDatabase()
    reliability = ReliabilityDatabase()
    try:
        repositories = {
            "frontier": transit,
            "eager": transit,
            "legacy": LegacyTransitRepository(transit),
        }
        modes = (
            ("frontier", "eager", "legacy") if args.compare_legacy else ("frontier",)
        )
        orders = (
            ("frontier", "eager", "legacy"),
            ("legacy", "eager", "frontier"),
            ("eager", "frontier", "legacy"),
        )
        measurements = {mode: [] for mode in modes}
        latest = {}
        for trial in range(max(1, args.runs)):
            order = orders[trial % len(orders)] if args.compare_legacy else modes
            for mode in order:
                elapsed, result, normalized = run(
                    repositories[mode],
                    reliability,
                    origin_id=args.origin,
                    destination_id=args.destination,
                    service_date=args.service_date,
                    departure=timedelta(seconds=args.departure_seconds),
                    route_count=args.route_count,
                    max_extra_minutes=args.max_extra_minutes,
                    timeout_seconds=args.timeout_seconds,
                    mode="eager" if mode == "eager" else "frontier",
                )
                measurements[mode].append(elapsed)
                latest[mode] = (result, normalized)
        hashes = {
            mode: sha256(
                json.dumps(latest[mode][1], sort_keys=True).encode()
            ).hexdigest()
            for mode in modes
        }
        report = {
            "all_normalized_results_identical": len(set(hashes.values())) == 1,
            "modes": {},
        }
        for mode in modes:
            result, normalized = latest[mode]
            diagnostics = result.diagnostics
            report["modes"][mode] = {
                "runs_ms": measurements[mode],
                "median_total_ms": median(measurements[mode]),
                "normalized_sha256": hashes[mode],
                "alternatives": len(result.alternatives),
                "timings_ms": asdict(diagnostics.timings_ms) if diagnostics else None,
                "cache_statistics": asdict(diagnostics.cache_statistics)
                if diagnostics
                else None,
                "counters": asdict(diagnostics.counters) if diagnostics else None,
            }
        print(json.dumps(report, indent=2))
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            (args.output_dir / "benchmark-report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            for mode in modes:
                (args.output_dir / f"{mode}.normalized.json").write_text(
                    json.dumps(latest[mode][1], indent=2), encoding="utf-8"
                )
    finally:
        reliability.close()
        transit.close()


if __name__ == "__main__":
    main()
