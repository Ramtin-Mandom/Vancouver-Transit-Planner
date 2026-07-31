"""Bounded time-dependent Pareto search for reliability-aware alternatives.

Each boarding contributes the selected profile's sample-adjusted
``reliability_probability``.
Leg probabilities are assumed independent and are multiplied (implemented as
the sum of negative logarithms). Transfer success is deliberately not added:
the same delay observations feed the leg profiles, so doing both here would
risk counting one reliability signal twice.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import count
from time import perf_counter
from typing import Any

from .models import (
    Connection,
    Itinerary,
    ReliableAlternative,
    ReliableSearchResult,
    RouteLeg,
    SearchTiming,
    Stop,
)
from .route_results import itinerary_identity
from .reconstruction import build_leg_stops, load_connection_stops

EPSILON = 1e-9
DEFAULT_MAX_TRANSFERS = 3
DEFAULT_SEARCH_HORIZON_MINUTES = 180
DEFAULT_TIMEOUT_SECONDS = 30.0


class ReliableSearchTimeout(RuntimeError):
    """Raised when a reliable search exceeds its configured wall-clock limit."""


@dataclass(frozen=True)
class _Label:
    stop_id: str
    arrival: timedelta
    reliability_cost: float
    transfers: int
    current_trip_id: str | None
    rides: tuple[Connection, ...]
    selections: tuple[Any, ...]
    visited: frozenset[str]


def _dominates(left: _Label, right: _Label) -> bool:
    """Safe dominance for labels with compatible current-trip state."""
    if left.stop_id != right.stop_id or left.current_trip_id != right.current_trip_id:
        return False
    no_worse = (
        left.arrival <= right.arrival
        and left.reliability_cost <= right.reliability_cost
        and left.transfers <= right.transfers
    )
    return no_worse and (
        left.arrival < right.arrival
        or left.reliability_cost < right.reliability_cost
        or left.transfers < right.transfers
    )


class ParetoTransitSearch:
    def __init__(self, database: Any, calendar: Any, resolver: Any) -> None:
        self.database = database
        self.calendar = calendar
        self.resolver = resolver

    def search(
        self,
        origin_stop_id: str,
        destination_stop_id: str,
        service_date: date,
        departure_time: timedelta,
        *,
        limit: int | None = None,
        max_transfers: int = DEFAULT_MAX_TRANSFERS,
        max_extra_minutes: int = 30,
        search_horizon_minutes: int = DEFAULT_SEARCH_HORIZON_MINUTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> ReliableSearchResult:
        if limit is not None and limit < 1:
            raise ValueError("candidate limit must be positive")
        if max_transfers < 0 or max_extra_minutes < 0:
            raise ValueError("invalid reliable-search bounds")
        if search_horizon_minutes < 1:
            raise ValueError("search horizon must be positive")
        if timeout_seconds <= 0:
            raise ValueError("search timeout must be positive")
        started = perf_counter()
        deadline = started + timeout_seconds

        def check_deadline() -> None:
            if perf_counter() >= deadline:
                raise ReliableSearchTimeout(
                    f"reliable search exceeded {timeout_seconds:g} seconds; "
                    "try a shorter horizon or fewer transfers"
                )

        configure_timeout = getattr(self.database, "set_statement_timeout", None)
        if callable(configure_timeout):
            configure_timeout(max(1, int(timeout_seconds * 1000)))
        resolver_timeout = getattr(self.resolver, "set_statement_timeout", None)
        if callable(resolver_timeout):
            resolver_timeout(max(1, int(timeout_seconds * 1000)))
        origin = self.database.find_stop(origin_stop_id)
        check_deadline()
        destination = self.database.find_stop(destination_stop_id)
        check_deadline()
        if origin is None or destination is None:
            missing = origin_stop_id if origin is None else destination_stop_id
            raise ValueError(f"unknown stop_id: {missing}")
        horizon = departure_time + timedelta(minutes=search_horizon_minutes)
        active_lookup = getattr(self.database, "active_service_ids", None)
        active = active_lookup(service_date) if callable(active_lookup) else None
        departure_cache: dict[str, list[Connection]] = {}
        trip_cache: dict[tuple[str, int], list[Connection]] = {}
        transfer_cache: dict[str, list[dict[str, Any]]] = {}
        profile_cache: dict[tuple[str, str, int, int], Any] = {}
        loaded_at = perf_counter()

        def departures(stop_id: str) -> list[Connection]:
            check_deadline()
            if stop_id in departure_cache:
                return departure_cache[stop_id]
            bounded = getattr(self.database, "departures_in_window", None)
            if callable(bounded):
                rows = bounded(stop_id, departure_time, horizon, service_ids=active)
            else:
                rows, offset = [], 0
                while True:
                    batch = self.database.departures_from(
                        stop_id, departure_time, limit=256, offset=offset,
                        service_ids=active,
                    )
                    rows.extend(item for item in batch if item.departure_time <= horizon)
                    if len(batch) < 256 or (batch and batch[-1].departure_time > horizon):
                        break
                    offset += 256
            departure_cache[stop_id] = rows
            check_deadline()
            return rows

        def trip(first: Connection) -> list[Connection]:
            check_deadline()
            key = (first.trip_id, first.from_stop_sequence)
            if key not in trip_cache:
                trip_cache[key] = self.database.trip_connections(*key)
            return trip_cache[key]

        def transfers(stop_id: str) -> list[dict[str, Any]]:
            check_deadline()
            if stop_id not in transfer_cache:
                transfer_cache[stop_id] = self.database.transfers_from(stop_id)
            return transfer_cache[stop_id]

        def profile(connection: Connection, alight_stop: str):
            check_deadline()
            key = (
                connection.route_id,
                connection.direction_id,
                connection.arrival_time,
            )
            if key not in profile_cache:
                try:
                    profile_cache[key] = self.resolver.resolve(*key)
                except TypeError:
                    # Compatibility for external resolvers using the deprecated
                    # stop/weekday/hour interface.
                    hour = int(connection.arrival_time.total_seconds() // 3600) % 24
                    profile_cache[key] = self.resolver.resolve(
                        connection.route_id, alight_stop,
                        service_date.weekday(), hour,
                    )
                check_deadline()
            return profile_cache[key]

        initial = _Label(
            origin_stop_id, departure_time, 0.0, 0, None, (), (),
            frozenset((origin_stop_id,)),
        )
        labels: dict[tuple[str, str | None], list[_Label]] = {
            (origin_stop_id, None): [initial]
        }
        queue: list[tuple[timedelta, float, int, int, _Label]] = []
        serial = count()
        heapq.heappush(queue, (initial.arrival, 0.0, 0, next(serial), initial))
        destinations: list[_Label] = []
        arrival_cutoff: timedelta | None = None
        pruned = 0

        def add(label: _Label) -> None:
            nonlocal pruned, arrival_cutoff
            key = (label.stop_id, label.current_trip_id)
            bucket = labels.setdefault(key, [])
            if any(_dominates(other, label) or other == label for other in bucket):
                pruned += 1
                return
            survivors = [other for other in bucket if not _dominates(label, other)]
            pruned += len(bucket) - len(survivors)
            survivors.append(label)
            labels[key] = survivors
            if label.stop_id == destination_stop_id:
                candidate_cutoff = label.arrival + timedelta(
                    minutes=max_extra_minutes
                )
                arrival_cutoff = min(
                    arrival_cutoff or candidate_cutoff, candidate_cutoff
                )
            heapq.heappush(
                queue,
                (label.arrival, label.reliability_cost, label.transfers,
                 next(serial), label),
            )

        while queue:
            check_deadline()
            _, _, _, _, label = heapq.heappop(queue)
            if arrival_cutoff is not None and label.arrival > arrival_cutoff:
                break
            if label not in labels.get((label.stop_id, label.current_trip_id), ()):
                continue
            if label.stop_id == destination_stop_id:
                destinations.append(label)
                if arrival_cutoff is None:
                    # The heap is ordered by scheduled arrival, so the first
                    # destination establishes the fastest feasible arrival.
                    arrival_cutoff = label.arrival + timedelta(
                        minutes=max_extra_minutes
                    )
                continue
            for transfer in transfers(label.stop_id):
                target = transfer["to_stop_id"]
                if transfer["transfer_type"] == 3 or target == label.stop_id:
                    continue
                if target in label.visited:
                    continue
                required_trip = transfer.get("from_trip_id")
                if required_trip not in (None, label.current_trip_id):
                    continue
                arrival = label.arrival + timedelta(
                    seconds=transfer["min_transfer_time"] or 0
                )
                effective_horizon = min(
                    horizon, arrival_cutoff or timedelta.max
                )
                if arrival <= effective_horizon:
                    add(_Label(
                        target, arrival, label.reliability_cost, label.transfers,
                        None, label.rides, label.selections,
                        label.visited | {target},
                    ))
            for first in departures(label.stop_id):
                effective_horizon = min(
                    horizon, arrival_cutoff or timedelta.max
                )
                if (
                    first.departure_time < label.arrival
                    or first.departure_time > effective_horizon
                ):
                    continue
                # The original boarding scan already emitted labels for every
                # downstream alighting point on this vehicle.
                if first.trip_id == label.current_trip_id:
                    continue
                if active is None and not self.calendar.operates(
                    first.service_id, service_date
                ):
                    continue
                new_transfer = int(bool(label.rides) and first.trip_id != label.current_trip_id)
                if label.transfers + new_transfer > max_transfers:
                    continue
                rules = transfers(label.stop_id)
                matching = [
                    rule for rule in rules
                    if rule["to_stop_id"] == label.stop_id
                    and rule.get("from_trip_id") in (None, label.current_trip_id)
                    and rule.get("to_trip_id") in (None, first.trip_id)
                ]
                if any(rule["transfer_type"] == 3 for rule in matching):
                    continue
                minimum = max(
                    (rule["min_transfer_time"] or 0 for rule in matching), default=0
                )
                if first.trip_id != label.current_trip_id and first.departure_time < (
                    label.arrival + timedelta(seconds=minimum)
                ):
                    continue
                segment: list[Connection] = []
                current = label.stop_id
                for connection in trip(first):
                    if connection.from_stop_id != current:
                        break
                    segment.append(connection)
                    current = connection.to_stop_id
                    if current in label.visited:
                        break
                    if connection.arrival_time > effective_horizon:
                        break
                    selection = profile(connection, current)
                    probability = (
                        selection.profile.reliability_probability
                        if selection.profile is not None else 0.0
                    )
                    add(_Label(
                        current,
                        connection.arrival_time,
                        label.reliability_cost
                        - math.log(max(probability, EPSILON)),
                        label.transfers + new_transfer,
                        first.trip_id,
                        label.rides + tuple(segment),
                        label.selections + (selection,),
                        label.visited | {current},
                    ))

        searched_at = perf_counter()
        if destinations:
            fastest = min(item.arrival for item in destinations)
            cutoff = fastest + timedelta(minutes=max_extra_minutes)
            destinations = [item for item in destinations if item.arrival <= cutoff]
            destinations = [
                item for item in destinations
                if not any(
                    other is not item
                    and other.arrival <= item.arrival
                    and other.reliability_cost <= item.reliability_cost
                    and other.transfers <= item.transfers
                    and (
                        other.arrival < item.arrival
                        or other.reliability_cost < item.reliability_cost
                        or other.transfers < item.transfers
                    )
                    for other in destinations
                )
            ]
        alternatives: list[ReliableAlternative] = []
        seen: set[tuple[tuple[str, str, str], ...]] = set()
        for label in sorted(
            destinations,
            key=lambda item: (
                item.reliability_cost, item.arrival, item.transfers,
                tuple((hop.trip_id, hop.from_stop_id, hop.to_stop_id) for hop in item.rides),
            ),
        ):
            itinerary = self._reconstruct(
                origin, destination, service_date, departure_time, label
            )
            identifier = itinerary_identity(itinerary)
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            alternatives.append(ReliableAlternative(
                itinerary,
                math.exp(-label.reliability_cost),
                label.reliability_cost,
                label.selections,
            ))
        ranked_at = perf_counter()
        returned = alternatives if limit is None else alternatives[:limit]
        return ReliableSearchResult(
            tuple(returned),
            SearchTiming(
                (loaded_at - started) * 1000,
                (searched_at - loaded_at) * 1000,
                (ranked_at - searched_at) * 1000,
                (ranked_at - started) * 1000,
            ),
            pruned,
        )

    def _reconstruct(
        self, origin: Stop, destination: Stop, service_date: date,
        departure: timedelta, label: _Label,
    ) -> Itinerary:
        legs: list[RouteLeg] = []
        stops_by_id = load_connection_stops(self.database, label.rides)
        index = 0
        while index < len(label.rides):
            first = label.rides[index]
            last = first
            leg_rides = [first]
            index += 1
            while index < len(label.rides) and label.rides[index].trip_id == first.trip_id:
                last = label.rides[index]
                leg_rides.append(last)
                index += 1
            leg_origin = stops_by_id.get(first.from_stop_id)
            leg_destination = stops_by_id.get(last.to_stop_id)
            if leg_origin is None or leg_destination is None:
                raise RuntimeError("a routed stop disappeared during reconstruction")
            legs.append(RouteLeg(
                first.trip_id, first.route_id, first.route_name,
                leg_origin, leg_destination, first.departure_time, last.arrival_time,
                first.direction_id, build_leg_stops(leg_rides, stops_by_id),
            ))
        return Itinerary(
            origin, destination, service_date, departure, label.arrival, tuple(legs)
        )
