from datetime import datetime, timedelta, timezone

import pytest

from fake_http import FakeHTTP, google_transit_response
from integrations import transit
from integrations.routes import get_route, get_walking_times
from schemas import RouteLeg, ToolResult

VALHALLA = "valhalla1.openstreetmap.de"
OSRM = "routing.openstreetmap.de"
GOOGLE = "routes.googleapis.com"

START = {"id": "start", "lat": 40.7853, "lng": -73.9693}
EL_DORADO = {"id": "wiki:9238071", "lat": 40.7883, "lng": -73.9675}
BERESFORD = {"id": "wiki:5667636", "lat": 40.7825, "lng": -73.9719}
WASHINGTON_SQUARE = {"id": "destination", "lat": 40.7308, "lng": -73.9973}

VALHALLA_TWO_LEGS = {"trip": {"legs": [
    {"summary": {"time": 270, "length": 0.38}, "maneuvers": [
        {"type": 1, "instruction": "Walk northeast on Central Park West.", "street_names": ["Central Park West"]},
        {"type": 10, "instruction": "Turn left onto the walkway."},
        {"type": 4, "instruction": "You have arrived at your destination."}]},
    {"summary": {"time": 570, "length": 0.8}, "maneuvers": [
        {"type": 1, "instruction": "Walk southwest on the walkway."},
        {"type": 15, "instruction": "Turn left onto West 81st Street.", "street_names": ["West 81st Street"]},
        {"type": 5, "instruction": "Your destination is on the right."}]},
]}}

OSRM_ONE_LEG = {"code": "Ok", "routes": [{"legs": [{"duration": 732, "distance": 1052, "steps": [
    {"name": "Columbus Avenue", "maneuver": {"type": "depart", "bearing_after": 208}},
    {"name": "", "maneuver": {"type": "turn", "modifier": "right"}},
    {"name": "Amsterdam Avenue", "maneuver": {"type": "turn", "modifier": "left"}},
    {"name": "Amsterdam Avenue", "maneuver": {"type": "arrive"}},
]}]}]}


def walk(seconds, km=1.0):
    return {"trip": {"legs": [{"summary": {"time": seconds, "length": km}, "maneuvers": [
        {"type": 1, "instruction": "Walk south on Central Park West.", "street_names": ["Central Park West"]}]}]}}


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    transit.reset_for_tests()
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")


def soon():
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0)


# --- Walking ---


def test_multi_stop_walk_becomes_one_route_leg_per_consecutive_pair():
    with FakeHTTP({VALHALLA: [(200, VALHALLA_TWO_LEGS)]}):
        result = get_route([START, EL_DORADO, BERESFORD], modes=["walk"], depart_at="2026-10-01T15:00:00-04:00")

    ToolResult.model_validate(result)
    first, second = [RouteLeg.model_validate(leg) for leg in result["data"]["legs"]]
    assert (first.from_id, first.to_id, second.from_id, second.to_id) == ("start", "wiki:9238071", "wiki:9238071", "wiki:5667636")
    assert (first.duration_minutes, first.distance_m, first.actual_modes) == (4.5, 380, ["walk"])
    assert first.instructions == ["Walk northeast on Central Park West.", "Turn left onto the walkway."]
    assert result["data"]["details"][1]["via_streets"] == ["West 81st Street"]
    assert first.source == "valhalla-fossgis" and first.uncertainty
    assert result["data"]["travel_minutes"] == 14.0
    assert result["freshness"]["kind"] == "static_reference"


def test_dwell_time_delays_the_next_leg_and_a_zoneless_time_means_new_york():
    with FakeHTTP({VALHALLA: [(200, VALHALLA_TWO_LEGS)]}):
        result = get_route([START, {**EL_DORADO, "dwell_minutes": 10}, BERESFORD], modes=["walk"],
                           depart_at="2026-10-01T15:00:00")

    first, second = result["data"]["legs"]
    assert first["depart_at"] == "2026-10-01T15:00:00-04:00"
    assert first["arrive_at"] == "2026-10-01T15:04:30-04:00"
    assert second["depart_at"] == "2026-10-01T15:14:30-04:00"  # 10 minutes at the El Dorado
    assert result["data"]["arrive_at"] == "2026-10-01T15:24:00-04:00"


def test_point_outside_the_city_is_rejected_before_any_request():
    with FakeHTTP({}) as http:
        result = get_route([START, {"id": "london", "lat": 51.5074, "lng": -0.1278}])

    assert result["error"]["code"] == "OUTSIDE_COVERAGE"
    assert http.calls == []


def test_car_only_and_unknown_modes_are_rejected():
    with FakeHTTP({}) as http:
        car = get_route([START, EL_DORADO], modes=["car"])
        boat = get_route([START, EL_DORADO], modes=["ferry"])

    assert car["error"]["code"] == boat["error"]["code"] == "INVALID_ARGUMENT"
    assert http.calls == []


