from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.reliability.models import ScheduledStop
from src.reliability.parser import decode_feed, parse_feed


class Message(SimpleNamespace):
    def HasField(self, name):
        return name in self.__dict__ and getattr(self, name) not in (None, 0, "")


class Lookup:
    def scheduled_stop(self, trip_id, stop_id, sequence):
        if trip_id != "T" or stop_id == "UNKNOWN":
            return None
        return ScheduledStop("T", stop_id or "S", sequence or 1, timedelta(hours=25))


def feed(*updates, timestamp=1782806400):
    trip = Message(trip_id="T", start_date="20260630")
    trip_update = Message(trip=trip, stop_time_update=list(updates))
    entity = Message(trip_update=trip_update)
    return Message(header=Message(timestamp=timestamp), entity=[entity])


def update(arrival=None, departure=None, stop_id="S", sequence=1):
    return Message(
        stop_id=stop_id,
        stop_sequence=sequence,
        arrival=arrival or Message(),
        departure=departure or Message(),
    )


def test_arrival_delay_and_negative_delay_are_preserved():
    parsed = parse_feed(feed(update(arrival=Message(delay=-30))), Lookup())
    assert parsed.observations[0].delay_seconds == -30


def test_departure_delay_fallback():
    parsed = parse_feed(feed(update(departure=Message(delay=90))), Lookup())
    assert parsed.observations[0].delay_seconds == 90


def test_absolute_timestamp_supports_gtfs_time_above_24_hours():
    service_date = date(2026, 6, 30)
    scheduled = datetime.combine(
        service_date, time.min, tzinfo=ZoneInfo("America/Vancouver")
    ) + timedelta(hours=25)
    predicted = int(scheduled.timestamp()) + 120
    parsed = parse_feed(feed(update(arrival=Message(time=predicted))), Lookup())
    assert parsed.observations[0].scheduled_arrival == timedelta(hours=25)
    assert parsed.observations[0].delay_seconds == 120


def test_unknown_and_unusable_updates_are_counted():
    parsed = parse_feed(
        feed(update(stop_id="UNKNOWN"), update(stop_id="S")), Lookup()
    )
    assert parsed.unknown == 1
    assert parsed.unusable_delay == 1
    assert parsed.stop_updates_processed == 2


def test_missing_stop_identity_is_malformed():
    parsed = parse_feed(feed(update(stop_id="", sequence=0)), Lookup())
    assert parsed.malformed == 1


def test_malformed_trip_does_not_crash_feed():
    entity = Message(
        trip_update=Message(
            trip=Message(trip_id="", start_date="bad"),
            stop_time_update=[],
        )
    )
    parsed = parse_feed(
        Message(
            header=Message(timestamp=1782806400),
            entity=[entity],
        ),
        Lookup(),
    )
    assert parsed.malformed == 1
    assert parsed.observations == ()


def test_programmatically_constructed_protobuf_fixture():
    from google.transit import gtfs_realtime_pb2

    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.timestamp = 1782806400
    entity = message.entity.add()
    entity.id = "synthetic"
    entity.trip_update.trip.trip_id = "T"
    entity.trip_update.trip.start_date = "20260630"
    stop = entity.trip_update.stop_time_update.add()
    stop.stop_id = "S"
    stop.stop_sequence = 1
    stop.arrival.delay = 45

    parsed = parse_feed(decode_feed(message.SerializeToString()), Lookup())
    assert parsed.observations[0].delay_seconds == 45
