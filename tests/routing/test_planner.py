from datetime import date, timedelta

from src.routing.models import Connection, Stop
from src.routing.planner import TransitPlanner
from src.routing.service_calendar import ServiceCalendar


MONDAY = date(2026, 7, 27)


def at(hours, minutes=0):
    return timedelta(hours=hours, minutes=minutes)


def connection(trip, service, route, start, end, departure, arrival, sequence=1):
    return Connection(
        trip_id=trip,
        service_id=service,
        route_id=route,
        route_name=route,
        from_stop_id=start,
        to_stop_id=end,
        departure_time=departure,
        arrival_time=arrival,
        from_stop_sequence=sequence,
        to_stop_sequence=sequence + 1,
    )


class FakeDatabase:
    def __init__(self, stops, trips, rules=None, exceptions=None, transfers=None):
        self.stops = {stop.stop_id: stop for stop in stops}
        self.trips = trips
        self.rules = rules or {
            "weekday": {
                "service_id": "weekday",
                "monday": True,
                "tuesday": True,
                "wednesday": True,
                "thursday": True,
                "friday": True,
                "saturday": False,
                "sunday": False,
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 12, 31),
            }
        }
        self.exceptions = exceptions or {}
        self.transfers = transfers or {}

    def find_stop(self, stop_id):
        return self.stops.get(stop_id)

    def departures_from(
        self,
        stop_id,
        earliest_time,
        *,
        limit=64,
        offset=0,
        service_ids=None,
    ):
        departures = sorted(
            (
                hop
                for trip in self.trips.values()
                for hop in trip
                if hop.from_stop_id == stop_id
                and hop.departure_time >= earliest_time
                and (service_ids is None or hop.service_id in service_ids)
            ),
            key=lambda hop: hop.departure_time,
        )
        return departures[offset : offset + limit]

    def trip_connections(self, trip_id, from_stop_sequence):
        return [
            hop
            for hop in self.trips[trip_id]
            if hop.from_stop_sequence >= from_stop_sequence
        ]

    def transfers_from(self, stop_id):
        return self.transfers.get(stop_id, [])

    def calendar_rule(self, service_id):
        return self.rules.get(service_id)

    def calendar_exception(self, service_id, service_date):
        return self.exceptions.get((service_id, service_date))


def stops(*identifiers):
    return [Stop(identifier, f"Stop {identifier}") for identifier in identifiers]


def test_direct_journey_rides_through_consecutive_stops():
    trip = [
        connection("T1", "weekday", "10", "A", "B", at(8), at(8, 10), 1),
        connection("T1", "weekday", "10", "B", "C", at(8, 11), at(8, 20), 2),
    ]
    result = TransitPlanner(FakeDatabase(stops("A", "B", "C"), {"T1": trip})).plan(
        "A", "C", MONDAY, at(7, 55)
    )

    assert result is not None
    assert len(result.legs) == 1
    assert result.legs[0].origin.stop_id == "A"
    assert result.legs[0].destination.stop_id == "C"
    assert result.arrival_time == at(8, 20)
    assert result.transfer_count == 0


def test_one_transfer_journey_respects_minimum_transfer_time():
    first = [connection("T1", "weekday", "10", "A", "B", at(8), at(8, 10))]
    too_early = [
        connection("T2", "weekday", "20", "B", "C", at(8, 12), at(8, 20))
    ]
    valid = [connection("T3", "weekday", "30", "B", "C", at(8, 16), at(8, 25))]
    transfer = {
        "to_stop_id": "B",
        "transfer_type": 2,
        "min_transfer_time": 300,
        "from_trip_id": None,
        "to_trip_id": None,
    }
    database = FakeDatabase(
        stops("A", "B", "C"),
        {"T1": first, "T2": too_early, "T3": valid},
        transfers={"B": [transfer]},
    )

    result = TransitPlanner(database).plan("A", "C", MONDAY, at(7, 55))

    assert result is not None
    assert [leg.trip_id for leg in result.legs] == ["T1", "T3"]
    assert result.transfer_count == 1


