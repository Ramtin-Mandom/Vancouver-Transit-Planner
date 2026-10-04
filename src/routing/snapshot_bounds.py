"""Optimistic travel-time bounds over the saved network, ignoring waiting."""

import heapq

import numpy as np


class NetworkBounds:
    def __init__(self, arrays):
        n = len(arrays["stop_ids"])
        # Include every ride and transfer, even forbidden/inactive ones. This
        # relaxation can only underestimate actual remaining journey time.
        origins = np.concatenate((arrays["from_stop"], arrays["transfer_from"]))
        targets = np.concatenate((arrays["to_stop"], arrays["transfer_to"]))
        costs = np.concatenate(
            (
                np.asarray(arrays["arrival_seconds"], dtype=np.int64)
                - arrays["departure_seconds"],
                arrays["transfer_seconds"],
            )
        )
        keys = targets.astype(np.int64) * n + origins
        order = np.argsort(keys)
        keys = keys[order]
        starts = (
            np.r_[0, np.flatnonzero(keys[1:] != keys[:-1]) + 1]
            if len(keys)
            else np.array([], dtype=int)
        )
        self.edges = [[] for _ in range(n)]
        if len(starts):
            minimums = np.minimum.reduceat(costs[order], starts)
            for key, cost in zip(keys[starts], minimums, strict=True):
                target, origin = divmod(int(key), n)
                self.edges[target].append((origin, max(0, int(cost))))

    def to(self, destination):
        distances = [float("inf")] * len(self.edges)
        distances[destination] = 0
        queue = [(0, destination)]
        while queue:
            distance, stop = heapq.heappop(queue)
            if distance != distances[stop]:
                continue
            for origin, cost in self.edges[stop]:
                candidate = distance + cost
                if candidate < distances[origin]:
                    distances[origin] = candidate
                    heapq.heappush(queue, (candidate, origin))
        return distances
