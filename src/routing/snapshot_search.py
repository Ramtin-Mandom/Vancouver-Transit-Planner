"""Correctness-first label search over the memory-mapped routing snapshot."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Label:
    stop: int
    arrival: int
    transfers: int
    trip: int
    can_alight: bool
    previous: int
    connection: int


@dataclass
class SearchStats:
    pushed: int = 0
    popped: int = 0
    reopened: int = 0
    connections: int = 0
    transfers: int = 0
    heuristics: int = 0
    zero_fallbacks: int = 0
    heuristic_cache_hits: int = 0
    heuristic_enabled: bool = False
    maximum_speed_mps: float | None = None
    heuristic_fallback_reason: str | None = None
    timed_out: bool = False


EARTH_RADIUS_METERS = 6_371_008.8


def haversine_meters(lat1: float, lon1: float,
                     lat2: float, lon2: float) -> float:
    """Return great-circle distance using the mean Earth radius."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    value = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, value)))


def active_services(arrays: dict[str, np.ndarray], service_date) -> np.ndarray:
    ordinal = service_date.toordinal()
    active = ((ordinal >= arrays["service_start_ordinal"])
              & (ordinal <= arrays["service_end_ordinal"])
              & ((arrays["service_weekday_mask"] & (1 << service_date.weekday())) != 0))
    for match in np.flatnonzero(arrays["exception_date_ordinal"] == ordinal):
        active[int(arrays["exception_service"][match])] = (
            int(arrays["exception_type"][match]) == 1)
    return active


