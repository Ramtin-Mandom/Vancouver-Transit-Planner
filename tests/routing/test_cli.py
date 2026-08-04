from datetime import timedelta

from src.routing.cli import build_parser, format_gtfs_time, parse_gtfs_time


def test_gtfs_time_round_trip_above_24_hours():
    value = parse_gtfs_time("25:10:00")
    assert value == timedelta(hours=25, minutes=10)
    assert format_gtfs_time(value) == "25:10:00"


def test_reliable_options_are_opt_in_and_scheduled_cli_is_compatible():
    args = build_parser().parse_args(
        [
            "--origin",
            "A",
            "--destination",
            "B",
            "--date",
            "2026-07-27",
            "--departure",
            "08:00:00",
        ]
    )
    assert not args.reliable
    assert args.alternatives == 5
    assert args.search_timeout_seconds == 30.0
    assert args.reliability_effect == 0.5
    assert args.travel_time_effect == 0.5
    assert args.transfer_effect == 0.0
