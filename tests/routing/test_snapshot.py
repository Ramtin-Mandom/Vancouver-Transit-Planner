from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")

from src.routing.snapshot import (RoutingSnapshot, SnapshotError, SnapshotPlanner,
                                  SnapshotStopCatalog, build_snapshot_from_rows)
import src.routing.snapshot as snapshot_module
from src.routing.route_results import itinerary_identity


STOPS = [
    {"stop_id": "A", "stop_name": "Alpha Station", "stop_code": "1", "stop_lat": 49.0, "stop_lon": -123.0},
    {"stop_id": "B", "stop_name": "Beta", "stop_code": "2", "stop_lat": 49.1, "stop_lon": -123.1},
    {"stop_id": "C", "stop_name": "Central", "stop_code": "3", "stop_lat": 49.2, "stop_lon": -123.2},
]
CONNECTIONS = [
    {"from_stop_id":"A","to_stop_id":"B","departure_seconds":28800,"arrival_seconds":29400,"trip_id":"T1","route_id":"R","route_name":"10","service_id":"S","stop_sequence":1,"direction_id":0},
    {"from_stop_id":"B","to_stop_id":"C","departure_seconds":29520,"arrival_seconds":30600,"trip_id":"T2","route_id":"R","route_name":"10","service_id":"S","stop_sequence":2,"direction_id":0},
]


@pytest.fixture
def snapshot(tmp_path):
    path=tmp_path/"snapshot"
    build_snapshot_from_rows(path,stops=STOPS,connections=CONNECTIONS)
    loaded=RoutingSnapshot(path)
    yield loaded
    loaded.close()


def test_build_load_manifest_and_autocomplete(snapshot):
    assert snapshot.manifest["counts"] == {"stops":3,"routes":1,"trips":2,"connections":2}
    assert [stop.stop_id for stop in snapshot.search_stops("ALP")] == ["A"]
    assert isinstance(snapshot.arrays["from_stop"], np.memmap)


def test_array_planner_and_concurrent_reads(snapshot):
    planner=SnapshotPlanner(snapshot)
    def route():
        return planner.get_ranked_route_result("A","C",date(2026,8,3),timedelta(hours=8))
    results=list(ThreadPoolExecutor(max_workers=4).map(lambda _:route(),range(12)))
    assert all(result.alternatives[0].route_reliability == 0.25 for result in results)
    assert all(len(result.alternatives[0].itinerary.legs) == 2 for result in results)
    assert all(result.alternatives[0].itinerary.departure_time == timedelta(hours=8) for result in results)


def test_incompatible_and_corrupt_snapshots(tmp_path):
    path=tmp_path/"snapshot"; build_snapshot_from_rows(path,stops=STOPS,connections=CONNECTIONS)
    manifest=path/"manifest.json"; manifest.write_text(manifest.read_text().replace('"format_version": 2','"format_version": 999'))
    with pytest.raises(SnapshotError,match="unsupported"): RoutingSnapshot(path)
    catalog = SnapshotStopCatalog(path)
    try:
        assert [stop.stop_id for stop in catalog.search_stops("alpha")] == ["A"]
        assert catalog.find_stop("C").stop_name == "Central"
    finally:
        catalog.close()
    manifest.unlink()
    with pytest.raises(SnapshotError,match="manifest"): RoutingSnapshot(path)


def test_compact_fixture_has_no_connection_objects(snapshot):
    assert sum(array.nbytes for array in snapshot.arrays.values()) < 10_000
    assert not hasattr(snapshot, "connections")


def test_generation_is_streamed_and_removes_spool(tmp_path):
    produced = 0

    def rows():
        nonlocal produced
        for index in range(5_000):
            produced += 1
            yield {
                **CONNECTIONS[index % 2],
                "trip_id": f"T{index}",
                "departure_seconds": 28_800 + index,
                "arrival_seconds": 28_801 + index,
            }

    path = tmp_path / "snapshot"
    build_snapshot_from_rows(path, stops=STOPS, connections=rows())
    assert produced == 5_000
    assert not list(tmp_path.glob(".snapshot.tmp-*"))
    assert not list(path.glob("*.jsonl"))


