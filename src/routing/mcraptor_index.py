"""Request-local route-pattern index for McRAPTOR."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .models import Connection


@dataclass(frozen=True)
class PatternTrip:
    trip_id: str
    connections: tuple[Connection, ...]

    def departure_at(self, position: int) -> timedelta:
        return self.connections[position].departure_time


@dataclass(frozen=True)
class RoutePattern:
    pattern_id: int
    route_id: str
    direction_id: int | None
    stops: tuple[str, ...]
    trips: tuple[PatternTrip, ...]


@dataclass(frozen=True)
class McRaptorIndex:
    patterns: tuple[RoutePattern, ...]
    patterns_by_stop: dict[str, tuple[tuple[int, int], ...]]
    trips_by_id: dict[str, PatternTrip]
    transfers_by_stop: dict[str, tuple[dict[str, Any], ...]]

    @classmethod
    def build(
        cls,
        departures: list[Connection],
        connections: list[Connection],
        transfers: list[dict[str, Any]],
    ) -> "McRaptorIndex":
        eligible_trip_ids = {item.trip_id for item in departures}
        grouped_connections: dict[str, list[Connection]] = defaultdict(list)
        for connection in connections:
            if connection.trip_id in eligible_trip_ids:
                grouped_connections[connection.trip_id].append(connection)

        pattern_groups: dict[
            tuple[str, int | None, tuple[str, ...]], list[PatternTrip]
        ] = defaultdict(list)
        trips_by_id: dict[str, PatternTrip] = {}
        for trip_id in sorted(grouped_connections):
            ordered = tuple(sorted(
                grouped_connections[trip_id],
                key=lambda item: item.from_stop_sequence,
            ))
            if not ordered:
                continue
            stops = tuple(item.from_stop_id for item in ordered) + (
                ordered[-1].to_stop_id,
            )
            first = ordered[0]
            trip = PatternTrip(trip_id, ordered)
            trips_by_id[trip_id] = trip
            pattern_groups[(first.route_id, first.direction_id, stops)].append(trip)

        patterns: list[RoutePattern] = []
        by_stop: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for pattern_id, key in enumerate(sorted(
            pattern_groups,
            key=lambda item: (item[0], -1 if item[1] is None else item[1], item[2]),
        )):
            route_id, direction_id, stops = key
            trips = tuple(sorted(
                pattern_groups[key],
                key=lambda trip: (trip.departure_at(0), trip.trip_id),
            ))
            pattern = RoutePattern(pattern_id, route_id, direction_id, stops, trips)
            patterns.append(pattern)
            for position, stop_id in enumerate(stops[:-1]):
                by_stop[stop_id].append((pattern_id, position))

        transfer_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for transfer in transfers:
            transfer_groups[transfer["from_stop_id"]].append(transfer)
        return cls(
            tuple(patterns),
            {stop: tuple(sorted(values)) for stop, values in by_stop.items()},
            trips_by_id,
            {
                stop: tuple(sorted(values, key=lambda item: (
                    item["to_stop_id"], item.get("from_trip_id") or "",
                    item.get("to_trip_id") or "",
                )))
                for stop, values in transfer_groups.items()
            },
        )
