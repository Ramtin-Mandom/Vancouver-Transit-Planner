from datetime import date, timedelta

from src.routing.integration_cases import IntegrationCase
from src.routing.integration_report import (
    IntegrationResult,
    IntegrationSummary,
    print_case_result,
    print_summary,
)
from src.routing.integration_runner import exit_status, run_cases, validate_itinerary
from src.routing.models import Itinerary, RouteLeg, Stop


def at(hours, minutes=0):
    return timedelta(hours=hours, minutes=minutes)


def sample_case(departure=None):
    return IntegrationCase(
        case_number=1,
        origin_stop_id="A",
        origin_stop_name="Alpha",
        destination_stop_id="B",
        destination_stop_name="Beta",
        service_date=date(2026, 7, 27),
        departure_time=departure or at(8),
        source_trip_id="SOURCE",
        source_route_id="R1",
        source_route_name="1",
        scheduled_source_arrival=(departure or at(8)) + timedelta(minutes=20),
    )


def itinerary(trip_id="ALTERNATIVE", departure=None):
    requested = (departure or at(8)) - timedelta(minutes=1)
    origin = Stop("A", "Alpha")
    destination = Stop("B", "Beta")
    leg = RouteLeg(
        trip_id=trip_id,
        route_id="R2",
        route_name="2",
        origin=origin,
        destination=destination,
        departure_time=departure or at(8),
        arrival_time=(departure or at(8)) + timedelta(minutes=15),
    )
    return Itinerary(
        origin=origin,
        destination=destination,
        service_date=date(2026, 7, 27),
        departure_time=requested,
        arrival_time=leg.arrival_time,
        legs=(leg,),
    )


class FakeDatabase:
    def __init__(self, case, references_exist=True):
        self.case = case
        self.references_exist = references_exist

    def integration_case_rows(self, limit):
        case = self.case
        return [
            {
                "origin_stop_id": case.origin_stop_id,
                "origin_stop_name": case.origin_stop_name,
                "destination_stop_id": case.destination_stop_id,
                "destination_stop_name": case.destination_stop_name,
                "service_date": case.service_date,
                "departure_time": case.departure_time,
                "source_trip_id": case.source_trip_id,
                "source_route_id": case.source_route_id,
                "source_route_name": case.source_route_name,
                "scheduled_source_arrival": case.scheduled_source_arrival,
            }
        ][:limit]

    def itinerary_leg_exists(self, leg):
        return self.references_exist


class FakePlanner:
    def __init__(self, result):
        self.result = result

    def plan(self, *args):
        return self.result


def test_case_validation_accepts_a_different_trip_from_sample():
    case = sample_case()
    result = itinerary(trip_id="NOT-SOURCE")
    failures = validate_itinerary(
        FakeDatabase(case), case, at(7, 59), result
    )
    assert failures == []


def test_case_validation_reports_invalid_result():
    case = sample_case()
    result = itinerary()
    result = Itinerary(
        origin=Stop("WRONG", "Wrong"),
        destination=result.destination,
        service_date=result.service_date,
        departure_time=result.departure_time,
        arrival_time=result.arrival_time,
        legs=result.legs,
    )
    failures = validate_itinerary(
        FakeDatabase(case, references_exist=False), case, at(7, 59), result
    )
    assert any("origin" in failure for failure in failures)
    assert any("absent from GTFS" in failure for failure in failures)


def test_pass_report_formatting(capsys):
    case = sample_case()
    report = IntegrationResult(case, at(7, 59), itinerary(), (), 0.25)
    print_case_result(report)
    output = capsys.readouterr().out
    assert "Test 1: PASS" in output
    assert "trip ALTERNATIVE" in output
    assert "Transfers: 0" in output


def test_fail_report_formatting(capsys):
    case = sample_case()
    report = IntegrationResult(
        case, at(7, 59), None, ("planner returned no itinerary",), 0.1
    )
    print_case_result(report)
    output = capsys.readouterr().out
    assert "Test 1: FAIL" in output
    assert "Validation failure: planner returned no itinerary" in output


def test_summary_counts_and_exit_status(capsys):
    passing = IntegrationSummary(1, 0, 0, 1, 0.5)
    failing = IntegrationSummary(0, 1, 0, 1, 0.5)
    print_summary(passing)
    assert "Passed: 1" in capsys.readouterr().out
    assert exit_status(passing) == 0
    assert exit_status(failing) == 1
    assert exit_status(IntegrationSummary(1, 0, 2, 1, 0.5)) == 0


def test_runner_status_uses_validation_result():
    case = sample_case()
    _, summary = run_cases(
        FakeDatabase(case), FakePlanner(itinerary()), limit=1
    )
    assert summary.passed == 1
    assert summary.failed == 0


def test_times_above_24_hours_are_preserved(capsys):
    case = sample_case(at(25, 10))
    result = itinerary(departure=at(25, 10))
    failures = validate_itinerary(
        FakeDatabase(case), case, at(25, 9), result
    )
    assert failures == []
    print_case_result(IntegrationResult(case, at(25, 9), result, (), 0.1))
    assert "25:10:00" in capsys.readouterr().out
