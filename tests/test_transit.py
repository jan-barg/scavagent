from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from fake_http import FakeHTTP, google_transit_response
from integrations import transit
from integrations.common import NoRoute, UpstreamError

GOOGLE = "routes.googleapis.com"
CPW_86 = (40.7853, -73.9693)
WASHINGTON_SQUARE = (40.7308, -73.9973)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    transit.reset_for_tests()
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.delenv("SCAVAGENT_ROUTES_PROJECT", raising=False)
    monkeypatch.delenv("SCAVAGENT_ROUTES_DAILY_LIMIT", raising=False)


def soon():
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0)


def test_door_to_door_time_starts_when_the_user_is_ready():
    ready = soon()
    leg = transit.parse_route(google_transit_response(ready)["routes"][0], ready)

    assert leg["has_transit"]
    assert leg["leave_by"] == (ready + timedelta(seconds=600 - 130)).isoformat()
    assert leg["duration_minutes"] == 30.7  # includes the 7.8-minute wait Google's own duration leaves out
    assert leg["fare"] == "$3.00"
    walk_to_train, train, walk_out = leg["segments"]
    assert walk_to_train["notes"] == ["Take entrance Central Park West & 86th St at NW corner"]
    assert (train["line"], train["stops"], train["headway_minutes"]) == ("A", 9, 20)
    assert leg["instructions"][0].startswith("Walk 2 min to 86 St (Take entrance")
    assert leg["instructions"][2].startswith("Walk 7 min to the destination (Take exit")


def test_a_walk_only_answer_is_reported_as_no_transit():
    route = {"duration": "1500s", "distanceMeters": 2000, "legs": [{"steps": [
        {"travelMode": "WALK", "staticDuration": "1500s", "distanceMeters": 2000}]}]}
    leg = transit.parse_route(route, soon())

    assert not leg["has_transit"]
    assert leg["duration_minutes"] == 25.0


def test_the_same_request_within_ten_minutes_is_answered_from_cache():
    ready = soon()
    with FakeHTTP({GOOGLE: [(200, google_transit_response(ready))]}) as http:
        first = transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready)
        second = transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready)

    assert first == second
    assert len(http.calls) == 1


def test_daily_limit_stops_lookups_before_calling_google(monkeypatch):
    monkeypatch.setenv("SCAVAGENT_ROUTES_DAILY_LIMIT", "1")
    ready = soon()
    with FakeHTTP({GOOGLE: [(200, google_transit_response(ready))]}) as http:
        transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready)
        with pytest.raises(UpstreamError) as limit:
            transit.transit_leg(WASHINGTON_SQUARE, CPW_86, ready)

    assert not limit.value.retryable
    assert len(http.calls) == 1


def test_no_route_answer_raises_no_route():
    with FakeHTTP({GOOGLE: [(200, {})]}):
        with pytest.raises(NoRoute):
            transit.transit_leg(CPW_86, WASHINGTON_SQUARE, soon())


def test_departure_outside_googles_window_is_refused_without_a_call():
    with FakeHTTP({}) as http:
        with pytest.raises(UpstreamError):
            transit.transit_leg(CPW_86, WASHINGTON_SQUARE, datetime.now(timezone.utc) - timedelta(days=8))
    assert http.calls == []


def test_missing_credentials_are_a_clear_non_retryable_error(monkeypatch):
    import google.auth

    monkeypatch.delenv("GOOGLE_MAPS_API_KEY")

    def no_credentials(**kwargs):
        raise google.auth.exceptions.DefaultCredentialsError("none")

    monkeypatch.setattr(google.auth, "default", no_credentials)
    with FakeHTTP({}) as http:
        with pytest.raises(UpstreamError) as error:
            transit.transit_leg(CPW_86, WASHINGTON_SQUARE, soon())

    assert "no usable Google credentials" in str(error.value) and not error.value.retryable
    assert http.calls == []


@pytest.mark.parametrize("configured, expected", [("kc3936-ieor4570-p1", "kc3936-ieor4570-p1"), (None, "agentic-ai-msds")])
def test_user_credentials_bill_to_the_configured_project(monkeypatch, configured, expected):
    import google.auth

    monkeypatch.delenv("GOOGLE_MAPS_API_KEY")
    if configured:
        monkeypatch.setenv("SCAVAGENT_ROUTES_PROJECT", configured)
    creds = SimpleNamespace(valid=True, token="user-token", quota_project_id="agentic-ai-msds")
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (creds, "agentic-ai-msds"))
    ready = soon()
    with FakeHTTP({GOOGLE: [(200, google_transit_response(ready))]}) as http:
        transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready)

    headers = http.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer user-token"
    assert headers["x-goog-user-project"] == expected


def test_a_cached_route_is_retimed_for_each_caller_and_refetched_once_its_train_has_left():
    ready = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(second=0, microsecond=0)
    # The train leaves 150 s after `ready`, and the walk to it takes 130 s: leave by ready + 20 s.
    answers = {GOOGLE: [(200, google_transit_response(ready, wait_s=150)), (200, google_transit_response(ready, wait_s=300))]}
    with FakeHTTP(answers) as http:
        first = transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready)
        early = transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready + timedelta(seconds=10))
        late = transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready + timedelta(seconds=55))

    assert early["duration_minutes"] == round(first["duration_minutes"] - 10 / 60, 1)  # same train, re-timed
    assert len(http.calls) == 2  # the 55-second caller would have missed it, so Google was asked again
    assert late["leave_by"] != first["leave_by"]


def test_an_answer_that_leaves_before_the_user_is_ready_is_not_used():
    ready = soon()
    already_gone = google_transit_response(ready, wait_s=60, walk_before_s=300)  # leave by 4 minutes ago
    with FakeHTTP({GOOGLE: [(200, already_gone)]}):
        with pytest.raises(UpstreamError):
            transit.transit_leg(CPW_86, WASHINGTON_SQUARE, ready)
