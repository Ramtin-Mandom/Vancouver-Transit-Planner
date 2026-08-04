from datetime import timedelta
import time

import pytest

from src.reliability.models import ProfileSelection, ReliabilityProfile
from src.routing.planner import TransitPlanner
from src.routing.cache import CacheConfiguration, RoutingCacheManager
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

    def resolve(self, route_id, direction_id, scheduled_time):
        key = next(
            (candidate for candidate in self.probabilities if candidate[0] == route_id),
            (route_id, None),
        )
        self.calls.append(key)
        probability = self.probabilities.get(key)
        if probability is None:
            return ProfileSelection(None, "insufficient-data", True)
        return ProfileSelection(profile(probability), "exact", False)


def test_response_cache_distinguishes_alternative_mode():
    trips = {
        "T": [connection("T", "weekday", "R", "A", "D", at(8), at(8, 10))]
    }
    cache = RoutingCacheManager(CacheConfiguration())
    planner = TransitPlanner(
        FakeDatabase(stops("A", "D"), trips), cache_manager=cache
    )
    resolver = Resolver({("R", "D"): .9})
    common = dict(
        algorithm="dijkstra", cache_mode="shared", include_diagnostics=True,
    )
    planner.get_ranked_route_result(
        "A", "D", MONDAY, at(7, 59), resolver,
        route_number=1, include_alternatives=False, **common,
    )
    planner.get_ranked_route_result(
        "A", "D", MONDAY, at(7, 59), resolver,
        route_number=3, include_alternatives=True, **common,
    )
    assert cache.responses.statistics().entries == 2


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


def test_get_ranked_routes_limits_without_extra_profile_lookups():
    trips = {
        f"T{number}": [
            connection(
                f"T{number}", "weekday", f"R{number}", "A", "D",
                at(8, number), at(8, 10 + number),
            )
        ]
        for number in range(4)
    }
    resolver = Resolver({
        (f"R{number}", "D"): .6 + number * .1 for number in range(4)
    })
    planner = TransitPlanner(FakeDatabase(stops("A", "D"), trips))
    routes = planner.get_ranked_routes(
        "A", "D", MONDAY, at(7, 59), resolver, route_number=3
    )
    assert len(routes) == 3
    assert len(resolver.calls) == 4


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


def test_profiled_search_reports_consistent_diagnostics_without_changing_routes():
    trips = {
        "T1": [connection("T1", "weekday", "R", "A", "D", at(8), at(8, 10))],
        "T2": [connection("T2", "weekday", "R", "A", "D", at(8, 1), at(8, 10))],
    }
    planner = TransitPlanner(FakeDatabase(stops("A", "D"), trips))
    plain = planner.plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), Resolver({("R", "D"): .9}),
        algorithm="baseline",
    )
    profiled = planner.plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), Resolver({("R", "D"): .9}),
        include_diagnostics=True, algorithm="baseline",
    )
    assert [item.itinerary for item in plain.alternatives] == [
        item.itinerary for item in profiled.alternatives
    ]
    diagnostics = profiled.diagnostics
    assert diagnostics is not None
    assert all(value >= 0 for value in vars(diagnostics.timings_ms).values())
    cache = diagnostics.cache_statistics
    assert cache.departure_query_count == cache.departure_cache_misses
    assert cache.trip_query_count == cache.trip_cache_misses
    assert cache.transfer_query_count == cache.transfer_cache_misses
    assert cache.profile_resolver_calls == cache.profile_cache_misses
    assert cache.profile_cache_hits >= 1
    counters = diagnostics.counters
    assert counters.queue_pops <= counters.queue_pushes
    assert counters.labels_accepted <= counters.labels_created
    assert counters.boardable_departures <= counters.departures_examined
    assert counters.connections_examined >= counters.trips_examined