def test_failed_build_cleans_temporary_files_and_preserves_existing(tmp_path):
    path = tmp_path / "snapshot"
    build_snapshot_from_rows(path, stops=STOPS, connections=CONNECTIONS)
    original = (path / "manifest.json").read_bytes()
    bad = [dict(CONNECTIONS[0], arrival_seconds=1)]
    with pytest.raises(SnapshotError, match="time ordering"):
        build_snapshot_from_rows(path, stops=STOPS, connections=bad)
    assert (path / "manifest.json").read_bytes() == original
    assert not list(tmp_path.glob(".snapshot.tmp-*"))


def test_build_fails_when_peak_rss_exceeds_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "_rss_bytes", lambda: 20_000)
    with pytest.raises(SnapshotError, match="exceeded limit"):
        build_snapshot_from_rows(
            tmp_path / "snapshot", stops=STOPS, connections=CONNECTIONS,
            max_peak_rss_bytes=10_000,
        )
    assert not list(tmp_path.glob(".snapshot.tmp-*"))


def test_astar_and_dijkstra_agree_and_report_executed_algorithm(snapshot):
    planner = SnapshotPlanner(snapshot)
    dijkstra = planner.get_ranked_route_result(
        "A", "C", date(2026, 8, 3), timedelta(hours=7, minutes=55),
        algorithm="dijkstra", include_diagnostics=True)
    astar = planner.get_ranked_route_result(
        "A", "C", date(2026, 8, 3), timedelta(hours=7, minutes=55),
        algorithm="astar", include_diagnostics=True)
    assert dijkstra.alternatives[0].itinerary.arrival_time == astar.alternatives[0].itinerary.arrival_time
    assert dijkstra.diagnostics.counters.executed_algorithm == "dijkstra"
    assert astar.diagnostics.counters.executed_algorithm == "astar"
    assert astar.diagnostics.counters.zero_heuristic_fallbacks > 0
    assert astar.alternatives[0].itinerary.total_scheduled_travel_time == timedelta(minutes=35)


def _direct_alternative_snapshot(tmp_path, count, *, late=False):
    connections = [
        {
            "from_stop_id": "A", "to_stop_id": "C",
            "departure_seconds": 28_800 + number * 60,
            "arrival_seconds": 29_400 + number * 300,
            "trip_id": f"T{number}", "route_id": f"R{number}",
            "route_name": f"Route {number}", "service_id": "S",
            "stop_sequence": 1,
        }
        for number in range(count)
    ]
    if count == 0:
        connections.append({
            "from_stop_id": "B", "to_stop_id": "C",
            "departure_seconds": 28_800, "arrival_seconds": 29_400,
            "trip_id": "UNREACHABLE", "route_id": "UNREACHABLE",
            "service_id": "S", "stop_sequence": 1,
        })
    if late:
        connections.append({
            "from_stop_id": "A", "to_stop_id": "C",
            "departure_seconds": 28_900, "arrival_seconds": 33_000,
            "trip_id": "TOO-LATE", "route_id": "TOO-LATE",
            "service_id": "S", "stop_sequence": 1,
        })
    path = tmp_path / f"alternatives-{count}-{late}"
    profiles = [
        {
            "route_id": f"R{number}", "direction_id": None,
            "time_window": "morning_peak",
            "reliability_probability": 0.4 + number * 0.1,
            "sample_count": 100,
        }
        for number in range(count)
    ]
    build_snapshot_from_rows(
        path, stops=STOPS, connections=connections,
        reliability_profiles=profiles,
    )
    return RoutingSnapshot(path)


