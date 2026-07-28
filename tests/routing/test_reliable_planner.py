from datetime import timedelta
import time

import pytest

from src.reliability.models import ProfileSelection, ReliabilityProfile
from src.routing.planner import TransitPlanner
from src.routing.reliable import ReliableSearchTimeout
from tests.routing.test_planner import (
    FakeDatabase,
    MONDAY,
    at,
    connection,
    stops,
)


def profile(probability):
    return ReliabilityProfile(
        None, None, None, None, 100, 0, 0, 0, 0, 0,
        0, probability, 1 - probability,
    )


class Resolver:
    def __init__(self, probabilities, fallback=0.5):
        self.probabilities = probabilities
        self.fallback = fallback
        self.calls = []

    def resolve(self, route_id, stop_id, weekday, hour):
        key = (route_id, stop_id)
        self.calls.append(key)
        probability = self.probabilities.get(key)
        if probability is None:
            return ProfileSelection(None, "insufficient-data", True)
        return ProfileSelection(profile(probability), "exact", False)


def test_two_leg_reliability_is_multiplied():
    trips = {
        "T1": [connection("T1", "weekday", "R1", "A", "B", at(8), at(8, 10))],
        "T2": [connection("T2", "weekday", "R2", "B", "C", at(8, 12), at(8, 20))],
    }
    result = TransitPlanner(
        FakeDatabase(stops("A", "B", "C"), trips)
    ).plan_reliable_alternatives(
        "A", "C", MONDAY, at(7, 55),
        Resolver({("R1", "B"): .75, ("R2", "C"): .75}),
    )
    assert result.alternatives[0].route_reliability == pytest.approx(.5625)


def test_three_probabilities_are_multiplied_not_averaged():
    trips = {
        "T1": [connection("T1", "weekday", "R1", "A", "B", at(8), at(8, 5))],
        "T2": [connection("T2", "weekday", "R2", "B", "C", at(8, 6), at(8, 11))],
        "T3": [connection("T3", "weekday", "R3", "C", "D", at(8, 12), at(8, 17))],
    }
    result = TransitPlanner(
        FakeDatabase(stops("A", "B", "C", "D"), trips)
    ).plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59),
        Resolver({("R1", "B"): .8, ("R2", "C"): .7, ("R3", "D"): .9}),
    )
    assert result.alternatives[0].route_reliability == pytest.approx(.504)


def test_slower_reliable_path_survives_and_ranks_first():
    trips = {
        "FAST": [connection("FAST", "weekday", "RF", "A", "D", at(8), at(8, 10))],
        "SLOW": [connection("SLOW", "weekday", "RS", "A", "D", at(8), at(8, 12))],
    }
    result = TransitPlanner(
        FakeDatabase(stops("A", "D"), trips)
    ).plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59),
        Resolver({("RF", "D"): .2, ("RS", "D"): .95}),
    )
    assert [x.itinerary.legs[0].trip_id for x in result.alternatives] == [
        "SLOW", "FAST"
    ]


def test_dominated_label_is_removed_and_bounds_are_respected():
    trips = {
        "BEST": [connection("BEST", "weekday", "RB", "A", "D", at(8), at(8, 10))],
        "DOMINATED": [
            connection("DOMINATED", "weekday", "RD", "A", "D", at(8), at(8, 20))
        ],
        "LATE": [connection("LATE", "weekday", "RL", "A", "D", at(10), at(10, 10))],
    }
    result = TransitPlanner(
        FakeDatabase(stops("A", "D"), trips)
    ).plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59),
        Resolver({("RB", "D"): .9, ("RD", "D"): .8, ("RL", "D"): 1}),
        search_horizon_minutes=60,
    )
    assert [x.itinerary.legs[0].trip_id for x in result.alternatives] == ["BEST"]


def test_missing_profile_uses_conservative_fallback_and_is_marked():
    trip = {
        "T": [connection("T", "weekday", "R", "A", "D", at(8), at(8, 10))]
    }
    result = TransitPlanner(
        FakeDatabase(stops("A", "D"), trip)
    ).plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), Resolver({})
    )
    item = result.alternatives[0]
    assert item.insufficient_data
    assert item.route_reliability == pytest.approx(1e-9)


def test_profile_lookups_are_request_cached():
    trips = {
        "T1": [connection("T1", "weekday", "R", "A", "D", at(8), at(8, 10))],
        "T2": [connection("T2", "weekday", "R", "A", "D", at(8, 1), at(8, 10))],
    }
    resolver = Resolver({("R", "D"): .9})
    TransitPlanner(FakeDatabase(stops("A", "D"), trips)).plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), resolver
    )
    assert resolver.calls.count(("R", "D")) == 1


def test_returns_five_structurally_distinct_deterministic_alternatives():
    trips = {
        f"T{number}": [
            connection(
                f"T{number}", "weekday", f"R{number}", "A", "D",
                at(8, number), at(8, 10 + number),
            )
        ]
        for number in range(5)
    }
    probabilities = {
        (f"R{number}", "D"): 0.50 + number * 0.1 for number in range(5)
    }
    planner = TransitPlanner(FakeDatabase(stops("A", "D"), trips))
    first = planner.plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), Resolver(probabilities)
    )
    second = planner.plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), Resolver(probabilities)
    )
    first_ids = [item.itinerary.legs[0].trip_id for item in first.alternatives]
    second_ids = [item.itinerary.legs[0].trip_id for item in second.alternatives]
    assert len(first_ids) == len(set(first_ids)) == 5
    assert first_ids == second_ids
    assert first.timing.search_ms < 1000


def test_search_timeout_is_enforced_during_profile_resolution():
    class SlowResolver(Resolver):
        def resolve(self, *args):
            time.sleep(0.01)
            return super().resolve(*args)

    trip = {
        "T": [connection("T", "weekday", "R", "A", "D", at(8), at(8, 10))]
    }
    planner = TransitPlanner(FakeDatabase(stops("A", "D"), trip))
    with pytest.raises(ReliableSearchTimeout, match="exceeded"):
        planner.plan_reliable_alternatives(
            "A", "D", MONDAY, at(7, 59),
            SlowResolver({("R", "D"): .9}),
            timeout_seconds=.001,
        )
