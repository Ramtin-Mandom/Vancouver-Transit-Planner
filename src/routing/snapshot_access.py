"""Conservative final walking access between nearby bays of one exchange."""

import math
import re

import numpy as np

from .snapshot_search import haversine_meters


def destination_access(arrays, destination):
    name = str(arrays["stop_names"][destination])
    match = re.fullmatch(r"(.+ Exchange) @ Bay \d+", name)
    access = {}
    if match is None:
        return arrays, access, len(arrays["transfer_from"])
    existing = {
        int(source)
        for source, target in zip(arrays["transfer_from"], arrays["transfer_to"], strict=True)
        if int(target) == destination
    }
    for stop, other in enumerate(arrays["stop_names"]):
        if stop == destination or stop in existing:
            continue
        if not re.fullmatch(re.escape(match[1]) + r" @ Bay \d+", str(other)):
            continue
        distance = haversine_meters(
            float(arrays["stop_lat"][stop]), float(arrays["stop_lon"][stop]),
            float(arrays["stop_lat"][destination]), float(arrays["stop_lon"][destination]),
        )
        if math.isfinite(distance) and distance <= 100:
            access[stop] = max(60, math.ceil(distance / 1.2))
    original_count = len(arrays["transfer_from"])
    if not access:
        return arrays, access, original_count
    result = dict(arrays)
    for key, extra in {
        "transfer_from": list(access),
        "transfer_to": [destination] * len(access),
        "transfer_seconds": list(access.values()),
        "transfer_type": [2] * len(access),
    }.items():
        result[key] = np.concatenate((arrays[key], np.asarray(extra, dtype=arrays[key].dtype)))
    result["transfer_order"] = np.argsort(result["transfer_from"], kind="stable")
    result["transfer_offsets"] = np.r_[0, np.cumsum(np.bincount(
        result["transfer_from"].astype(int), minlength=len(arrays["stop_ids"])
    ))]
    # Legacy arrays may not have same-stop indexes; retain their fallback path.
    return result, access, original_count
