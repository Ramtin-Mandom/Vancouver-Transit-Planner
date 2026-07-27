"""Earliest-arrival scheduled transit routing."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import count
from typing import Any, Protocol

from .models import Connection, Itinerary, RouteLeg, Stop
from .service_calendar import ServiceCalendar


class RoutingRepository(Protocol):
    def find_stop(self, stop_id: str) -> Stop | None: ...

    def departures_from(
        self,
        stop_id: str,
        earliest_time: timedelta,
        *,
        limit: int = 64,
        offset: int = 0,
        service_ids: set[str] | None = None,
    ) -> list[Connection]: ...

    def trip_connections(
        self, trip_id: str, from_stop_sequence: int
    ) -> list[Connection]: ...

    def transfers_from(self, stop_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class _Ride:
    previous_stop_id: str
    connection: Connection


@dataclass(frozen=True)
class _Walk:
    previous_stop_id: str


class TransitPlanner:
    def __init__(
        self, database: RoutingRepository, calendar: ServiceCalendar | None = None
    ) -> None:
        self.database = database
        self.calendar = calendar or ServiceCalendar(database)  # type: ignore[arg-type]

    def plan(
        self,
        origin_stop_id: str,
        destination_stop_id: str,
        service_date: date,
        departure_time: timedelta,
    ) -> Itinerary | None:
        origin = self.database.find_stop(origin_stop_id)
        destination = self.database.find_stop(destination_stop_id)
        if origin is None:
            raise ValueError(f"unknown origin stop_id: {origin_stop_id}")
        if destination is None:
            raise ValueError(f"unknown destination stop_id: {destination_stop_id}")
        if origin_stop_id == destination_stop_id:
            return Itinerary(
                origin, destination, service_date, departure_time, departure_time, ()
            )

        active_service_lookup = getattr(
            self.database, "active_service_ids", None
        )
        active_service_ids = (
            active_service_lookup(service_date)
            if callable(active_service_lookup)
            else None
        )
        best: dict[str, timedelta] = {origin_stop_id: departure_time}
        previous: dict[str, _Ride | _Walk] = {}
        queue: list[tuple[timedelta, int, str]] = []
        sequence = count()
        heapq.heappush(queue, (departure_time, next(sequence), origin_stop_id))

        while queue:
            reached_at, _, stop_id = heapq.heappop(queue)
            if reached_at != best.get(stop_id):
                continue
            if stop_id == destination_stop_id:
                break

            self._relax_departures(
                stop_id,
                destination_stop_id,
                reached_at,
                service_date,
                active_service_ids,
                best,
                previous,
                queue,
                sequence,
            )
            self._relax_transfers(
                stop_id, reached_at, best, previous, queue, sequence
            )

        if destination_stop_id not in best:
            return None
        return self._reconstruct(
            origin,
            destination,
            service_date,
            departure_time,
            best[destination_stop_id],
            previous,
        )

    def plan_candidates(
        self,
        origin_stop_id: str,
        destination_stop_id: str,
        service_date: date,
        departure_time: timedelta,
        *,
        limit: int = 5,
        max_extra_minutes: int = 30,
    ) -> list[Itinerary]:
        """Return bounded, distinct scheduled alternatives.

        Successive earliest-arrival searches advance just beyond the first
        boarding of the previous result. This retains the proven scheduled
        router and produces non-cyclic alternatives without changing plan().
        """
        if limit < 1 or max_extra_minutes < 0:
            raise ValueError("candidate limit must be positive and extra time nonnegative")
        candidates: list[Itinerary] = []
        identifiers: set[tuple[tuple[str, str, str], ...]] = set()
        search_time = departure_time
        fastest_arrival: timedelta | None = None
        attempts = 0
        while len(candidates) < limit and attempts < limit * 4:
            attempts += 1
            itinerary = self.plan(
                origin_stop_id,
                destination_stop_id,
                service_date,
                search_time,
            )
            if itinerary is None:
                break
            if fastest_arrival is None:
                fastest_arrival = itinerary.arrival_time
            if itinerary.arrival_time > fastest_arrival + timedelta(
                minutes=max_extra_minutes
            ):
                break
            identifier = tuple(
                (
                    leg.trip_id,
                    leg.origin.stop_id,
                    leg.destination.stop_id,
                )
                for leg in itinerary.legs
            )
            if identifier and identifier not in identifiers:
                identifiers.add(identifier)
                candidates.append(itinerary)
            if not itinerary.legs:
                break
            next_search = itinerary.legs[0].departure_time + timedelta(seconds=1)
            if next_search <= search_time:
                break
            search_time = next_search
        return candidates

    def _relax_departures(
        self,
        stop_id: str,
        destination_stop_id: str,
        reached_at: timedelta,
        service_date: date,
        active_service_ids: set[str] | None,
        best: dict[str, timedelta],
        previous: dict[str, _Ride | _Walk],
        queue: list[tuple[timedelta, int, str]],
        sequence: count,
    ) -> None:
        prior = previous.get(stop_id)
        prior_trip_id = (
            prior.connection.trip_id if isinstance(prior, _Ride) else None
        )
        transfer_rules = self.database.transfers_from(stop_id)
        scanned_trips: set[str] = set()
        batch_size = 64
        offset = 0
        while True:
            departures = self.database.departures_from(
                stop_id,
                reached_at,
                limit=batch_size,
                offset=offset,
                service_ids=active_service_ids,
            )
            if not departures:
                break
            destination_reached = False
            for first in departures:
                # Departures are ordered. Once a known destination arrival is
                # no later than this departure, no later trip can improve it.
                if first.departure_time >= best.get(
                    destination_stop_id, timedelta.max
                ):
                    destination_reached = True
                    break
                operates = (
                    first.service_id in active_service_ids
                    if active_service_ids is not None
                    else self.calendar.operates(first.service_id, service_date)
                )
                if not operates:
                    continue
                if first.trip_id in scanned_trips:
                    continue
                scanned_trips.add(first.trip_id)
                if prior_trip_id and first.trip_id != prior_trip_id:
                    matching = [
                        rule
                        for rule in transfer_rules
                        if rule["to_stop_id"] == stop_id
                        and (rule.get("from_trip_id") in (None, prior_trip_id))
                        and (rule.get("to_trip_id") in (None, first.trip_id))
                    ]
                    if any(rule["transfer_type"] == 3 for rule in matching):
                        continue
                    minimum = max(
                        (rule["min_transfer_time"] or 0 for rule in matching),
                        default=0,
                    )
                    if first.departure_time < reached_at + timedelta(
                        seconds=minimum
                    ):
                        continue
                # Once boardable, scan only this trip's remaining stops.
                connections = self.database.trip_connections(
                    first.trip_id, first.from_stop_sequence
                )
                current_stop = stop_id
                current_time = reached_at
                for connection in connections:
                    if connection.from_stop_id != current_stop:
                        break
                    if connection.departure_time < current_time:
                        break
                    self._relax(
                        connection.to_stop_id,
                        connection.arrival_time,
                        _Ride(current_stop, connection),
                        best,
                        previous,
                        queue,
                        sequence,
                    )
                    current_stop = connection.to_stop_id
                    current_time = connection.arrival_time
            if destination_reached or len(departures) < batch_size:
                break
            offset += batch_size

    def _relax_transfers(
        self,
        stop_id: str,
        reached_at: timedelta,
        best: dict[str, timedelta],
        previous: dict[str, _Ride | _Walk],
        queue: list[tuple[timedelta, int, str]],
        sequence: count,
    ) -> None:
        for transfer in self.database.transfers_from(stop_id):
            if transfer["transfer_type"] == 3:
                continue
            if transfer["to_stop_id"] == stop_id:
                # Same-stop rules constrain boarding and are handled above.
                continue
            prior = previous.get(stop_id)
            prior_trip_id = (
                prior.connection.trip_id if isinstance(prior, _Ride) else None
            )
            if transfer.get("from_trip_id") not in (None, prior_trip_id):
                continue
            seconds = transfer["min_transfer_time"] or 0
            self._relax(
                transfer["to_stop_id"],
                reached_at + timedelta(seconds=seconds),
                _Walk(stop_id),
                best,
                previous,
                queue,
                sequence,
            )

    @staticmethod
    def _relax(
        stop_id: str,
        arrival: timedelta,
        step: _Ride | _Walk,
        best: dict[str, timedelta],
        previous: dict[str, _Ride | _Walk],
        queue: list[tuple[timedelta, int, str]],
        sequence: count,
    ) -> None:
        if arrival >= best.get(stop_id, timedelta.max):
            return
        best[stop_id] = arrival
        previous[stop_id] = step
        heapq.heappush(queue, (arrival, next(sequence), stop_id))

    def _reconstruct(
        self,
        origin: Stop,
        destination: Stop,
        service_date: date,
        requested_departure: timedelta,
        arrival: timedelta,
        previous: dict[str, _Ride | _Walk],
    ) -> Itinerary:
        rides: list[Connection] = []
        cursor = destination.stop_id
        while cursor != origin.stop_id:
            step = previous[cursor]
            if isinstance(step, _Ride):
                rides.append(step.connection)
            cursor = step.previous_stop_id
        rides.reverse()

        legs: list[RouteLeg] = []
        index = 0
        while index < len(rides):
            first = rides[index]
            last = first
            index += 1
            while index < len(rides) and rides[index].trip_id == first.trip_id:
                last = rides[index]
                index += 1
            leg_origin = self.database.find_stop(first.from_stop_id)
            leg_destination = self.database.find_stop(last.to_stop_id)
            if leg_origin is None or leg_destination is None:
                raise RuntimeError("a routed stop disappeared during reconstruction")
            legs.append(
                RouteLeg(
                    trip_id=first.trip_id,
                    route_id=first.route_id,
                    route_name=first.route_name,
                    origin=leg_origin,
                    destination=leg_destination,
                    departure_time=first.departure_time,
                    arrival_time=last.arrival_time,
                )
            )
        return Itinerary(
            origin=origin,
            destination=destination,
            service_date=service_date,
            departure_time=requested_departure,
            arrival_time=arrival,
            legs=tuple(legs),
        )
