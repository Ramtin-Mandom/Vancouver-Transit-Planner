"""Seeded differential coverage for the array Dijkstra/A* implementations."""

from datetime import date, timedelta
import random

from src.routing.snapshot import RoutingSnapshot, SnapshotPlanner, build_snapshot_from_rows


SEED = 20260803


def test_seeded_fifo_astar_dijkstra_differential(tmp_path):
    rng = random.Random(SEED)
    stop_count = 18
    stops = [{"stop_id": f"S{i}", "stop_name": f"Stop {i}",
              "stop_lat": 49 + i / 1000, "stop_lon": -123 - i / 1000}
             for i in range(stop_count)]
    connections = []
    for i in range(stop_count - 1):
        departure = 28_800 + i * 600
        connections.append({
            "from_stop_id": f"S{i}", "to_stop_id": f"S{i+1}",
            "departure_seconds": departure, "arrival_seconds": departure + 300,
            "trip_id": "THROUGH", "route_id": "CHAIN", "service_id": "ALL",
            "stop_sequence": i, "pickup_type": 0, "drop_off_type": 0,
        })
    for number in range(80):
        source = rng.randrange(stop_count - 1)
        target = rng.randrange(source + 1, stop_count)
        departure = 28_800 + source * 600 + rng.randrange(0, 240)
        connections.append({
            "from_stop_id": f"S{source}", "to_stop_id": f"S{target}",
            "departure_seconds": departure,
            "arrival_seconds": departure + (target - source) * rng.randrange(180, 500),
            "trip_id": f"R{number}", "route_id": f"R{number}", "service_id": "ALL",
            "stop_sequence": 0, "pickup_type": 0, "drop_off_type": 0,
        })
    path = tmp_path / "randomized"
    build_snapshot_from_rows(path, stops=stops, connections=connections,
                             source_version=f"synthetic-seed-{SEED}")
    snapshot = RoutingSnapshot(path)
    planner = SnapshotPlanner(snapshot)
    try:
        for _ in range(500):
            source = rng.randrange(stop_count - 1)
            target = rng.randrange(source + 1, stop_count)
            requested = timedelta(seconds=28_800 + source * 600 - rng.randrange(0, 120))
            dijkstra = planner.get_ranked_route_result(
                f"S{source}", f"S{target}", date(2026, 8, 3), requested,
                algorithm="dijkstra", max_transfers=3)
            astar = planner.get_ranked_route_result(
                f"S{source}", f"S{target}", date(2026, 8, 3), requested,
                algorithm="astar", max_transfers=3)
            assert bool(dijkstra.alternatives) == bool(astar.alternatives)
            if dijkstra.alternatives:
                assert (dijkstra.alternatives[0].itinerary.arrival_time
                        == astar.alternatives[0].itinerary.arrival_time)
    finally:
        snapshot.close()