@pytest.mark.parametrize("available, expected", [(4, 3), (2, 2), (1, 1), (0, 0)])
def test_snapshot_returns_up_to_three_distinct_alternatives(
    tmp_path, available, expected
):
    loaded = _direct_alternative_snapshot(tmp_path, available)
    try:
        planner = SnapshotPlanner(loaded)
        first = planner.get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            algorithm="dijkstra", route_number=3, include_alternatives=True,
        )
        repeated = planner.get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            algorithm="dijkstra", route_number=3, include_alternatives=True,
        )
        assert len(first.alternatives) == expected
        identities = [itinerary_identity(item.itinerary) for item in first.alternatives]
        assert len(identities) == len(set(identities))
        assert identities == [
            itinerary_identity(item.itinerary) for item in repeated.alternatives
        ]
        for item in first.alternatives:
            itinerary = item.itinerary
            assert itinerary.arrival_time >= itinerary.departure_time
            assert itinerary.legs[0].origin.stop_id == "A"
            assert itinerary.legs[-1].destination.stop_id == "C"
    finally:
        loaded.close()


def test_snapshot_alternatives_obey_extra_window_and_preserve_fastest(tmp_path):
    loaded = _direct_alternative_snapshot(tmp_path, 3, late=True)
    try:
        planner = SnapshotPlanner(loaded)
        dijkstra = planner.get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            algorithm="dijkstra", route_number=3, max_extra_minutes=10,
            include_alternatives=True,
        )
        astar = planner.get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            algorithm="astar", route_number=3, max_extra_minutes=10,
            include_alternatives=True,
        )
        assert len(dijkstra.alternatives) == 3
        assert all(
            item.itinerary.legs[0].trip_id != "TOO-LATE"
            for item in dijkstra.alternatives
        )
        assert dijkstra.alternatives[0].itinerary.arrival_time == timedelta(
            seconds=29_400
        )
        assert [itinerary_identity(item.itinerary) for item in dijkstra.alternatives] == [
            itinerary_identity(item.itinerary) for item in astar.alternatives
        ]
    finally:
        loaded.close()


def test_slower_equal_route_is_dominated(tmp_path):
    connections = [
        {"from_stop_id": "A", "to_stop_id": "C", "departure_seconds": 28_800,
         "arrival_seconds": arrival, "trip_id": trip, "route_id": route,
         "service_id": "S", "stop_sequence": 1}
        for arrival, trip, route in (
            (29_400, "FAST", "RF"), (30_000, "SLOW", "RS")
        )
    ]
    path = tmp_path / "dominated"
    build_snapshot_from_rows(path, stops=STOPS, connections=connections)
    loaded = RoutingSnapshot(path)
    try:
        result = SnapshotPlanner(loaded).get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8), route_number=3,
            include_alternatives=True,
        )
        assert [item.itinerary.legs[0].trip_id for item in result.alternatives] == ["FAST"]
    finally:
        loaded.close()


def test_equivalent_trip_instances_are_deduplicated(tmp_path):
    connections = [
        {
            "from_stop_id": "A", "to_stop_id": "C",
            "departure_seconds": 28_800, "arrival_seconds": 29_400,
            "trip_id": trip, "route_id": "R", "service_id": "S",
            "stop_sequence": 1,
        }
        for trip in ("INSTANCE-1", "INSTANCE-2")
    ]
    path = tmp_path / "equivalent-instances"
    build_snapshot_from_rows(path, stops=STOPS, connections=connections)
    loaded = RoutingSnapshot(path)
    try:
        result = SnapshotPlanner(loaded).get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8), route_number=3,
            include_alternatives=True,
        )
        assert len(result.alternatives) == 1
    finally:
        loaded.close()


def test_adjacent_bay_suffix_detour_is_dominated(tmp_path):
    stops = [
        {"stop_id": "A", "stop_name": "Origin"},
        {"stop_id": "B1", "stop_name": "Bay 1", "parent_station": "B"},
        {"stop_id": "B2", "stop_name": "Bay 2", "parent_station": "B"},
        {"stop_id": "B", "stop_name": "Station"},
    ]
    connections = [
        {"from_stop_id": "A", "to_stop_id": "B1", "departure_seconds": 28_800,
         "arrival_seconds": 29_400, "trip_id": "DIRECT", "route_id": "R",
         "service_id": "S", "stop_sequence": 1},
        {"from_stop_id": "A", "to_stop_id": "B2", "departure_seconds": 28_800,
         "arrival_seconds": 29_520, "trip_id": "DETOUR", "route_id": "R",
         "service_id": "S", "stop_sequence": 1},
    ]
    path = tmp_path / "bay-detour"
    build_snapshot_from_rows(path, stops=stops, connections=connections,
                             intra_station_transfer_seconds=120)
    loaded = RoutingSnapshot(path)
    try:
        result = SnapshotPlanner(loaded).get_ranked_route_result(
            "A", "B1", date(2026, 8, 3), timedelta(hours=8), route_number=3,
            include_alternatives=True,
        )
        assert [item.itinerary.legs[0].trip_id for item in result.alternatives] == ["DIRECT"]
    finally:
        loaded.close()


