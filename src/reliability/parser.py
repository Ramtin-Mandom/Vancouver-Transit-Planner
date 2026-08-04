"""Pure conversion from GTFS-Realtime protobuf messages to observations."""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

from .models import DelayObservation, ParseSummary, ScheduledStop

VANCOUVER = ZoneInfo("America/Vancouver")


class ScheduleLookup(Protocol):
    def scheduled_stop(
        self, trip_id: str, stop_id: str | None, stop_sequence: int | None
    ) -> ScheduledStop | None: ...


def _has(message, field: str) -> bool:
    try:
        return message.HasField(field)
    except (AttributeError, ValueError):
        value = getattr(message, field, None)
        return value not in (None, 0, "")


def _event_delay(event, scheduled_time, service_date) -> int | None:
    if event is None:
        return None
    if _has(event, "delay"):
        return int(event.delay)
    if _has(event, "time"):
        service_midnight = datetime.combine(service_date, time.min, tzinfo=VANCOUVER)
        scheduled_timestamp = (service_midnight + scheduled_time).timestamp()
        return round(int(event.time) - scheduled_timestamp)
    return None


def parse_feed(feed, lookup: ScheduleLookup) -> ParseSummary:
    timestamp = int(getattr(feed.header, "timestamp", 0))
    observed_at = datetime.fromtimestamp(timestamp, tz=UTC)
    observations: list[DelayObservation] = []
    trip_count = stop_count = malformed = unknown = unusable = 0

    for entity in getattr(feed, "entity", ()):
        if not _has(entity, "trip_update"):
            continue
        trip_count += 1
        update = entity.trip_update
        descriptor = update.trip
        trip_id = getattr(descriptor, "trip_id", "").strip()
        start_date = getattr(descriptor, "start_date", "").strip()
        if not trip_id or len(start_date) != 8:
            malformed += 1
            continue
        try:
            service_date = datetime.strptime(start_date, "%Y%m%d").date()
        except ValueError:
            malformed += 1
            continue

        for stop_update in getattr(update, "stop_time_update", ()):
            stop_count += 1
            stop_id = getattr(stop_update, "stop_id", "").strip() or None
            sequence = (
                int(stop_update.stop_sequence)
                if _has(stop_update, "stop_sequence")
                else None
            )
            if stop_id is None and sequence is None:
                malformed += 1
                continue
            scheduled = lookup.scheduled_stop(trip_id, stop_id, sequence)
            if scheduled is None:
                unknown += 1
                continue
            arrival = getattr(stop_update, "arrival", None)
            departure = getattr(stop_update, "departure", None)
            delay = _event_delay(arrival, scheduled.scheduled_arrival, service_date)
            if delay is None:
                delay = _event_delay(
                    departure,
                    scheduled.scheduled_departure or scheduled.scheduled_arrival,
                    service_date,
                )
            if delay is None:
                unusable += 1
                continue
            observations.append(
                DelayObservation(
                    trip_id=trip_id,
                    stop_id=scheduled.stop_id,
                    stop_sequence=scheduled.stop_sequence,
                    service_date=service_date,
                    scheduled_arrival=scheduled.scheduled_arrival,
                    observed_at=observed_at,
                    delay_seconds=delay,
                )
            )
    return ParseSummary(
        feed_timestamp=observed_at,
        observations=tuple(observations),
        trip_updates_processed=trip_count,
        stop_updates_processed=stop_count,
        malformed=malformed,
        unknown=unknown,
        unusable_delay=unusable,
    )


def decode_feed(payload: bytes):
    from google.protobuf.message import DecodeError
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(payload)
    except DecodeError as exc:
        raise ValueError("invalid GTFS-Realtime protobuf payload") from exc
    return feed
