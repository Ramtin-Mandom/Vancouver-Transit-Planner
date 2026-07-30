"""CLI for realtime collection, aggregation, and profile reports."""

from __future__ import annotations

import argparse
import os
import sys

from src.data_ingestion.config import ConfigurationError

from .aggregation import rebuild_profiles
from .classification import (
    EARLY_THRESHOLD_SECONDS,
    LATE_THRESHOLD_SECONDS,
    SHRINKAGE_STRENGTH,
)
from .client import RealtimeClient, RealtimeDownloadError
from .collector import collect_snapshot
from .config import ReliabilityConfig
from .database import ReliabilityDatabase
from .profiles import ProfileResolver
from .policy import DEFAULT_MINIMUM_SAMPLES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transit reliability data tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect", help="collect one GTFS-Realtime snapshot")
    aggregate = subparsers.add_parser("aggregate", help="update route profiles")
    aggregate.add_argument(
        "--minimum-samples", type=int, default=DEFAULT_MINIMUM_SAMPLES
    )
    aggregate.add_argument(
        "--full-rebuild", action="store_true",
        help="recreate all derived samples and profiles from append-only raw data",
    )
    report = subparsers.add_parser("report", help="show a reliability profile")
    report.add_argument("--route-id", required=True)
    report.add_argument("--direction-id", type=int, choices=(0, 1))
    report.add_argument("--time-window", required=True, choices=(
        "overnight", "morning_peak", "midday", "afternoon_peak", "evening",
    ))
    report.add_argument(
        "--minimum-samples", type=int, default=DEFAULT_MINIMUM_SAMPLES
    )
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
            summary = rebuild_profiles(
                database, args.minimum_samples,
                full_rebuild=args.full_rebuild,
                early_threshold=int(os.getenv(
                    "RELIABILITY_EARLY_SECONDS",
                    str(EARLY_THRESHOLD_SECONDS),
                )),
                late_threshold=int(os.getenv(
                    "RELIABILITY_LATE_SECONDS",
                    str(LATE_THRESHOLD_SECONDS),
                )),
                shrinkage_strength=float(
                    os.getenv(
                        "RELIABILITY_SHRINKAGE_STRENGTH",
                        str(SHRINKAGE_STRENGTH),
                    )
                ),
            )
            print(f"Unique observations used: {summary.observations_used}")
            print(f"Representative trip samples: {summary.samples_used}")
            print(f"Profiles inserted or updated: {summary.profiles_upserted}")
            print(f"Profiles below minimum: {summary.profiles_below_minimum}")
            print(f"Total execution time: {summary.elapsed_seconds:.3f}s")
        else:
            selection = ProfileResolver(
                database, args.minimum_samples
            ).resolve(args.route_id, args.direction_id, args.time_window)
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
            print(
                "P90 absolute delay: "
                f"{profile.p90_absolute_delay_seconds:.1f}s"
            )
            print(f"Early probability (< -120s): {profile.early_probability:.1%}")
            print(
                "On-time probability (-120s to 300s): "
                f"{profile.on_time_probability:.1%}"
            )
            print(f"Late probability (> 300s): {profile.late_probability:.1%}")
            print(
                "Adjusted reliability probability: "
                f"{profile.reliability_probability:.1%}"
            )
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
