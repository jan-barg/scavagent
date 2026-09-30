import re
import time

import pytest
import requests

from fake_http import FakeHTTP
from integrations import geocoding
from integrations.geocoding import geocode_place, normalize_street, parse_intersection, street_pattern
from schemas import LocationContext, ToolResult

OVERPASS = "overpass-api.de"
OVERPASS_MIRROR = "maps.mail.ru"
NOMINATIM = "nominatim.openstreetmap.org"
GEOSEARCH = "geosearch.planninglabs.nyc"


@pytest.fixture(autouse=True)
def fresh_geocoder():
    geocoding._shared_nodes.cache_clear()
    geocoding._last_nominatim_call = float("-inf")


def nodes(*points):
    return (200, {"elements": [{"type": "node", "id": i + 1, "lat": lat, "lon": lng} for i, (lat, lng) in enumerate(points)]})


@pytest.mark.parametrize("typed, osm_name, expected", [
    ("W 86th St", "West 86th Street", True),
    ("w. 86 st.", "West 86th Street", True),
    ("86th", "West 86th Street", True),
    ("86th", "East 86th Street", True),
    ("86th", "West 186th Street", False),
    ("W 86th St", "East 86th Street", False),
    ("CPW", "Central Park West", True),
    ("Fifth Ave", "5th Avenue", True),
    ("5th Ave", "Fifth Avenue", True),
    ("6th Ave", "Avenue of the Americas", True),
    ("W 11 St", "West 11th Street", True),
    ("W 21 St", "West 21st Street", True),
    ("W 112 St", "West 112th Street", True),
    ("St Nicholas Ave", "Saint Nicholas Avenue", True),
    ("Adam Clayton Powell Jr Blvd", "Adam Clayton Powell Jr. Boulevard", True),
])
def test_typed_street_names_match_openstreetmap_names(typed, osm_name, expected):
    pattern = street_pattern(normalize_street(typed))
    assert bool(re.fullmatch(pattern, osm_name, re.IGNORECASE)) == expected


def test_only_two_street_names_count_as_an_intersection():
    assert parse_intersection("Central Park West and West 86th Street, New York, NY") == ["central park west", "west 86th street"]
    assert parse_intersection("corner of Broadway & 72nd") == ["broadway", "72nd street"]
    assert parse_intersection("Barnes & Noble") is None
    assert parse_intersection("American Museum of Natural History") is None


def test_intersection_resolves_to_the_shared_street_node_as_a_location():
    with FakeHTTP({OVERPASS: [nodes((40.78530, -73.96934), (40.78532, -73.96938))]}) as http:
        result = geocode_place("Central Park West and West 86th Street")

    ToolResult.model_validate(result)
    assert result["ok"], result
    data = result["data"]
    location = LocationContext.model_validate(data["location"])  # usable as AdventureRequest.start
    assert location.source == "geocoded"
    assert location.place_text == "Central Park West & West 86th Street"
    assert location.point.lat == pytest.approx(40.78531, abs=1e-5)
    assert data["match_type"] == "intersection"
    assert data["provider_ref"] == "osm:node/1"
    assert result["freshness"]["kind"] == "static_reference"
    assert result["warnings"] == []
    assert len(http.calls) == 1


def test_streets_meeting_in_two_places_are_flagged_for_confirmation():
    # Two meeting points about 2 km apart: the tool must not silently pick one.
    with FakeHTTP({OVERPASS: [nodes((40.7787, -73.9820), (40.7960, -73.9710))]}):
        result = geocode_place("Broadway and 72nd Street")

    assert result["ok"]
    assert len(result["data"]["alternatives"]) == 1
    assert "separate places" in result["warnings"][0]


def test_rate_limited_overpass_falls_back_to_the_mirror():
    with FakeHTTP({OVERPASS: [(429, None)], OVERPASS_MIRROR: [nodes((40.78326, -73.97455))]}) as http:
        result = geocode_place("Columbus Ave & W 81st St")

    assert result["ok"], result
    assert result["data"]["match_type"] == "intersection"
    assert not any(NOMINATIM in url for url in http.urls())


def test_a_slow_overpass_server_is_raced_by_the_mirror(monkeypatch):
    # Live answers took 5-15 s; one turn waited 33 s for a timeout before the mirror was tried.
    answers = {geocoding.OVERPASS_URLS[0]: 1.0, geocoding.OVERPASS_URLS[1]: 0.0}  # seconds to answer

    def fake_fetch(provider, method, url, **kwargs):
        time.sleep(answers[url])
        return {"elements": [{"type": "node", "id": 7, "lat": 40.78326, "lon": -73.97455}]}

    monkeypatch.setattr(geocoding, "OVERPASS_HEDGE_S", 0.05)
    monkeypatch.setattr(geocoding, "fetch_json", fake_fetch)
    started = time.monotonic()
    result = geocode_place("Columbus Avenue and West 81st Street")

    assert result["ok"] and result["data"]["provider_ref"] == "osm:node/7"
    assert time.monotonic() - started < 0.5  # the mirror's answer, not the slow server's


def test_every_provider_down_is_a_retryable_upstream_error():
    answers = {OVERPASS: [(504, None)], OVERPASS_MIRROR: [(504, None)], NOMINATIM: [requests.ConnectionError("offline")]}
    with FakeHTTP(answers):
        result = geocode_place("Columbus Avenue and West 81st Street")

    assert not result["ok"]
    assert result["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert result["error"]["retryable"]


def test_unknown_place_is_no_match_with_a_next_step():
    with FakeHTTP({NOMINATIM: [(200, [])]}):
        result = geocode_place("The Glass Pagoda of Nowhere")

    assert result["error"]["code"] == "NO_MATCH"
    assert "cross street" in result["error"]["next_step"]


def test_address_uses_city_geosearch_and_flags_a_weak_match():
    feature = {"geometry": {"coordinates": [-73.975905, 40.776785]},
               "properties": {"label": "1 WEST 72 STREET, New York, NY, USA", "confidence": 0.6,
                              "addendum": {"pad": {"bbl": "1011250001"}}}}
    with FakeHTTP({GEOSEARCH: [(200, {"features": [feature]})]}):
        result = geocode_place("1 W 72nd St")

    assert result["ok"], result
    assert result["data"]["match_type"] == "address"
    assert result["data"]["provider_ref"] == "bbl:1011250001"
    assert "confirm" in result["warnings"][0]


def test_match_outside_the_city_is_outside_coverage():
    feature = {"geometry": {"coordinates": [-75.1652, 39.9526]}, "properties": {"label": "1 Market St", "confidence": 1}}
    with FakeHTTP({GEOSEARCH: [(200, {"features": [feature]})]}):
        result = geocode_place("1 Market Street")

    assert result["error"]["code"] == "OUTSIDE_COVERAGE"
