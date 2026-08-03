"""Reliability-aware multi-criteria RAPTOR using boarding rounds."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from time import perf_counter
from typing import Any

from src.reliability.classification import TIME_WINDOWS

from .mcraptor_index import McRaptorIndex
from .models import (
    Itinerary,
    ReliableAlternative,
    ReliableSearchResult,
    RouteLeg,
    SearchCacheStatistics,
    SearchDiagnosticCounters,
    SearchDiagnostics,
    SearchDiagnosticTimings,
    SearchTiming,
    Stop,
)
from .reconstruction import build_leg_stops, load_connection_stops
from .route_results import itinerary_identity
from .reliable import (
    DEFAULT_MAX_TRANSFERS,
    DEFAULT_SEARCH_HORIZON_MINUTES,
    DEFAULT_TIMEOUT_SECONDS,
    EPSILON,
    ReliableSearchTimeout,
)


@dataclass
class _McLabel:
    label_id: int
    stop_id: str
    arrival: timedelta
    reliability_cost: float
    boardings: int
    last_trip_id: str | None
    required_trip_id: str | None
    parent_id: int | None
    action_id: int | None
    active: bool = True


@dataclass(frozen=True)
class _RideAction:
    trip_id: str
    board_position: int
    alight_position: int
    selection: Any


@dataclass(frozen=True)
class _WalkAction:
    from_stop_id: str
    to_stop_id: str


def _dominates(left: _McLabel, right: _McLabel) -> bool:
    """Dominance for labels with compatible future boarding constraints."""
    if (
        left.stop_id != right.stop_id
        or left.last_trip_id != right.last_trip_id
        or left.required_trip_id != right.required_trip_id
        or left.boardings != right.boardings
    ):
        return False
    no_worse = (
        left.arrival <= right.arrival
        and left.reliability_cost <= right.reliability_cost
    )
    return no_worse and (
        left.arrival < right.arrival
        or left.reliability_cost < right.reliability_cost
    )


class McRaptorTransitSearch:
    """Timetable-native Pareto search with one route scan per pattern/round."""

    algorithm_name = "mc_raptor"

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
        include_diagnostics: bool = False,
        trip_loading_mode: str = "frontier",
    ) -> ReliableSearchResult:
        del trip_loading_mode  # McRAPTOR needs complete chains to build patterns.
        if limit is not None and limit < 1:
            raise ValueError("candidate limit must be positive")
        if max_transfers < 0 or max_extra_minutes < 0:
            raise ValueError("invalid reliable-search bounds")
        if search_horizon_minutes < 1 or timeout_seconds <= 0:
            raise ValueError("invalid reliable-search bounds")

        started = perf_counter()
        deadline = started + timeout_seconds
        timings = vars(SearchDiagnosticTimings()).copy()
        counters = vars(SearchDiagnosticCounters()).copy()
        caches = vars(SearchCacheStatistics()).copy()
        counters["algorithm"] = self.algorithm_name

        def diagnostics() -> SearchDiagnostics | None:
            if not include_diagnostics:
                return None
            timings["measured_search_ms"] = (perf_counter() - started) * 1000
            return SearchDiagnostics(
                SearchDiagnosticTimings(**timings),
                SearchDiagnosticCounters(**counters),
                SearchCacheStatistics(**caches),
            )

        def check_deadline() -> None:
            if perf_counter() >= deadline:
                raise ReliableSearchTimeout(
                    f"reliable search exceeded {timeout_seconds:g} seconds",
                    diagnostics(),
                    {"algorithm": self.algorithm_name, "timed_out": True},
                )

        origin = self.database.find_stop(origin_stop_id)
        destination = self.database.find_stop(destination_stop_id)
        if origin is None or destination is None:
            missing = origin_stop_id if origin is None else destination_stop_id
            raise ValueError(f"unknown stop_id: {missing}")
        if origin_stop_id == destination_stop_id:
            itinerary = Itinerary(
                origin, destination, service_date, departure_time, departure_time, ()
            )
            alternative = ReliableAlternative(itinerary, 1.0, 0.0, ())
            ended = perf_counter()
            return ReliableSearchResult(
                (alternative,),
                SearchTiming((ended - started) * 1000, 0.0, 0.0, (ended - started) * 1000),
                0,
                diagnostics(),
            )

        horizon = departure_time + timedelta(minutes=search_horizon_minutes)
        active_lookup = getattr(self.database, "active_service_ids", None)
        active = active_lookup(service_date) if callable(active_lookup) else None
        bulk_search = getattr(self.database, "bulk_search_data", None)
        if callable(bulk_search):
            departures, transfers, connections, phase_timings = bulk_search(
                departure_time,
                horizon,
                service_ids=active,
                include_trip_connections=True,
            )
            timings.update(phase_timings)
        else:
            bulk_departures = getattr(self.database, "bulk_departures_in_window", None)
            bulk_transfers = getattr(self.database, "bulk_transfers", None)
            bulk_trips = getattr(self.database, "bulk_trip_connections", None)
            if all(callable(item) for item in (bulk_departures, bulk_transfers, bulk_trips)):
                departures = bulk_departures(departure_time, horizon, service_ids=active)
                transfers = bulk_transfers()
                connections = bulk_trips({item.trip_id for item in departures})
            else:
                departures = []
                for stop in getattr(self.database, "stops", {}).values():
                    departures.extend(self.database.departures_from(
                        stop.stop_id, departure_time, limit=100000, service_ids=active
                    ))
                transfers = [
                    transfer
                    for stop_id in sorted(getattr(self.database, "stops", {}))
                    for transfer in self.database.transfers_from(stop_id)
                ]
                connections = [
                    connection
                    for trip_id in sorted({item.trip_id for item in departures})
                    for connection in self.database.trip_connections(trip_id, 0)
                ]
        if active is None:
            departures = [
                item for item in departures
                if self.calendar.operates(item.service_id, service_date)
            ]
        check_deadline()
        caches["unique_departures_loaded"] = len(set(departures))
        caches["unique_trips_loaded"] = len({item.trip_id for item in connections})
        caches["unique_connections_loaded"] = len(set(connections))
        caches["unique_transfers_loaded"] = len(transfers)

        preload = getattr(self.resolver, "preload", None)
        if callable(preload):
            keys = {
                (item.route_id, item.direction_id, window)
                for item in departures
                for window, _, _ in TIME_WINDOWS
            }
            caches["bulk_profile_query_count"] = preload(keys)

        index_started = perf_counter()
        index = McRaptorIndex.build(departures, connections, transfers)
        timings["index_building_ms"] = (perf_counter() - index_started) * 1000
        loaded_at = perf_counter()

        labels: dict[int, _McLabel] = {}
        actions: dict[int, _RideAction | _WalkAction] = {}
        bags: list[dict[tuple[str, str | None, str | None], list[int]]] = [dict()]
        next_label_id = 0
        next_action_id = 0
        pruned = 0
        fastest_destination: timedelta | None = None

        def insert(
            round_number: int,
            stop_id: str,
            arrival: timedelta,
            reliability_cost: float,
            last_trip_id: str | None,
            required_trip_id: str | None,
            parent_id: int | None,
            action: _RideAction | _WalkAction | None,
        ) -> tuple[int | None, bool]:
            nonlocal next_label_id, next_action_id, pruned, fastest_destination
            counters["labels_created"] += 1
            candidate = _McLabel(
                next_label_id, stop_id, arrival, reliability_cost, round_number,
                last_trip_id, required_trip_id, parent_id,
                next_action_id if action is not None else None,
            )
            key = (stop_id, last_trip_id, required_trip_id)
            bucket = bags[round_number].setdefault(key, [])
            active = [labels[item] for item in bucket if labels[item].active]
            if any(
                _dominates(other, candidate)
                or (
                    other.arrival == candidate.arrival
                    and abs(other.reliability_cost - candidate.reliability_cost) <= EPSILON
                )
                for other in active
            ):
                counters["labels_pruned"] += 1
                pruned += 1
                return None, False
            survivors: list[int] = []
            for other in active:
                if _dominates(candidate, other):
                    other.active = False
                    counters["labels_pruned"] += 1
                    pruned += 1
                else:
                    survivors.append(other.label_id)
            if action is not None:
                actions[next_action_id] = action
                next_action_id += 1
            labels[next_label_id] = candidate
            survivors.append(next_label_id)
            bags[round_number][key] = survivors
            next_label_id += 1
            counters["labels_inserted"] += 1
            counters["labels_accepted"] += 1
            counters["maximum_pareto_bag_size"] = max(
                counters["maximum_pareto_bag_size"], len(survivors)
            )
            if stop_id == destination_stop_id:
                fastest_destination = min(
                    fastest_destination or arrival, arrival
                )
            return candidate.label_id, True

        origin_label_id, _ = insert(
            0, origin_stop_id, departure_time, 0.0, None, None, None, None
        )

        def ids_at(round_number: int, stop_id: str) -> list[int]:
            return sorted(
                label_id
                for (bag_stop, _, _), bucket in bags[round_number].items()
                if bag_stop == stop_id
                for label_id in bucket
                if labels[label_id].active
            )

        def transfer_closure(round_number: int, initial_marked: set[str]) -> set[str]:
            marked = set(initial_marked)
            queue = deque(sorted(initial_marked))
            processed: set[tuple[int, str]] = set()
            while queue:
                stop_id = queue.popleft()
                for label_id in ids_at(round_number, stop_id):
                    state = (label_id, stop_id)
                    if state in processed:
                        continue
                    processed.add(state)
                    label = labels[label_id]
                    for transfer in index.transfers_by_stop.get(stop_id, ()):
                        counters["transfer_edges_relaxed"] += 1
                        if transfer["transfer_type"] == 3:
                            continue
                        target = transfer["to_stop_id"]
                        if target == stop_id:
                            continue
                        if transfer.get("from_trip_id") not in (None, label.last_trip_id):
                            continue
                        arrival = label.arrival + timedelta(
                            seconds=transfer.get("min_transfer_time") or 0
                        )
                        if arrival > horizon:
                            continue
                        _, improved = insert(
                            round_number, target, arrival, label.reliability_cost,
                            None, transfer.get("to_trip_id"), label_id,
                            _WalkAction(stop_id, target),
                        )
                        if improved:
                            marked.add(target)
                            queue.append(target)
            return marked

        marked = transfer_closure(0, {origin_stop_id})
        marked_counts = [len(marked)]
        scans_per_round: list[int] = []
        profile_cache: dict[tuple[str, int | None, timedelta], Any] = {}

        def profile(connection, _alight_stop):
            key = (connection.route_id, connection.direction_id, connection.arrival_time)
            if key not in profile_cache:
                caches["profile_cache_misses"] += 1
                caches["profile_resolver_calls"] += 1
                profile_cache[key] = self.resolver.resolve(*key)
                check_deadline()
            else:
                caches["profile_cache_hits"] += 1
            return profile_cache[key]

        max_boardings = max_transfers + 1
        for round_number in range(1, max_boardings + 1):
            check_deadline()
            bags.append({})
            collected: dict[int, int] = {}
            for stop_id in sorted(marked):
                for pattern_id, position in index.patterns_by_stop.get(stop_id, ()):
                    collected[pattern_id] = min(collected.get(pattern_id, position), position)
            counters["route_patterns_collected"] += len(collected)
            scans_per_round.append(len(collected))
            next_marked: set[str] = set()
            for pattern_id in sorted(collected):
                pattern = index.patterns[pattern_id]
                counters["route_pattern_scans"] += 1
                start_position = collected[pattern_id]
                for board_position in range(start_position, len(pattern.stops) - 1):
                    boarding_labels = ids_at(round_number - 1, pattern.stops[board_position])
                    if not boarding_labels:
                        continue
                    for trip in pattern.trips:
                        counters["trips_considered"] += 1
                        boarding = trip.connections[board_position]
                        search_cutoff = min(
                            horizon,
                            (
                                fastest_destination
                                + timedelta(minutes=max_extra_minutes)
                                if fastest_destination is not None
                                else horizon
                            ),
                        )
                        if boarding.departure_time > search_cutoff:
                            continue
                        for parent_id in boarding_labels:
                            parent = labels[parent_id]
                            if parent.last_trip_id == trip.trip_id:
                                continue
                            if parent.required_trip_id not in (None, trip.trip_id):
                                continue
                            rules = index.transfers_by_stop.get(parent.stop_id, ())
                            matching = [
                                rule for rule in rules
                                if rule["to_stop_id"] == parent.stop_id
                                and rule.get("from_trip_id") in (None, parent.last_trip_id)
                                and rule.get("to_trip_id") in (None, trip.trip_id)
                            ]
                            if any(rule["transfer_type"] == 3 for rule in matching):
                                continue
                            minimum = max(
                                (rule.get("min_transfer_time") or 0 for rule in matching),
                                default=0,
                            )
                            if boarding.departure_time < parent.arrival + timedelta(seconds=minimum):
                                continue
                            counters["trips_boarded"] += 1
                            for alight_position in range(board_position + 1, len(pattern.stops)):
                                connection = trip.connections[alight_position - 1]
                                counters["stop_time_entries_scanned"] += 1
                                if connection.arrival_time > search_cutoff:
                                    break
                                selection = profile(connection, connection.to_stop_id)
                                probability = (
                                    selection.profile.reliability_probability
                                    if selection.profile is not None else 0.0
                                )
                                cost = parent.reliability_cost - math.log(
                                    max(float(probability), EPSILON)
                                )
                                _, improved = insert(
                                    round_number,
                                    connection.to_stop_id,
                                    connection.arrival_time,
                                    cost,
                                    trip.trip_id,
                                    None,
                                    parent_id,
                                    _RideAction(
                                        trip.trip_id, board_position,
                                        alight_position, selection,
                                    ),
                                )
                                if improved and connection.to_stop_id != destination_stop_id:
                                    next_marked.add(connection.to_stop_id)
            counters["rounds_executed"] = round_number
            next_marked = transfer_closure(round_number, next_marked)
            marked_counts.append(len(next_marked))
            if not next_marked:
                break
            marked = next_marked

        counters["unique_routes_scanned_per_round"] = tuple(scans_per_round)
        counters["marked_stops_per_round"] = tuple(marked_counts)
        searched_at = perf_counter()

        destination_labels = [
            label
            for round_bag in bags
            for (stop_id, _, _), bucket in round_bag.items()
            if stop_id == destination_stop_id
            for label_id in bucket
            if (label := labels[label_id]).active
        ]
        if destination_labels:
            fastest = min(label.arrival for label in destination_labels)
            cutoff = fastest + timedelta(minutes=max_extra_minutes)
            destination_labels = [label for label in destination_labels if label.arrival <= cutoff]
            destination_labels = [
                label for label in destination_labels
                if not any(
                    other.label_id != label.label_id
                    and other.arrival <= label.arrival
                    and other.reliability_cost <= label.reliability_cost
                    and other.boardings <= label.boardings
                    and (
                        other.arrival < label.arrival
                        or other.reliability_cost < label.reliability_cost
                        or other.boardings < label.boardings
                    )
                    for other in destination_labels
                )
            ]
        counters["destination_labels_found"] = len(destination_labels)

        def reconstruct(label: _McLabel) -> ReliableAlternative:
            action_chain: list[_RideAction | _WalkAction] = []
            cursor = label
            while cursor.parent_id is not None:
                if cursor.action_id is not None:
                    action_chain.append(actions[cursor.action_id])
                cursor = labels[cursor.parent_id]
            action_chain.reverse()
            ride_actions = [item for item in action_chain if isinstance(item, _RideAction)]
            ride_connections = [
                connection
                for action in ride_actions
                for connection in index.trips_by_id[action.trip_id].connections[
                    action.board_position:action.alight_position
                ]
            ]
            stops = load_connection_stops(self.database, ride_connections)
            legs: list[RouteLeg] = []
            selections = []
            for action in ride_actions:
                trip = index.trips_by_id[action.trip_id]
                segment = trip.connections[action.board_position:action.alight_position]
                first, last = segment[0], segment[-1]
                legs.append(RouteLeg(
                    first.trip_id, first.route_id, first.route_name,
                    stops[first.from_stop_id], stops[last.to_stop_id],
                    first.departure_time, last.arrival_time, first.direction_id,
                    build_leg_stops(segment, stops),
                ))
                selections.append(action.selection)
            itinerary = Itinerary(
                origin, destination, service_date, departure_time,
                label.arrival, tuple(legs),
            )
            return ReliableAlternative(
                itinerary, math.exp(-label.reliability_cost),
                label.reliability_cost, tuple(selections),
            )

        alternatives: list[ReliableAlternative] = []
        seen = set()
        for label in sorted(destination_labels, key=lambda item: (
            item.reliability_cost, item.arrival, item.boardings, item.label_id
        )):
            alternative = reconstruct(label)
            identity = itinerary_identity(alternative.itinerary)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            alternatives.append(alternative)
        counters["candidate_itineraries"] = len(alternatives)
        counters["alternatives_reconstructed"] = len(alternatives)
        returned = alternatives if limit is None else alternatives[:limit]
        counters["alternatives_returned"] = len(returned)
        ranked_at = perf_counter()
        return ReliableSearchResult(
            tuple(returned),
            SearchTiming(
                (loaded_at - started) * 1000,
                (searched_at - loaded_at) * 1000,
                (ranked_at - searched_at) * 1000,
                (ranked_at - started) * 1000,
            ),
            pruned,
            diagnostics(),
        )