def search(arrays: dict[str, np.ndarray], origin: int, destination: int,
           departure: int, service_date, *, algorithm: str,
           max_transfers: int = 3, search_horizon_seconds: int = 10_800,
           max_extra_seconds: int = 1_800, candidate_limit: int = 24,
           timeout_seconds: float = 30.0, collect_alternatives: bool = True,
           heuristic_metadata: dict[str, Any] | None = None,
           clock=None,
) -> tuple[list[Label], list[int], SearchStats]:
    """Run Dijkstra or correctness-safe geographic A* on the state graph."""
    if algorithm not in {"dijkstra", "astar"}:
        raise ValueError("snapshot routing algorithm must be 'dijkstra' or 'astar'")
    if timeout_seconds <= 0:
        raise ValueError("search timeout must be positive")
    clock = clock or perf_counter
    active = active_services(arrays, service_date)
    horizon = departure + search_horizon_seconds
    labels = [Label(origin, departure, 0, -1, True, -1, -1)]
    best: dict[tuple[int, int, int, bool], int] = {
        (origin, -1, 0, True): departure
    }
    queue: list[tuple[int, int, int, int]] = []
    stats = SearchStats(pushed=1)
    requested_geographic = algorithm == "astar" and not collect_alternatives
    speed = None
    fallback_reason = None
    if algorithm == "astar":
        if collect_alternatives:
            fallback_reason = "alternatives require arrival-ordered candidate collection"
        elif not heuristic_metadata:
            fallback_reason = "snapshot has no validated geographic heuristic metadata"
        elif heuristic_metadata.get("enabled") is not True:
            fallback_reason = str(heuristic_metadata.get("reason") or
                                  "snapshot geographic heuristic is disabled")
        else:
            try:
                speed = float(heuristic_metadata["maximum_speed_mps"])
                if not math.isfinite(speed) or speed <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                speed = None
                fallback_reason = "snapshot geographic heuristic metadata is invalid"
    stats.heuristic_enabled = requested_geographic and speed is not None
    stats.maximum_speed_mps = speed if stats.heuristic_enabled else None
    stats.heuristic_fallback_reason = fallback_reason
    heuristic_cache: dict[int, int] = {destination: 0}
    destination_lat = float(arrays["stop_lat"][destination])
    destination_lon = float(arrays["stop_lon"][destination])

    def estimate(stop: int) -> int:
        if not stats.heuristic_enabled:
            return 0
        cached = heuristic_cache.get(stop)
        if cached is not None:
            stats.heuristic_cache_hits += 1
            return cached
        try:
            latitude = float(arrays["stop_lat"][stop])
            longitude = float(arrays["stop_lon"][stop])
            if (not math.isfinite(latitude) or not math.isfinite(longitude)
                    or not -90 <= latitude <= 90
                    or not -180 <= longitude <= 180):
                raise ValueError
            value = max(0, math.floor(haversine_meters(
                latitude, longitude, destination_lat, destination_lon) / speed))
        except (IndexError, TypeError, ValueError, OverflowError):
            # Metadata validation normally prevents this. Keep a request safe
            # if a legacy or externally modified artifact contains bad data.
            value = 0
        heuristic_cache[stop] = value
        stats.heuristics += 1
        return value
    queue.append((departure + estimate(origin), departure, 0, 0))
    sequence = 1
    winners: list[int] = []
    winner_paths: set[tuple[int, ...]] = set()
    fastest_arrival: int | None = None
    deadline = clock() + timeout_seconds
    departures = arrays["departure_order"]
    offsets = arrays["departure_offsets"]

    def push(label: Label) -> None:
        nonlocal sequence
        key = (label.stop, label.trip, label.transfers, label.can_alight)
        prior = best.get(key)
        if prior is not None and prior <= label.arrival:
            return
        if prior is not None:
            stats.reopened += 1
        best[key] = label.arrival
        labels.append(label)
        index = len(labels) - 1
        heuristic = estimate(label.stop)
        if algorithm == "astar" and not stats.heuristic_enabled:
            stats.zero_fallbacks += 1
        heapq.heappush(queue, (label.arrival + heuristic, label.arrival, sequence, index))
        sequence += 1
        stats.pushed += 1

    while queue:
        if clock() >= deadline:
            stats.timed_out = True
            break
        _, reached, _, label_index = heapq.heappop(queue)
        stats.popped += 1
        label = labels[label_index]
        key = (label.stop, label.trip, label.transfers, label.can_alight)
        if best.get(key) != reached:
            continue
        if fastest_arrival is not None and reached > fastest_arrival + max_extra_seconds:
            break
        if label.stop == destination and label.can_alight:
            path = tuple(connection_path(labels, label_index))
            if path not in winner_paths:
                winner_paths.add(path)
                winners.append(label_index)
                if fastest_arrival is None:
                    fastest_arrival = reached
                if not collect_alternatives:
                    break
                if len(winners) >= candidate_limit:
                    break
            continue

        # Explicit GTFS transfer edges. Type 3 is forbidden.
        for edge in np.flatnonzero(arrays["transfer_from"] == label.stop):
            stats.transfers += 1
            if int(arrays["transfer_type"][edge]) == 3 or not label.can_alight:
                continue
            push(Label(int(arrays["transfer_to"][edge]),
                       reached + int(arrays["transfer_seconds"][edge]),
                       label.transfers, label.trip, True, label_index, -2 - int(edge)))

        start, end = int(offsets[label.stop]), int(offsets[label.stop + 1])
        for position in range(start, end):
            connection = int(departures[position])
            depart = int(arrays["departure_seconds"][connection])
            if depart < reached:
                continue
            if depart > horizon:
                break
            stats.connections += 1
            if not active[int(arrays["service_index"][connection])]:
                continue
            trip = int(arrays["trip_index"][connection])
            continuing = trip == label.trip
            if not continuing and not label.can_alight:
                continue
            same_stop_rules = np.flatnonzero(
                (arrays["transfer_from"] == label.stop)
                & (arrays["transfer_to"] == label.stop))
            if not continuing and len(same_stop_rules):
                if any(int(arrays["transfer_type"][edge]) == 3 for edge in same_stop_rules):
                    continue
                minimum = max(int(arrays["transfer_seconds"][edge]) for edge in same_stop_rules)
                if depart < reached + minimum:
                    continue
            if not continuing and int(arrays["pickup_type"][connection]) != 0:
                continue
            transfer_count = label.transfers + int(label.trip >= 0 and not continuing)
            if transfer_count > max_transfers:
                continue
            push(Label(int(arrays["to_stop"][connection]),
                       int(arrays["arrival_seconds"][connection]), transfer_count,
                       trip, int(arrays["drop_off_type"][connection]) == 0,
                       label_index, connection))
    return labels, winners, stats


def connection_path(labels: list[Label], winner: int) -> list[int]:
    result: list[int] = []
    cursor = winner
    while cursor >= 0:
        label = labels[cursor]
        if label.connection >= 0:
            result.append(label.connection)
        cursor = label.previous
    result.reverse()
    return result


