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
    return parser


def print_itinerary(itinerary: Itinerary) -> None:
    print(f"{itinerary.origin.stop_name} → {itinerary.destination.stop_name}")
    for number, leg in enumerate(itinerary.legs, start=1):
        print(
            f"{number}. {leg.route_name} | trip {leg.trip_id} | "
            f"{leg.origin.stop_name} {format_gtfs_time(leg.departure_time)} → "
            f"{leg.destination.stop_name} {format_gtfs_time(leg.arrival_time)}"
        )
    print(f"Transfers: {itinerary.transfer_count}")
    print(
        "Total scheduled travel time: "
        f"{format_gtfs_time(itinerary.total_scheduled_travel_time)}"
    )


def main() -> int:
    args = build_parser().parse_args()
    try:
        # Keep PostgreSQL's native driver out of argument parsing and formatting
        # imports. Unit tests for these pure helpers do not need libpq.
        from .database import TransitDatabase

        itinerary = TransitPlanner(TransitDatabase()).plan(
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