def test_valhalla_outage_falls_back_to_osrm():
    with FakeHTTP({VALHALLA: [(503, None)], OSRM: [(200, OSRM_ONE_LEG)]}):
        result = get_route([START, EL_DORADO], modes=["walk"])

    leg = result["data"]["legs"][0]
    assert leg["source"] == "osrm-fossgis-foot"
    assert leg["instructions"] == ["Walk southwest on Columbus Avenue.", "Turn right onto the walkway.",
                                   "Turn left onto Amsterdam Avenue."]


def test_disconnected_points_are_no_match_not_an_outage():
    no_path = {"error_code": 442, "error": "No path could be found for input", "status_code": 400}
    with FakeHTTP({VALHALLA: [(400, no_path)]}) as http:
        result = get_route([START, EL_DORADO], modes=["walk"])

    assert result["error"]["code"] == "NO_MATCH"
    assert all(VALHALLA in url for url in http.urls())


def test_both_walking_routers_down_is_a_retryable_upstream_error():
    with FakeHTTP({VALHALLA: [(502, None)], OSRM: [(502, None)]}):
        result = get_route([START, EL_DORADO], modes=["walk"])

    assert result["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert result["error"]["retryable"]


# --- Transit ---


def test_long_leg_rides_the_subway_and_counts_the_wait():
    ready = soon()
    answers = {VALHALLA: [(200, walk(90 * 60, 7.2))], GOOGLE: [(200, google_transit_response(ready))]}
    with FakeHTTP(answers) as http:
        result = get_route([START, WASHINGTON_SQUARE], depart_at=ready.isoformat())

    assert result["ok"], result
    leg = RouteLeg.model_validate(result["data"]["legs"][0])
    assert leg.actual_modes == ["walk", "transit"] and leg.source == "google-routes"
    # Ready now, train in 10 minutes, 14-minute ride, then a 400-second walk: door to door 30.7 minutes.
    assert leg.duration_minutes == 30.7
    ride = result["data"]["details"][0]["transit"]
    assert ride["wait_minutes"] == 7.8  # 10 minutes until the train, less the 130-second walk to it
    assert [s["line"] for s in ride["segments"] if s["mode"] == "subway"] == ["A"]
    assert "Take the A train toward Far Rockaway-Mott Av from 86 St" in leg.instructions[1]
    assert result["freshness"]["kind"] == "scheduled"
    google_call = next(c for c in http.calls if GOOGLE in c["url"])
    assert google_call["headers"]["X-Goog-Api-Key"] == "test-key"


def test_short_walks_never_look_up_transit():
    with FakeHTTP({VALHALLA: [(200, walk(10 * 60))]}) as http:
        result = get_route([START, EL_DORADO])

    assert result["data"]["legs"][0]["actual_modes"] == ["walk"]
    assert not any(GOOGLE in url for url in http.urls())


def test_walking_wins_when_the_subway_saves_too_little():
    ready = soon()
    answers = {VALHALLA: [(200, walk(25 * 60))], GOOGLE: [(200, google_transit_response(ready, wait_s=120))]}
    with FakeHTTP(answers):
        result = get_route([START, WASHINGTON_SQUARE], depart_at=ready.isoformat())

    leg = result["data"]["legs"][0]
    assert leg["actual_modes"] == ["walk"]  # the ride is 22.7 minutes door to door against a 25-minute walk
    assert result["data"]["details"][0]["walking_minutes"] == 25.0


def test_disabled_routes_api_falls_back_to_walking_and_names_the_fix():
    disabled = {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [
        {"reason": "SERVICE_DISABLED", "metadata": {"consumer": "projects/agentic-ai-msds", "service": "routes.googleapis.com"}}]}}
    with FakeHTTP({VALHALLA: [(200, walk(40 * 60, 3.2))], GOOGLE: [(403, disabled)]}):
        result = get_route([START, WASHINGTON_SQUARE])

    assert result["ok"]
    assert result["data"]["legs"][0]["actual_modes"] == ["walk"]
    assert "Routes API is not enabled for projects/agentic-ai-msds" in result["warnings"][0]


def test_walk_only_request_never_calls_google_even_for_long_legs():
    with FakeHTTP({VALHALLA: [(200, walk(90 * 60, 7.2))]}) as http:
        result = get_route([START, WASHINGTON_SQUARE], modes=["walk"])

    assert result["data"]["legs"][0]["duration_minutes"] == 90.0
    assert not any(GOOGLE in url for url in http.urls())


# --- Walking times ---


def test_unreachable_destination_gets_no_guessed_time():
    matrix = {"sources_to_targets": [[
        {"to_index": 1, "time": None, "distance": None},
        {"to_index": 0, "time": 269, "distance": 0.38},
    ]]}
    with FakeHTTP({VALHALLA: [(200, matrix)]}):
        result = get_walking_times(START, [EL_DORADO, BERESFORD])

    assert result["data"]["times"] == [
        {"to_id": "wiki:9238071", "duration_minutes": 4.5, "distance_m": 380},
        {"to_id": "wiki:5667636", "duration_minutes": None, "distance_m": None},
    ]
    assert "unreachable" in result["warnings"][0]