def test_snapshot_complete_search_is_invoked_once(snapshot, monkeypatch):
    calls = 0
    original = snapshot_module.snapshot_search

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(snapshot_module, "snapshot_search", counted)
    SnapshotPlanner(snapshot).get_ranked_route_result(
        "A", "C", date(2026, 8, 3), timedelta(hours=8), route_number=3,
        include_alternatives=True,
    )
    assert calls == 1


def test_single_route_mode_stops_at_first_destination_and_does_less_work(tmp_path):
    loaded = _direct_alternative_snapshot(tmp_path, 4)
    try:
        planner = SnapshotPlanner(loaded)
        single = planner.get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            include_alternatives=False, include_diagnostics=True,
        )
        alternatives = planner.get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            include_alternatives=True, include_diagnostics=True,
        )
        assert len(single.alternatives) == 1
        assert len(alternatives.alternatives) == 3
        assert (single.alternatives[0].itinerary
                == alternatives.alternatives[0].itinerary)
        assert single.diagnostics.counters.destination_labels_found == 1
        assert alternatives.diagnostics.counters.destination_labels_found == 4
        assert (single.diagnostics.counters.states_popped
                < alternatives.diagnostics.counters.states_popped)
        assert (single.diagnostics.counters.labels_created
                <= alternatives.diagnostics.counters.labels_created)
    finally:
        loaded.close()


def test_pickup_dropoff_transfer_and_maximum_transfer(tmp_path):
    stops = [
        {"stop_id": "A", "stop_name": "A", "parent_station": None},
        {"stop_id": "B1", "stop_name": "B bay 1", "parent_station": "B"},
        {"stop_id": "B2", "stop_name": "B bay 2", "parent_station": "B"},
        {"stop_id": "B", "stop_name": "B station", "parent_station": None},
        {"stop_id": "C", "stop_name": "C", "parent_station": None},
    ]
    connections = [
        {"from_stop_id":"A","to_stop_id":"B1","departure_seconds":28800,"arrival_seconds":29400,
         "trip_id":"T1","route_id":"R1","service_id":"S","stop_sequence":1},
        {"from_stop_id":"B2","to_stop_id":"C","departure_seconds":29520,"arrival_seconds":30000,
         "trip_id":"T2","route_id":"R2","service_id":"S","stop_sequence":1},
    ]
    path = tmp_path / "rules"
    build_snapshot_from_rows(path, stops=stops, connections=connections,
                             intra_station_transfer_seconds=120)
    loaded = RoutingSnapshot(path)
    try:
        result = SnapshotPlanner(loaded).get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            algorithm="dijkstra", max_transfers=1)
        assert result.alternatives[0].itinerary.arrival_time == timedelta(seconds=30000)
        blocked = SnapshotPlanner(loaded).get_ranked_route_result(
            "A", "C", date(2026, 8, 3), timedelta(hours=8),
            algorithm="dijkstra", max_transfers=0)
        assert blocked.alternatives == ()
        origin_transfer = SnapshotPlanner(loaded).get_ranked_route_result(
            "B1", "C", date(2026, 8, 3), timedelta(seconds=29280),
            algorithm="dijkstra", max_transfers=0)
        assert origin_transfer.alternatives[0].itinerary.arrival_time == timedelta(seconds=30000)
        destination_transfer = SnapshotPlanner(loaded).get_ranked_route_result(
            "A", "B2", date(2026, 8, 3), timedelta(hours=8),
            algorithm="dijkstra", max_transfers=0)
        assert destination_transfer.alternatives[0].itinerary.arrival_time == timedelta(seconds=29520)
    finally:
        loaded.close()
