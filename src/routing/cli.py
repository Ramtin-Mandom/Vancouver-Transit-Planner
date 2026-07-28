"""Command-line interface for scheduled transit routing."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from src.data_ingestion.config import ConfigurationError

from .models import Itinerary
from .planner import TransitPlanner


def parse_gtfs_time(value: str) -> timedelta:
    try:
        hours_text, minutes_text, seconds_text = value.split(":")
        hours, minutes, seconds = map(
            int, (hours_text, minutes_text, seconds_text)
        )
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("expected HH:MM:SS") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise argparse.ArgumentTypeError("expected a valid GTFS time (HH:MM:SS)")
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def format_gtfs_time(value: timedelta) -> str:
    total = int(value.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find an earliest scheduled journey.")
    parser.add_argument("--origin", required=True, help="origin stop_id")
    parser.add_argument("--destination", required=True, help="destination stop_id")
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--departure", required=True, type=parse_gtfs_time)
    parser.add_argument("--reliable", action="store_true")
    parser.add_argument("--alternatives", type=int, default=5)
    parser.add_argument("--simulations", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-extra-minutes", type=int, default=30)
    parser.add_argument("--minimum-samples", type=int, default=20)
    parser.add_argument("--search-timeout-seconds", type=float, default=30.0)
    return parser


def print_itinerary(itinerary: Itinerary) -> None:
    print(f"{itinerary.origin.stop_name} -> {itinerary.destination.stop_name}")
    for number, leg in enumerate(itinerary.legs, start=1):
        print(
            f"{number}. {leg.route_name} | trip {leg.trip_id} | "
            f"{leg.origin.stop_name} {format_gtfs_time(leg.departure_time)} -> "
            f"{leg.destination.stop_name} {format_gtfs_time(leg.arrival_time)}"
        )
    print(f"Transfers: {itinerary.transfer_count}")
    print(
        "Total scheduled travel time: "
        f"{format_gtfs_time(itinerary.total_scheduled_travel_time)}"
    )


def print_ranked_itineraries(ranked) -> None:
    for rank, item in enumerate(ranked, start=1):
        print(f"\nAlternative {rank} - reliability score {item.reliability_score:.1f}")
        print_itinerary(item.itinerary)
        result = item.simulation
        print(f"Completion probability: {result.completion_probability:.1%}")
        print(f"Schedule adherence: {item.schedule_adherence:.1%}")
        print(f"Speed ratio: {item.speed_ratio:.3f}")
        print(
            "Score components: "
            f"0.55*{result.completion_probability:.3f} + "
            f"0.25*{item.schedule_adherence:.3f} + "
            f"0.20*{item.speed_ratio:.3f}"
        )
        print(
            "On-time arrival probability (<= 10 min): "
            f"{result.on_time_arrival_probability:.1%}"
        )
        print(f"Expected arrival delay: {result.expected_arrival_delay_seconds:.0f}s")
        print(f"P90 arrival delay: {result.p90_arrival_delay_seconds:.0f}s")
        print("Profile fallback levels: " + ", ".join(result.fallback_levels))
        print(f"Insufficient data: {'yes' if result.insufficient_data else 'no'}")


def print_reliable_alternatives(result) -> None:
    for rank, item in enumerate(result.alternatives, start=1):
        print(f"\nAlternative {rank} - combined score {item.combined_score:.1f}")
        print_itinerary(item.itinerary)
        print(f"Scheduled arrival: {format_gtfs_time(item.itinerary.arrival_time)}")
        print(f"Route reliability: {item.route_reliability:.2%}")
        print(f"Speed component: {item.speed_component:.3f}")
        print("Score components: "
              f"0.80*{item.route_reliability:.3f} + "
              f"0.20*{item.speed_component:.3f}")
        print("Profile fallback levels: " + ", ".join(item.fallback_levels))
        print(f"Insufficient data: {'yes' if item.insufficient_data else 'no'}")
    timing = result.timing
    print(
        "\nSearch timing: "
        f"load {timing.data_loading_ms:.2f} ms, "
        f"search {timing.search_ms:.2f} ms, "
        f"ranking {timing.ranking_ms:.2f} ms, "
        f"total {timing.total_ms:.2f} ms"
    )


def main() -> int:
    args = build_parser().parse_args()
    try:
        # Keep PostgreSQL's native driver out of argument parsing and formatting
        # imports. Unit tests for these pure helpers do not need libpq.
        from .database import TransitDatabase

        routing_database = TransitDatabase()
        planner = TransitPlanner(routing_database)
        if args.reliable:
            from src.reliability.database import ReliabilityDatabase
            from src.reliability.profiles import ProfileResolver
            reliability_database = ReliabilityDatabase()
            resolver = ProfileResolver(
                reliability_database, args.minimum_samples
            )
            result = planner.plan_reliable_alternatives(
                args.origin,
                args.destination,
                args.date,
                args.departure,
                resolver,
                limit=args.alternatives,
                max_extra_minutes=args.max_extra_minutes,
                timeout_seconds=args.search_timeout_seconds,
            )
            if not result.alternatives:
                reliability_database.close()
                print("No scheduled route found.", file=sys.stderr)
                return 1
            print_reliable_alternatives(result)
            reliability_database.close()
            return 0
        itinerary = planner.plan(
            args.origin, args.destination, args.date, args.departure
        )
    except (ConfigurationError, ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"Routing failed: {exc}", file=sys.stderr)
        return 1
    if itinerary is None:
        print("No scheduled route found.", file=sys.stderr)
        return 1
    print_itinerary(itinerary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
