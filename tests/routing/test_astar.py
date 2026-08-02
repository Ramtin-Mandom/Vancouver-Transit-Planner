from datetime import timedelta
from decimal import Decimal

import pytest

from src.routing.astar import (
    AStarParetoTransitSearch,
    haversine_distance_km,
    heuristic_seconds,
)
from src.routing.models import Stop
from src.routing.planner import TransitPlanner
from src.routing.reliable import _Label, _dominates
from tests.routing.test_planner import FakeDatabase, MONDAY, at, connection
from tests.routing.test_reliable_planner import Resolver


def located_stop(identifier, latitude, longitude):
    return Stop(
        identifier,
        f"Stop {identifier}",
        stop_lat=Decimal(str(latitude)) if latitude is not None else None,
        stop_lon=Decimal(str(longitude)) if longitude is not None else None,
    )


class CoordinateDatabase(FakeDatabase):
    def __init__(self, stops, trips, transfers=None):
        super().__init__(stops, trips, transfers=transfers)
        self.coordinate_queries = 0

    def find_stops(self, stop_ids):
        self.coordinate_queries += 1
        return {
            stop_id: self.stops[stop_id]
            for stop_id in stop_ids
            if stop_id in self.stops
        }

    def all_stop_coordinates(self):
        self.coordinate_queries += 1
        return dict(self.stops)


def signature(result):
    return [
        (
            tuple(
                (
                    leg.trip_id,
                    leg.route_id,
                    leg.origin.stop_id,
                    leg.destination.stop_id,
                    leg.departure_time,
                    leg.arrival_time,
                )
                for leg in alternative.itinerary.legs
            ),
            alternative.itinerary.transfer_count,
            alternative.route_reliability,
        )
        for alternative in result.alternatives
    ]


def test_haversine_same_and_known_coordinates():
    assert haversine_distance_km(49.2827, -123.1207, 49.2827, -123.1207) == pytest.approx(0)
    # Vancouver to Victoria is roughly 97 km great-circle.
    assert haversine_distance_km(49.2827, -123.1207, 48.4284, -123.3656) == pytest.approx(97, abs=2)


def test_heuristic_converts_distance_at_120_kmh_and_handles_bad_coordinates():
    origin = located_stop("A", 0, 0)
    one_degree_east = located_stop("B", 0, 1)
    distance = haversine_distance_km(0, 0, 0, 1)
    assert heuristic_seconds(origin, one_degree_east) == pytest.approx(distance / 120 * 3600)
    assert heuristic_seconds(located_stop("M", None, None), one_degree_east) == 0
    assert heuristic_seconds(located_stop("I", 999, "NaN"), one_degree_east) == 0


def test_request_cache_and_queue_priority_are_arrival_plus_heuristic():
    database = CoordinateDatabase(
        [located_stop("A", 49.28, -123.12), located_stop("D", 49.25, -123.0)],
        {},
    )
    search = AStarParetoTransitSearch(database, None, None, max_speed_kmh=120)
    search._prepare_queue_ordering(database.stops["A"], database.stops["D"], {"A", "D"})
    label = _Label("A", at(8), 0, 0, None, (), (), frozenset({"A"}))
    expected = at(8) + timedelta(seconds=heuristic_seconds(database.stops["A"], database.stops["D"]))
    assert search._queue_priority(label) == expected
    assert search._queue_priority(label) == expected
    assert search._queue_ordering_statistics() == (1, 1)
    assert database.coordinate_queries == 2


def test_heuristic_never_participates_in_dominance():
    earlier = _Label("A", at(8), 0.1, 0, None, (), (), frozenset({"A"}))
    later = _Label("A", at(8, 1), 0.2, 1, None, (), (), frozenset({"A"}))
    assert _dominates(earlier, later)


def test_baseline_and_astar_return_identical_alternatives_and_order():
    trips = {
        "FAST": [connection("FAST", "weekday", "RF", "A", "D", at(8), at(8, 10))],
        "SLOW": [connection("SLOW", "weekday", "RS", "A", "D", at(8, 1), at(8, 15))],
    }
    stops = [located_stop("A", 49.28, -123.12), located_stop("D", 49.25, -123.0)]
    planner = TransitPlanner(CoordinateDatabase(stops, trips))
    probabilities = {("RF", "D"): 0.7, ("RS", "D"): 0.95}
    baseline = planner.get_ranked_route_result(
        "A", "D", MONDAY, at(7, 59), Resolver(probabilities),
        algorithm="baseline", include_diagnostics=True,
    )
    astar = planner.get_ranked_route_result(
        "A", "D", MONDAY, at(7, 59), Resolver(probabilities),
        algorithm="astar", include_diagnostics=True,
    )
    assert signature(astar) == signature(baseline)
    assert baseline.diagnostics.counters.algorithm == "baseline"
    assert astar.diagnostics.counters.algorithm == "astar"
    assert astar.diagnostics.counters.unique_heuristic_calculations == 2


def test_astar_termination_keeps_allowed_destinations_and_skips_hopeless_label():
    trips = {
        "FAST": [connection("FAST", "weekday", "RF", "A", "D", at(8), at(8, 10))],
        "ALT": [connection("ALT", "weekday", "RA", "A", "D", at(8, 2), at(8, 20))],
        "AWAY": [connection("AWAY", "weekday", "RX", "A", "X", at(8), at(8, 11))],
    }
    database = CoordinateDatabase(
        [
            located_stop("A", 49.28, -123.12),
            located_stop("D", 49.25, -123.0),
            located_stop("X", 48.0, -125.0),
        ],
        trips,
    )
    result = TransitPlanner(database).get_ranked_route_result(
        "A", "D", MONDAY, at(7, 59),
        Resolver({("RF", "D"): .8, ("RA", "D"): .9, ("RX", "X"): .9}),
        algorithm="astar", max_extra_minutes=15, include_diagnostics=True,
    )
    assert {item.itinerary.legs[0].trip_id for item in result.alternatives} == {"FAST", "ALT"}
    # Origin expands, but the geographically hopeless X label does not.
    assert result.diagnostics.counters.stops_expanded == 1


def test_invalid_algorithm_is_rejected():
    planner = TransitPlanner(CoordinateDatabase([located_stop("A", 0, 0)], {}))
    with pytest.raises(ValueError, match="routing algorithm"):
        planner.get_ranked_route_result(
            "A", "A", MONDAY, at(8), Resolver({}), algorithm="greedy"
        )


def test_astar_preserves_overnight_times_and_is_deterministic():
    trips = {
        "NIGHT": [
            connection("NIGHT", "weekday", "RN", "A", "D", at(25, 5), at(25, 25))
        ]
    }
    database = CoordinateDatabase(
        [located_stop("A", 49.28, -123.12), located_stop("D", 49.25, -123.0)],
        trips,
    )
    planner = TransitPlanner(database)
    runs = [
        planner.get_ranked_route_result(
            "A", "D", MONDAY, at(25), Resolver({("RN", "D"): .9}),
            algorithm="astar",
        )
        for _ in range(2)
    ]
    assert runs[0].alternatives[0].itinerary.arrival_time == at(25, 25)
    assert signature(runs[0]) == signature(runs[1])
