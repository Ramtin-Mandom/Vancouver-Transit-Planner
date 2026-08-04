"""Deterministic Monte Carlo itinerary reliability simulation."""

from __future__ import annotations

import random
from statistics import mean

from .classification import (
    EARLY_THRESHOLD_SECONDS,
    FINAL_ARRIVAL_LATE_THRESHOLD_SECONDS,
)
from .models import SimulationResult

MIN_DELAY_SECONDS = -900
MAX_DELAY_SECONDS = 7200
INSUFFICIENT_MEAN_SECONDS = 600.0
INSUFFICIENT_STDDEV_SECONDS = 600.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def simulate_itinerary(
    itinerary,
    resolver,
    *,
    simulations: int = 1000,
    seed: int = 42,
) -> SimulationResult:
    if simulations < 1:
        raise ValueError("simulations must be positive")
    rng = random.Random(seed)
    selections = []
    for leg in itinerary.legs:
        selection = resolver.resolve(leg.route_id, leg.direction_id, leg.arrival_time)
        selections.append(selection)
    schedule_adherence = (
        mean(
            item.profile.on_time_probability if item.profile is not None else 0.0
            for item in selections
        )
        if selections
        else 0.0
    )

    completions = 0
    final_delays: list[float] = []
    for _ in range(simulations):
        sampled: list[float] = []
        for selection in selections:
            if selection.profile is None:
                average = INSUFFICIENT_MEAN_SECONDS
                stddev = INSUFFICIENT_STDDEV_SECONDS
            else:
                average = selection.profile.mean_delay_seconds
                stddev = selection.profile.delay_stddev_seconds or 0.0
            delay = average if stddev == 0 else rng.normalvariate(average, stddev)
            sampled.append(min(MAX_DELAY_SECONDS, max(MIN_DELAY_SECONDS, delay)))
        completed = True
        for index in range(len(itinerary.legs) - 1):
            transfer_seconds = (
                itinerary.legs[index + 1].departure_time
                - itinerary.legs[index].arrival_time
            ).total_seconds()
            if sampled[index] > transfer_seconds:
                completed = False
                break
        if completed:
            completions += 1
            final_delays.append(sampled[-1] if sampled else 0.0)

    completion = completions / simulations
    on_time = (
        sum(
            EARLY_THRESHOLD_SECONDS <= delay <= FINAL_ARRIVAL_LATE_THRESHOLD_SECONDS
            for delay in final_delays
        )
        / simulations
        if final_delays
        else 0.0
    )
    return SimulationResult(
        completion_probability=completion,
        schedule_adherence=schedule_adherence,
        on_time_arrival_probability=on_time,
        expected_arrival_delay_seconds=mean(final_delays) if final_delays else 0.0,
        p90_arrival_delay_seconds=_percentile(final_delays, 0.9),
        insufficient_data=any(item.insufficient_data for item in selections),
        fallback_levels=tuple(item.fallback_level for item in selections),
    )
