from datetime import timedelta

from src.reliability.classification import time_window


def test_time_window_boundaries_and_gtfs_overflow():
    assert time_window(timedelta(hours=5, minutes=59)) == "overnight"
    assert time_window(timedelta(hours=6)) == "morning_peak"
    assert time_window(timedelta(hours=10)) == "midday"
    assert time_window(timedelta(hours=15)) == "afternoon_peak"
    assert time_window(timedelta(hours=19)) == "evening"
    assert time_window(timedelta(hours=25)) == "overnight"
