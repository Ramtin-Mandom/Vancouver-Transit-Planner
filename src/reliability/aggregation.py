"""Reliability-profile aggregation orchestration."""

from __future__ import annotations

from time import perf_counter
from math import sqrt

from .models import AggregationSummary
from .classification import classify_delay


def summarize_delays(delays: list[int]) -> dict[str, float]:
    """Reference statistics used by tests and non-SQL repositories."""
    if not delays:
        raise ValueError("at least one delay is required")
    ordered = sorted(delays)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(len(ordered) - 1, lower + 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    average = sum(delays) / len(delays)
    stddev = (
        sqrt(sum((item - average) ** 2 for item in delays) / (len(delays) - 1))
        if len(delays) > 1
        else 0.0
    )
    return {
        "sample_count": float(len(delays)),
        "mean_delay_seconds": average,
        "mean_absolute_delay_seconds": (
            sum(abs(item) for item in delays) / len(delays)
        ),
        "delay_stddev_seconds": stddev,
        "p50_delay_seconds": percentile(0.5),
        "p90_delay_seconds": percentile(0.9),
        "early_probability": (
            sum(classify_delay(item) == "early" for item in delays) / len(delays)
        ),
        "on_time_probability": (
            sum(classify_delay(item) == "on-time" for item in delays) / len(delays)
        ),
        "late_probability": (
            sum(classify_delay(item) == "late" for item in delays) / len(delays)
        ),
    }


def rebuild_profiles(database, minimum_samples: int = 20) -> AggregationSummary:
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    started = perf_counter()
    observations, profiles, below_default = database.aggregate_profiles()
    # Database aggregation returns the count below the schema's documented
    # default. Repositories used in tests may implement the configurable count.
    below = (
        database.count_profiles_below(minimum_samples)
        if hasattr(database, "count_profiles_below")
        else below_default
    )
    return AggregationSummary(
        observations, profiles, below, perf_counter() - started
    )
