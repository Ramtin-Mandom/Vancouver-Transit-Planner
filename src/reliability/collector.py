"""Coordinate realtime download, parsing, and persistence."""

from __future__ import annotations

from contextlib import nullcontext

from .models import CollectionSummary
from .parser import decode_feed, parse_feed


def collect_snapshot(client, database) -> CollectionSummary:
    feed = decode_feed(client.download())
    trip_ids = {
        getattr(entity.trip_update.trip, "trip_id", "")
        for entity in getattr(feed, "entity", ())
        if getattr(entity, "HasField", lambda name: False)("trip_update")
    }
    lookup_context = (
        database.lookup_session()
        if hasattr(database, "lookup_session")
        else nullcontext()
    )
    with lookup_context:
        if hasattr(database, "preload_schedules"):
            database.preload_schedules(trip_ids)
        parsed = parse_feed(feed, database)
    inserted, duplicates = database.insert_observations(parsed.observations)
    return CollectionSummary(
        feed_timestamp=parsed.feed_timestamp,
        trip_updates_processed=parsed.trip_updates_processed,
        stop_updates_processed=parsed.stop_updates_processed,
        inserted=inserted,
        duplicates=duplicates,
        malformed=parsed.malformed,
        unknown=parsed.unknown,
        unusable_delay=parsed.unusable_delay,
    )