def test_unreachable_destination_returns_none():
    database = FakeDatabase(stops("A", "B"), {})
    assert TransitPlanner(database).plan("A", "B", MONDAY, at(8)) is None


def test_inactive_service_date_is_not_routed():
    trip = [connection("T1", "weekday", "10", "A", "B", at(8), at(8, 10))]
    saturday = date(2026, 8, 1)
    database = FakeDatabase(stops("A", "B"), {"T1": trip})
    assert TransitPlanner(database).plan("A", "B", saturday, at(7)) is None


def test_calendar_exception_adds_and_removes_service():
    database = FakeDatabase(
        stops("A"),
        {},
        exceptions={("weekday", MONDAY): 2, ("weekday", date(2026, 8, 1)): 1},
    )
    calendar = ServiceCalendar(database)
    assert not calendar.operates("weekday", MONDAY)
    assert calendar.operates("weekday", date(2026, 8, 1))


def test_gtfs_time_greater_than_24_hours():
    trip = [
        connection(
            "T1", "weekday", "N10", "A", "B", at(25, 10), at(25, 30)
        )
    ]
    result = TransitPlanner(FakeDatabase(stops("A", "B"), {"T1": trip})).plan(
        "A", "B", MONDAY, at(25)
    )
    assert result is not None
    assert result.legs[0].departure_time == at(25, 10)
    assert result.arrival_time == at(25, 30)


def test_later_departures_are_not_scanned_after_destination_is_reached():
    first = [
        connection("EARLY", "weekday", "10", "A", "B", at(8), at(8, 20))
    ]
    later_trips = {
        f"LATE-{number}": [
            connection(
                f"LATE-{number}",
                "weekday",
                "10",
                "A",
                "B",
                at(9 + number),
                at(9 + number, 20),
            )
        ]
        for number in range(10)
    }

    class CountingDatabase(FakeDatabase):
        def __init__(self):
            super().__init__(
                stops("A", "B"), {"EARLY": first, **later_trips}
            )
            self.scanned_trip_ids = []

        def trip_connections(self, trip_id, from_stop_sequence):
            self.scanned_trip_ids.append(trip_id)
            return super().trip_connections(trip_id, from_stop_sequence)

    database = CountingDatabase()
    result = TransitPlanner(database).plan("A", "B", MONDAY, at(7, 59))

    assert result is not None
    assert result.arrival_time == at(8, 20)
    assert database.scanned_trip_ids == ["EARLY"]


def test_bulk_active_service_lookup_is_used_when_available():
    trip = [
        connection("T1", "inactive", "10", "A", "B", at(8), at(8, 20))
    ]

    class BulkCalendarDatabase(FakeDatabase):
        def active_service_ids(self, service_date):
            return {"inactive"}

        def calendar_rule(self, service_id):
            raise AssertionError("per-service calendar lookup should not run")

    database = BulkCalendarDatabase(stops("A", "B"), {"T1": trip}, rules={})
    result = TransitPlanner(database).plan("A", "B", MONDAY, at(7, 59))

    assert result is not None
    assert result.arrival_time == at(8, 20)


def test_plan_candidates_preserves_plan_and_deduplicates_trips():
    trips = {
        "T1": [connection("T1", "weekday", "10", "A", "B", at(8), at(8, 10))],
        "T2": [connection("T2", "weekday", "10", "A", "B", at(8, 15), at(8, 25))],
    }
    planner = TransitPlanner(FakeDatabase(stops("A", "B"), trips))
    original = planner.plan("A", "B", MONDAY, at(7, 55))
    candidates = planner.plan_candidates("A", "B", MONDAY, at(7, 55), limit=5)
    assert candidates[0] == original
    assert [item.legs[0].trip_id for item in candidates] == ["T1", "T2"]
