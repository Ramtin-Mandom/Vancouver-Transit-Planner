from src.routing.search_data import RequestTripConnectionLoader, SearchDataIndex
from tests.routing.test_planner import at, connection


def test_departure_and_trip_indexes_are_sorted_and_boundary_inclusive():
    late = connection("T2", "weekday", "R", "A", "C", at(25), at(25, 10), 4)
    early = connection("T1", "weekday", "R", "A", "B", at(8), at(8, 10), 2)
    index = SearchDataIndex.build([late, early], [late, early], [])
    assert index.departures("A", at(8), at(25)) == (early, late)
    assert index.departures("A", at(25), at(25)) == (late,)
    assert index.trip_connections("T2", 4) == (late,)


def test_empty_indexes_and_request_isolation():
    first = SearchDataIndex.build([], [], [])
    second = SearchDataIndex.build([], [], [])
    assert first.departures("missing", at(0), at(30)) == ()
    assert first.trip_connections("missing", 1) == ()
    assert first.transfers("missing") == ()
    assert first.departures_by_stop is not second.departures_by_stop


def test_frontier_loader_batches_orders_deduplicates_and_remembers_empty_trips():
    rows = [
        connection("T1", "weekday", "R", "B", "C", at(8, 11), at(8, 20), 2),
        connection("T1", "weekday", "R", "A", "B", at(8), at(8, 10), 1),
    ]

    class Repository:
        def __init__(self):
            self.batches = []

        def bulk_trip_connections(self, trip_ids, *, batch_size):
            self.batches.append(tuple(sorted(trip_ids)))
            return [item for item in rows if item.trip_id in trip_ids]

    repository = Repository()
    loader = RequestTripConnectionLoader(repository, batch_size=2)
    loader.ensure_loaded({"T1", "EMPTY", "T1"})
    loader.ensure_loaded({"T1", "EMPTY"})
    assert repository.batches == [("EMPTY", "T1")]
    assert [item.from_stop_sequence for item in loader.connections_for("T1")] == [1, 2]
    assert loader.known_empty_trip_ids == {"EMPTY"}
    assert loader.query_count == 1


def test_frontier_loader_empty_input_executes_no_query_and_is_request_local():
    class Repository:
        calls = 0

        def bulk_trip_connections(self, trip_ids, *, batch_size):
            self.calls += 1
            return []

    repository = Repository()
    first = RequestTripConnectionLoader(repository)
    second = RequestTripConnectionLoader(repository)
    first.ensure_loaded(set())
    assert repository.calls == 0
    assert first.loaded_trip_ids is not second.loaded_trip_ids