def test_timeout_carries_partial_diagnostics():
    class SlowResolver(Resolver):
        def resolve(self, *args):
            time.sleep(0.01)
            return super().resolve(*args)

    trips = {
        "T": [connection("T", "weekday", "R", "A", "D", at(8), at(8, 10))]
    }
    with pytest.raises(ReliableSearchTimeout) as caught:
        TransitPlanner(FakeDatabase(stops("A", "D"), trips)).plan_reliable_alternatives(
            "A", "D", MONDAY, at(7, 59), SlowResolver({("R", "D"): .9}),
            timeout_seconds=.001, include_diagnostics=True, algorithm="baseline",
        )
    assert caught.value.diagnostics is not None
    assert caught.value.diagnostics.timings_ms.measured_search_ms >= 0
    assert caught.value.diagnostics.cache_statistics.profile_resolver_calls == 1


def test_bulk_index_path_executes_no_hot_loop_repository_queries():
    trips = {
        "T": [connection("T", "weekday", "R", "A", "D", at(8), at(8, 10))]
    }

    class BulkDatabase(FakeDatabase):
        def __init__(self):
            super().__init__(stops("A", "D"), trips)
            self.bulk_counts = {"departures": 0, "transfers": 0, "trips": 0}

        def bulk_departures_in_window(self, earliest, latest, service_ids=None):
            self.bulk_counts["departures"] += 1
            return [item for values in self.trips.values() for item in values]

        def bulk_transfers(self):
            self.bulk_counts["transfers"] += 1
            return []

        def bulk_trip_connections(self, trip_ids, *, batch_size=2000):
            self.bulk_counts["trips"] += 1
            return [item for trip_id in sorted(trip_ids) for item in self.trips[trip_id]]

        def departures_from(self, *args, **kwargs):
            raise AssertionError("hot-loop departure query")

        def trip_connections(self, *args, **kwargs):
            raise AssertionError("hot-loop trip query")

        def transfers_from(self, *args, **kwargs):
            raise AssertionError("hot-loop transfer query")

    database = BulkDatabase()
    result = TransitPlanner(database).plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), Resolver({("R", "D"): .9}),
        include_diagnostics=True, algorithm="baseline",
    )
    assert database.bulk_counts == {"departures": 1, "transfers": 1, "trips": 1}
    assert result.diagnostics.cache_statistics.unexpected_queries_during_search == 0
    assert result.alternatives[0].itinerary.legs[0].trip_id == "T"


def test_nonboardable_departure_does_not_trigger_frontier_trip_loading():
    trips = {
        "PAST": [
            connection("PAST", "weekday", "R", "A", "D", at(7, 58), at(8, 8))
        ]
    }

    class BulkDatabase(FakeDatabase):
        trip_batches = 0

        def bulk_departures_in_window(self, earliest, latest, service_ids=None):
            return list(self.trips["PAST"])

        def bulk_transfers(self):
            return []

        def bulk_trip_connections(self, trip_ids, *, batch_size=2000):
            self.trip_batches += 1
            return list(self.trips["PAST"])

    database = BulkDatabase(stops("A", "D"), trips)
    result = TransitPlanner(database).plan_reliable_alternatives(
        "A", "D", MONDAY, at(7, 59), Resolver({("R", "D"): .9}),
        include_diagnostics=True, algorithm="baseline",
    )
    assert not result.alternatives
    assert database.trip_batches == 0
    assert result.diagnostics.counters.unique_frontier_trips_requested == 0


def test_timeout_diagnostics_include_completed_frontier_batch():
    trips = {
        "T": [connection("T", "weekday", "R", "A", "D", at(8), at(8, 10))]
    }

    class SlowBulkDatabase(FakeDatabase):
        def bulk_departures_in_window(self, earliest, latest, service_ids=None):
            return list(self.trips["T"])

        def bulk_transfers(self):
            return []

        def bulk_trip_connections(self, trip_ids, *, batch_size=2000):
            time.sleep(0.01)
            return []

    with pytest.raises(ReliableSearchTimeout) as caught:
        TransitPlanner(SlowBulkDatabase(stops("A", "D"), trips)).plan_reliable_alternatives(
            "A", "D", MONDAY, at(7, 59), Resolver({("R", "D"): .9}),
            timeout_seconds=.001, include_diagnostics=True, algorithm="baseline",
        )
    diagnostics = caught.value.diagnostics
    assert diagnostics.counters.frontier_trip_batch_query_count == 1
    assert diagnostics.counters.unique_frontier_trips_requested == 1
