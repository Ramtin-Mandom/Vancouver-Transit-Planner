import math

import numpy as np

from src.routing.snapshot_bounds import NetworkBounds


def test_reverse_bound_uses_fastest_parallel_edge_and_transfers():
    arrays = {
        "stop_ids": np.arange(5),
        "from_stop": np.array([0, 0, 1]),
        "to_stop": np.array([1, 1, 2]),
        "departure_seconds": np.array([100, 200, 500]),
        "arrival_seconds": np.array([120, 210, 530]),
        "transfer_from": np.array([2]),
        "transfer_to": np.array([3]),
        "transfer_seconds": np.array([5]),
    }
    bounds = NetworkBounds(arrays)
    assert bounds.to(3)[:4] == [45, 35, 5, 0]
    assert math.isinf(bounds.to(3)[4])
    assert math.isinf(bounds.to(0)[3])


def test_empty_network_and_zero_duration_edges():
    arrays = {
        "stop_ids": np.arange(2),
        "from_stop": np.array([], dtype=int),
        "to_stop": np.array([], dtype=int),
        "departure_seconds": np.array([], dtype=int),
        "arrival_seconds": np.array([], dtype=int),
        "transfer_from": np.array([0]),
        "transfer_to": np.array([1]),
        "transfer_seconds": np.array([0]),
    }
    assert NetworkBounds(arrays).to(1) == [0, 0]
    for name in ("transfer_from", "transfer_to", "transfer_seconds"):
        arrays[name] = np.array([], dtype=int)
    assert math.isinf(NetworkBounds(arrays).to(1)[0])
