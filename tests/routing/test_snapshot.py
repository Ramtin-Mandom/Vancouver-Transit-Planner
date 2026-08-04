from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")

from src.routing.snapshot import (RoutingSnapshot, SnapshotError, SnapshotPlanner,
                                  build_snapshot_from_rows)


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
    assert all(result.alternatives[0].route_reliability == 1.0 for result in results)
    assert all(len(result.alternatives[0].itinerary.legs) == 2 for result in results)


def test_incompatible_and_corrupt_snapshots(tmp_path):
    path=tmp_path/"snapshot"; build_snapshot_from_rows(path,stops=STOPS,connections=CONNECTIONS)
    manifest=path/"manifest.json"; manifest.write_text(manifest.read_text().replace('"format_version": 1','"format_version": 999'))
    with pytest.raises(SnapshotError,match="unsupported"): RoutingSnapshot(path)
    manifest.unlink()
    with pytest.raises(SnapshotError,match="manifest"): RoutingSnapshot(path)


def test_compact_fixture_has_no_connection_objects(snapshot):
    assert sum(array.nbytes for array in snapshot.arrays.values()) < 10_000
    assert not hasattr(snapshot, "connections")
