"""CLI for realtime collection, aggregation, and profile reports."""

from __future__ import annotations

import argparse
import sys

from src.data_ingestion.config import ConfigurationError

from .aggregation import rebuild_profiles
from .client import RealtimeClient, RealtimeDownloadError
from .collector import collect_snapshot
from .config import ReliabilityConfig
from .database import ReliabilityDatabase
from .profiles import ProfileResolver


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transit reliability data tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect", help="collect one GTFS-Realtime snapshot")
    aggregate = subparsers.add_parser("aggregate", help="rebuild route profiles")
    aggregate.add_argument("--minimum-samples", type=int, default=20)
    report = subparsers.add_parser("report", help="show a reliability profile")
    report.add_argument("--route-id", required=True)
    report.add_argument("--stop-id")
    report.add_argument("--weekday", type=int, choices=range(7))
    report.add_argument("--hour", type=int, choices=range(24))
    report.add_argument("--minimum-samples", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        database = ReliabilityDatabase()
        if args.command == "collect":
            config = ReliabilityConfig.from_environment()
            summary = collect_snapshot(RealtimeClient(config), database)
            print(f"Feed timestamp: {summary.feed_timestamp.isoformat()}")
            print(f"Trip updates processed: {summary.trip_updates_processed}")
            print(f"Stop updates processed: {summary.stop_updates_processed}")
            print(f"Observations inserted: {summary.inserted}")
            print(f"Duplicates skipped: {summary.duplicates}")
            print(f"Malformed updates skipped: {summary.malformed}")
            print(f"Unknown trips/stops skipped: {summary.unknown}")
            print(f"Updates without usable delay: {summary.unusable_delay}")
        elif args.command == "aggregate":
            summary = rebuild_profiles(database, args.minimum_samples)
            print(f"Unique observations used: {summary.observations_used}")
            print(f"Profiles inserted or updated: {summary.profiles_upserted}")
            print(f"Profiles below minimum: {summary.profiles_below_minimum}")
            print(f"Total execution time: {summary.elapsed_seconds:.3f}s")
        else:
            selection = ProfileResolver(
                database, args.minimum_samples
            ).resolve(args.route_id, args.stop_id, args.weekday, args.hour)
            print(f"Fallback level: {selection.fallback_level}")
            if selection.profile is None:
                print("Insufficient reliability data.")
                return 1
            profile = selection.profile
            print(f"Samples: {profile.sample_count}")
            print(f"Signed average delay: {profile.mean_delay_seconds:.1f}s")
            print(
                "Mean absolute delay: "
                f"{profile.mean_absolute_delay_seconds:.1f}s"
            )
            print(f"P50 delay: {profile.p50_delay_seconds:.1f}s")
            print(f"P90 delay: {profile.p90_delay_seconds:.1f}s")
            print(f"Early probability (< -60s): {profile.early_probability:.1%}")
            print(
                "On-time probability (-60s to 300s): "
                f"{profile.on_time_probability:.1%}"
            )
            print(f"Late probability (> 300s): {profile.late_probability:.1%}")
    except (
        ConfigurationError,
        RealtimeDownloadError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Reliability command failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