def validate_label_path(arrays: dict[str, np.ndarray], labels: list[Label],
                        winner: int, origin: int, destination: int,
                        departure: int) -> None:
    """Validate the full reconstructed state chain, including walking edges."""
    chain: list[Label] = []
    cursor = winner
    while cursor >= 0:
        chain.append(labels[cursor])
        cursor = labels[cursor].previous
    chain.reverse()
    if not chain or chain[0].stop != origin or chain[0].arrival != departure:
        raise ValueError("itinerary does not begin at the requested origin")
    if chain[-1].stop != destination or not chain[-1].can_alight:
        raise ValueError("itinerary does not end at the requested destination")
    counted_transfers = 0
    for previous, current in zip(chain, chain[1:]):
        if current.arrival < previous.arrival:
            raise ValueError("itinerary travels backward in time")
        if current.connection >= 0:
            item = current.connection
            if int(arrays["from_stop"][item]) != previous.stop:
                raise ValueError("ride connection starts at the wrong stop")
            if int(arrays["to_stop"][item]) != current.stop:
                raise ValueError("ride connection ends at the wrong stop")
            depart = int(arrays["departure_seconds"][item])
            arrive = int(arrays["arrival_seconds"][item])
            trip = int(arrays["trip_index"][item])
            if depart < previous.arrival or arrive != current.arrival:
                raise ValueError("ride timestamps do not match the label chain")
            changing = previous.trip >= 0 and trip != previous.trip
            if changing:
                counted_transfers += 1
            if trip != previous.trip and int(arrays["pickup_type"][item]) != 0:
                raise ValueError("itinerary violates pickup restrictions")
            if current.can_alight != (int(arrays["drop_off_type"][item]) == 0):
                raise ValueError("itinerary drop-off state is inconsistent")
        else:
            edge = -2 - current.connection
            if edge < 0 or edge >= len(arrays["transfer_from"]):
                raise ValueError("itinerary references an invalid transfer edge")
            if int(arrays["transfer_type"][edge]) == 3:
                raise ValueError("itinerary uses a forbidden transfer")
            if (int(arrays["transfer_from"][edge]) != previous.stop
                    or int(arrays["transfer_to"][edge]) != current.stop):
                raise ValueError("transfer edge does not connect its labels")
            expected = previous.arrival + int(arrays["transfer_seconds"][edge])
            if current.arrival != expected or not previous.can_alight:
                raise ValueError("itinerary transfer timing is invalid")
        if current.transfers != counted_transfers:
            raise ValueError("itinerary transfer count is inconsistent")


def validate_connection_path(arrays: dict[str, np.ndarray], path: list[int],
                             origin: int, destination: int, departure: int,
                             arrival: int) -> None:
    """Independently reject chronological or disconnected reconstructed rides."""
    if not path:
        raise ValueError("non-trivial itinerary has no ride connections")
    first_stop = int(arrays["from_stop"][path[0]])
    if first_stop != origin and not len(np.flatnonzero(
            (arrays["transfer_from"] == origin) & (arrays["transfer_to"] == first_stop)
            & (arrays["transfer_type"] != 3))):
        raise ValueError("itinerary does not begin at the requested origin")
    last_stop = int(arrays["to_stop"][path[-1]])
    if last_stop != destination and not len(np.flatnonzero(
            (arrays["transfer_from"] == last_stop) & (arrays["transfer_to"] == destination)
            & (arrays["transfer_type"] != 3))):
        raise ValueError("itinerary does not end at the requested destination")
    prior_arrival = departure
    prior_stop = first_stop
    prior_trip = -1
    for item in path:
        source = int(arrays["from_stop"][item])
        trip = int(arrays["trip_index"][item])
        depart = int(arrays["departure_seconds"][item])
        arrive = int(arrays["arrival_seconds"][item])
        if depart < prior_arrival or arrive < depart:
            raise ValueError("itinerary travels backward in time")
        if source != prior_stop:
            edges = np.flatnonzero((arrays["transfer_from"] == prior_stop)
                                   & (arrays["transfer_to"] == source)
                                   & (arrays["transfer_type"] != 3))
            if not len(edges):
                raise ValueError("itinerary contains a disconnected vehicle change")
            minimum = min(int(arrays["transfer_seconds"][edge]) for edge in edges)
            if depart < prior_arrival + minimum:
                raise ValueError("itinerary violates minimum transfer time")
        if trip != prior_trip and int(arrays["pickup_type"][item]) != 0:
            raise ValueError("itinerary violates pickup restrictions")
        prior_arrival = arrive
        prior_stop = int(arrays["to_stop"][item])
        prior_trip = trip
    if last_stop == destination and prior_arrival != arrival:
        raise ValueError("reported arrival differs from the final connection")
    if last_stop != destination:
        edges = np.flatnonzero((arrays["transfer_from"] == last_stop)
                               & (arrays["transfer_to"] == destination)
                               & (arrays["transfer_type"] != 3))
        if arrival < prior_arrival + min(int(arrays["transfer_seconds"][edge]) for edge in edges):
            raise ValueError("reported arrival omits destination transfer time")
