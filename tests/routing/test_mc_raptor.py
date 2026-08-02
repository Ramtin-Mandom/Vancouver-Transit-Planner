from datetime import timedelta

import pytest

from src.routing.mcraptor_index import McRaptorIndex
from src.routing.planner import TransitPlanner
from tests.routing.test_planner import FakeDatabase, MONDAY, at, connection, stops
from tests.routing.test_reliable_planner import Resolver


def run(database, probabilities, **bounds):
    return TransitPlanner(database).get_ranked_route_result(
        "A", "D", MONDAY, bounds.pop("departure", at(7, 59)),
        Resolver(probabilities), algorithm="mc_raptor", **bounds,
    )


def trip_ids(result):
    return [
        tuple(leg.trip_id for leg in item.itinerary.legs)
        for item in result.alternatives
    ]


def test_direct_trip_uses_first_round_and_default_dispatch_is_mc_raptor():
    trips = {"T": [connection("T", "weekday", "R", "A", "D", at(8), at(8, 10))]}
    planner = TransitPlanner(FakeDatabase(stops("A", "D"), trips))
    result = planner.get_ranked_route_result(
        "A", "D", MONDAY, at(7, 59), Resolver({("R", "D"): .9}),
        include_diagnostics=True,
    )
    assert trip_ids(result) == [("T",)]
    assert result.diagnostics.counters.algorithm == "mc_raptor"
    assert result.alternatives[0].itinerary.transfer_count == 0
    assert result.diagnostics.counters.rounds_executed == 1


def test_one_and_two_transfer_journeys_use_two_and_three_boardings():
    trips = {
        "T1": [connection("T1", "weekday", "R1", "A", "B", at(8), at(8, 5))],
        "T2": [connection("T2", "weekday", "R2", "B", "C", at(8, 6), at(8, 11))],
        "T3": [connection("T3", "weekday", "R3", "C", "D", at(8, 12), at(8, 17))],
    }
    probabilities = {("R1", "B"): .9, ("R2", "C"): .9, ("R3", "D"): .9}
    result = run(FakeDatabase(stops("A", "B", "C", "D"), trips), probabilities)
    assert trip_ids(result) == [("T1", "T2", "T3")]
    assert result.alternatives[0].itinerary.transfer_count == 2
    blocked = run(
        FakeDatabase(stops("A", "B", "C", "D"), trips), probabilities,
        max_transfers=1,
    )
    assert not blocked.alternatives


def test_walking_transfer_does_not_consume_boarding_round():
    trips = {"T": [connection("T", "weekday", "R", "B", "D", at(8, 5), at(8, 15))]}
    transfers = {"A": [{
        "from_stop_id": "A", "to_stop_id": "B", "transfer_type": 2,
        "min_transfer_time": 120, "from_trip_id": None, "to_trip_id": None,
    }]}
    result = run(
        FakeDatabase(stops("A", "B", "D"), trips, transfers=transfers),
        {("R", "D"): .9}, max_transfers=0,
    )
    assert trip_ids(result) == [("T",)]
    assert result.alternatives[0].itinerary.transfer_count == 0


def test_reliability_is_once_per_boarded_leg_not_per_connection():
    through = [
        connection("T", "weekday", "R", "A", "B", at(8), at(8, 5), 1),
        connection("T", "weekday", "R", "B", "D", at(8, 6), at(8, 10), 2),
    ]
    result = run(
        FakeDatabase(stops("A", "B", "D"), {"T": through}),
        {("R", "B"): .75, ("R", "D"): .75},
    )
    assert result.alternatives[0].route_reliability == pytest.approx(.75)
    assert len(result.alternatives[0].profile_selections) == 1


def test_two_75_percent_legs_multiply_to_5625_and_minimum_transfer_applies():
    trips = {
        "T1": [connection("T1", "weekday", "R1", "A", "B", at(8), at(8, 5))],
        "TOO_SOON": [connection("TOO_SOON", "weekday", "R2", "B", "D", at(8, 6), at(8, 15))],
        "VALID": [connection("VALID", "weekday", "R2", "B", "D", at(8, 8), at(8, 17))],
    }
    transfers = {"B": [{
        "from_stop_id": "B", "to_stop_id": "B", "transfer_type": 2,
        "min_transfer_time": 120, "from_trip_id": None, "to_trip_id": None,
    }]}
    result = run(
        FakeDatabase(stops("A", "B", "D"), trips, transfers=transfers),
        {("R1", "B"): .75, ("R2", "D"): .75},
    )
    assert trip_ids(result) == [("T1", "VALID")]
    assert result.alternatives[0].route_reliability == pytest.approx(.5625)


def test_slower_reliable_alternative_survives_and_dominated_trip_is_removed():
    trips = {
        "FAST": [connection("FAST", "weekday", "RF", "A", "D", at(8), at(8, 10))],
        "SLOW": [connection("SLOW", "weekday", "RS", "A", "D", at(8, 1), at(8, 15))],
        "DOMINATED": [connection("DOMINATED", "weekday", "RX", "A", "D", at(8, 2), at(8, 20))],
    }
    result = run(
        FakeDatabase(stops("A", "D"), trips),
        {("RF", "D"): .5, ("RS", "D"): .95, ("RX", "D"): .4},
    )
    assert set(trip_ids(result)) == {("FAST",), ("SLOW",)}


def test_unreachable_origin_equals_destination_missing_profile_and_overnight():
    database = FakeDatabase(stops("A", "D"), {})
    assert not run(database, {}).alternatives
    same = TransitPlanner(database).get_ranked_route_result(
        "A", "A", MONDAY, at(8), Resolver({}), algorithm="mc_raptor"
    )
    assert same.alternatives[0].route_reliability == 1

    night = {"N": [connection("N", "weekday", "RN", "A", "D", at(25, 5), at(25, 25))]}
    overnight = run(
        FakeDatabase(stops("A", "D"), night), {}, departure=at(25),
    )
    assert overnight.alternatives[0].itinerary.arrival_time == at(25, 25)
    assert overnight.alternatives[0].insufficient_data


def test_service_date_filtering_is_preserved():
    trips = {
        "T": [connection("T", "weekend", "R", "A", "D", at(8), at(8, 10))]
    }
    database = FakeDatabase(
        stops("A", "D"), trips,
        rules={"weekend": {
            "service_id": "weekend", "monday": False, "tuesday": False,
            "wednesday": False, "thursday": False, "friday": False,
            "saturday": True, "sunday": True,
            "start_date": MONDAY, "end_date": MONDAY,
        }},
    )
    assert not run(database, {("R", "D"): .9}).alternatives


def test_pattern_groups_exact_stop_sequences_and_deterministic_reconstruction():
    connections = [
        connection("T1", "weekday", "R", "A", "B", at(8), at(8, 5), 1),
        connection("T1", "weekday", "R", "B", "D", at(8, 6), at(8, 10), 2),
        connection("T2", "weekday", "R", "A", "C", at(8), at(8, 5), 1),
        connection("T2", "weekday", "R", "C", "D", at(8, 6), at(8, 11), 2),
    ]
    index = McRaptorIndex.build(connections, connections, [])
    assert len(index.patterns) == 2
    database = FakeDatabase(
        stops("A", "B", "C", "D"),
        {"T1": connections[:2], "T2": connections[2:]},
    )
    runs = [run(database, {("R", "D"): .9}) for _ in range(2)]
    assert trip_ids(runs[0]) == trip_ids(runs[1])
    assert [item.stop.stop_id for item in runs[0].alternatives[0].itinerary.legs[0].stops] in (
        ["A", "B", "D"], ["A", "C", "D"]
    )
