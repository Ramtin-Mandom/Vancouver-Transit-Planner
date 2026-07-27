from datetime import timedelta

from src.routing.cli import format_gtfs_time, parse_gtfs_time


def test_gtfs_time_round_trip_above_24_hours():
    value = parse_gtfs_time("25:10:00")
    assert value == timedelta(hours=25, minutes=10)
    assert format_gtfs_time(value) == "25:10:00"
