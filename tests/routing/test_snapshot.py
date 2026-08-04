from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")

from src.routing.snapshot import (RoutingSnapshot, SnapshotError, SnapshotPlanner,
                                  SnapshotStopCatalog, build_snapshot_from_rows)
import src.routing.snapshot as snapshot_module


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
