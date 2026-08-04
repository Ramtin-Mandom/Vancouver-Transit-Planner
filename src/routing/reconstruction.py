"""Helpers shared by scheduled and reliability-aware route reconstruction."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .models import Connection, LegStop, Stop


def load_connection_stops(
    database: Any, connections: Iterable[Connection]
) -> dict[str, Stop]:
    """Load stop metadata in one batch when supported by the repository."""
    stop_ids = {
        stop_id
        for connection in connections
        for stop_id in (connection.from_stop_id, connection.to_stop_id)
    }
    find_stops = getattr(database, "find_stops", None)
    if callable(find_stops):
        return find_stops(stop_ids)

    # Compatibility for small test/integration repositories implementing the
    # older protocol. Production TransitDatabase always uses the batch path.
    return {
        stop_id: stop
        for stop_id in stop_ids
        if (stop := database.find_stop(stop_id)) is not None
    }


def build_leg_stops(
    connections: Sequence[Connection], stops_by_id: dict[str, Stop]
) -> tuple[LegStop, ...]:
    """Build the ordered stop-time slice represented by consecutive hops."""
    if not connections:
        return ()

    result: list[LegStop] = []
    for index, connection in enumerate(connections):
        stop = stops_by_id.get(connection.from_stop_id)
        if stop is None:
            raise RuntimeError("a routed stop disappeared during reconstruction")
        result.append(
            LegStop(
                stop=stop,
                stop_sequence=connection.from_stop_sequence,
                arrival_time=(
                    connection.from_arrival_time
                    if connection.from_arrival_time is not None
                    else (connections[index - 1].arrival_time if index > 0 else None)
                ),
                departure_time=connection.departure_time,
            )
        )

    last = connections[-1]
    destination = stops_by_id.get(last.to_stop_id)
    if destination is None:
        raise RuntimeError("a routed stop disappeared during reconstruction")
    result.append(
        LegStop(
            stop=destination,
            stop_sequence=last.to_stop_sequence,
            arrival_time=last.arrival_time,
            departure_time=last.to_departure_time,
        )
    )
    return tuple(result)
