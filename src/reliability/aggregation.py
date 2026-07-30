"""Reliability-profile aggregation orchestration."""

from __future__ import annotations

from time import perf_counter
from math import sqrt

from .models import AggregationSummary
from .classification import classify_delay
from .policy import DEFAULT_MINIMUM_SAMPLES


def summarize_delays(delays: list[int]) -> dict[str, float]:
    """Reference statistics used by tests and non-SQL repositories."""
    if not delays:
        raise ValueError("at least one delay is required")
    ordered = sorted(delays)

    def percentile(fraction: float, values=ordered) -> float:
        position = (len(values) - 1) * fraction
        lower = int(position)
        upper = min(len(values) - 1, lower + 1)
        weight = position - lower
        return values[lower] * (1 - weight) + values[upper] * weight

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
        "p90_absolute_delay_seconds": percentile(
            0.9, sorted(abs(item) for item in delays)
        ),
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


def rebuild_profiles(
    database,
    minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
    *,
    full_rebuild: bool = False,
    early_threshold: int = -120,
    late_threshold: int = 300,
    shrinkage_strength: float = 20.0,
) -> AggregationSummary:
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    if early_threshold >= late_threshold:
        raise ValueError("early threshold must be less than late threshold")
    if shrinkage_strength < 0:
        raise ValueError("shrinkage strength cannot be negative")
    started = perf_counter()
    try:
        result = database.aggregate_profiles(
            full_rebuild=full_rebuild,
            early_threshold=early_threshold,
            late_threshold=late_threshold,
            shrinkage_strength=shrinkage_strength,
            minimum_samples=minimum_samples,
        )
    except TypeError:
        # Keeps small repository fakes and third-party adapters compatible.
        result = database.aggregate_profiles()
    if len(result) == 4:
        observations, samples, profiles, below_default = result
    else:
        observations, profiles, below_default = result
        samples = observations
    # Database aggregation returns the count below the schema's documented
    # default. Repositories used in tests may implement the configurable count.
    below = (
        database.count_profiles_below(minimum_samples)
        if hasattr(database, "count_profiles_below")
        else below_default
    )
    return AggregationSummary(
        observations, profiles, below, perf_counter() - started, samples
    )
