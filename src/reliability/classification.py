"""Shared reliability policy.

Python is the application source of truth; migration 003 creates the matching
SQL ``transit.reliability_time_window(interval)`` function used by aggregation.
"""

from datetime import timedelta

EARLY_THRESHOLD_SECONDS = -120
LATE_THRESHOLD_SECONDS = 300
FINAL_ARRIVAL_LATE_THRESHOLD_SECONDS = 600
SHRINKAGE_STRENGTH = 20.0

TIME_WINDOWS = (
    ("overnight", 0, 6),
    ("morning_peak", 6, 10),
    ("midday", 10, 15),
    ("afternoon_peak", 15, 19),
    ("evening", 19, 24),
)


def time_window(value: timedelta) -> str:
    """Map a GTFS service-day time to a window, normalizing hours >= 24."""
    hour = int(value.total_seconds() // 3600) % 24
    return next(name for name, start, end in TIME_WINDOWS if start <= hour < end)


def classify_delay(delay_seconds: float) -> str:
    if delay_seconds < EARLY_THRESHOLD_SECONDS:
        return "early"
    if delay_seconds > LATE_THRESHOLD_SECONDS:
        return "late"
    return "on-time"
