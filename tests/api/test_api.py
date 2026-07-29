from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import ApiServices
from src.api.main import app
from src.reliability.models import ProfileSelection
from src.routing.models import (
    Itinerary,
    ReliableAlternative,
    ReliableSearchResult,
    RouteLeg,
    SearchTiming,
    Stop,
)


ORIGIN = Stop("646", "Granville Station", "50001", Decimal("49.283"), Decimal("-123.117"))
DESTINATION = Stop("31", "UBC Exchange", "60001", Decimal("49.267"), Decimal("-123.247"))


class FakeTransitDatabase:
    def __init__(self):
        self.stops = {ORIGIN.stop_id: ORIGIN, DESTINATION.stop_id: DESTINATION}
        self.search_calls = []
        self.closed = False

    def search_stops(self, query, limit=20):
        self.search_calls.append((query, limit))
        return [ORIGIN][:limit]

    def find_stop(self, stop_id):
        return self.stops.get(stop_id)

    def close(self):
        self.closed = True


class FakeReliabilityDatabase:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def alternative(trip_id, departure_hour, arrival_hour, score):
    departure = timedelta(hours=departure_hour)
    arrival = timedelta(hours=arrival_hour)
    itinerary = Itinerary(
        origin=ORIGIN,
        destination=DESTINATION,
        service_date=date(2026, 7, 27),
        departure_time=departure,
        arrival_time=arrival,
        legs=(
            RouteLeg(
                trip_id=trip_id,
                route_id="R5",
                route_name="5",
                origin=ORIGIN,
                destination=DESTINATION,
                departure_time=departure,
                arrival_time=arrival,
                direction_id=0,
            ),
        ),
    )
    return ReliableAlternative(
        itinerary=itinerary,
        route_reliability=0.82,
        reliability_cost=0.2,
        profile_selections=(
            ProfileSelection(None, "route_direction_window", False),
        ),
        speed_component=0.94,
        combined_score=score,
    )


class FakePlanner:
    def __init__(self, alternatives=None):
        self.alternatives = alternatives or ()
        self.calls = []

    def get_ranked_route_result(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return ReliableSearchResult(
            alternatives=tuple(self.alternatives),
            timing=SearchTiming(1.0, 2.0, 3.0, 6.0),
            labels_pruned=0,
        )


@pytest.fixture
def api_services():
    return ApiServices(
        FakeTransitDatabase(),
        FakePlanner(
            (
                alternative("BEST", 8, 9, 90.0),
                alternative("SECOND", 9, 10, 80.0),
            )
        ),
        FakeReliabilityDatabase(),
    )


@pytest.fixture
def client(api_services):
    app.state.services = api_services
    app.state.owns_services = False
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.state.services = None


def valid_request(**overrides):
    payload = {
        "origin_stop_id": "646",
        "destination_stop_id": "31",
        "service_date": "2026-07-27",
        "departure_time": "08:00:00",
        "route_number": 5,
        "minimum_samples": 20,
        "max_extra_minutes": 30,
        "search_timeout_seconds": 30.0,
        "reliability_effect": 0.5,
        "travel_time_effect": 0.5,
        "transfer_effect": 0.0,
    }
    payload.update(overrides)
    return payload


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_successful_stop_search_trims_query_and_serializes_decimals(
    client, api_services
):
    response = client.get("/stops/search", params={"query": "  Gran  "})
    assert response.status_code == 200
    assert api_services.transit_database.search_calls == [("Gran", 10)]
    assert response.json() == [
        {
            "stop_id": "646",
            "stop_code": "50001",
            "stop_name": "Granville Station",
            "latitude": 49.283,
            "longitude": -123.117,
        }
    ]


@pytest.mark.parametrize("query", ["", " ", " a "])
def test_short_or_blank_stop_query_is_rejected(client, query):
    response = client.get("/stops/search", params={"query": query})
    assert response.status_code == 422


@pytest.mark.parametrize("limit", [0, 21])
def test_stop_search_limit_is_validated(client, limit):
    response = client.get(
        "/stops/search", params={"query": "station", "limit": limit}
    )
    assert response.status_code == 422


def test_successful_route_response_preserves_ranked_order(client, api_services):
    response = client.post("/routes/plan", json=valid_request())
    assert response.status_code == 200
    body = response.json()
    assert [item["legs"][0]["trip_id"] for item in body["alternatives"]] == [
        "BEST",
        "SECOND",
    ]
    assert [item["rank"] for item in body["alternatives"]] == [1, 2]
    assert body["alternatives"][0]["duration_display"] == "01:00:00"
    assert body["timing"]["total_ms"] == 6.0


def test_gtfs_departure_time_beyond_24_hours(client, api_services):
    response = client.post(
        "/routes/plan", json=valid_request(departure_time="25:10:00")
    )
    assert response.status_code == 200
    args, _ = api_services.planner.calls[-1]
    assert args[3] == timedelta(hours=25, minutes=10)
    assert response.json()["requested_departure_time"] == "25:10:00"


def test_invalid_gtfs_departure_time_is_rejected(client):
    response = client.post(
        "/routes/plan", json=valid_request(departure_time="25:99:00")
    )
    assert response.status_code == 422


def test_identical_origin_and_destination_is_rejected(client):
    response = client.post(
        "/routes/plan", json=valid_request(destination_stop_id="646")
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "weights",
    [
        {
            "reliability_effect": -1,
            "travel_time_effect": 1,
            "transfer_effect": 0,
        },
        {
            "reliability_effect": 0,
            "travel_time_effect": 0,
            "transfer_effect": 0,
        },
    ],
)
def test_invalid_ranking_weights_are_rejected(client, weights):
    response = client.post("/routes/plan", json=valid_request(**weights))
    assert response.status_code == 422


def test_unknown_stop_maps_to_404(client):
    response = client.post(
        "/routes/plan", json=valid_request(origin_stop_id="missing")
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown origin stop_id: missing"


def test_no_route_returns_empty_alternatives(client, api_services):
    api_services.planner.alternatives = ()
    response = client.post("/routes/plan", json=valid_request())
    assert response.status_code == 200
    assert response.json()["alternatives"] == []


def test_database_failure_does_not_leak_details(client, api_services):
    def fail(_stop_id):
        raise psycopg.OperationalError("password=do-not-expose")

    api_services.transit_database.find_stop = fail
    response = client.post("/routes/plan", json=valid_request())
    assert response.status_code == 503
    assert "do-not-expose" not in response.text


def test_api_services_close_both_dependencies():
    transit = FakeTransitDatabase()
    reliability = FakeReliabilityDatabase()
    services = ApiServices(transit, FakePlanner(), reliability)
    services.close()
    assert transit.closed
    assert reliability.closed
