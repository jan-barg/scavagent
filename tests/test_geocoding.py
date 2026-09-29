import re
import unittest

import requests

from integrations import geocoding
from integrations.geocoding import geocode_place, normalize_street, parse_intersection, street_pattern
from tests.fake_http import FakeHTTP

OVERPASS = "overpass-api.de"
OVERPASS_MIRROR = "maps.mail.ru"
NOMINATIM = "nominatim.openstreetmap.org"
GEOSEARCH = "geosearch.planninglabs.nyc"


def nodes(*points):
    return (200, {"elements": [{"type": "node", "id": i + 1, "lat": lat, "lon": lon} for i, (lat, lon) in enumerate(points)]})


class StreetNameTests(unittest.TestCase):
    def test_typed_street_names_match_openstreetmap_names(self):
        cases = [
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
        ]
        for typed, osm_name, expected in cases:
            with self.subTest(typed=typed, osm_name=osm_name):
                pattern = street_pattern(normalize_street(typed))
                self.assertEqual(bool(re.fullmatch(pattern, osm_name, re.IGNORECASE)), expected)

    def test_only_two_street_names_count_as_an_intersection(self):
        self.assertEqual(parse_intersection("Central Park West and West 86th Street, New York, NY"),
                         ["central park west", "west 86th street"])
        self.assertEqual(parse_intersection("corner of Broadway & 72nd"), ["broadway", "72nd street"])
        self.assertIsNone(parse_intersection("Barnes & Noble"))
        self.assertIsNone(parse_intersection("American Museum of Natural History"))


class GeocodePlaceTests(unittest.TestCase):
    def setUp(self):
        geocoding._shared_nodes.cache_clear()
        geocoding._last_nominatim_call = float("-inf")

    def test_intersection_resolves_to_the_shared_street_node(self):
        with FakeHTTP({OVERPASS: [nodes((40.78530, -73.96934), (40.78532, -73.96938))]}) as http:
            result = geocode_place("Central Park West and West 86th Street")

        self.assertTrue(result["ok"], result)
        place = result["data"]
        self.assertEqual(place["match_type"], "intersection")
        self.assertEqual(place["source"], "geocoded")
        self.assertEqual(place["label"], "Central Park West & West 86th Street")
        self.assertAlmostEqual(place["lat"], 40.78531, places=5)
        self.assertEqual(place["provider_ref"], "osm:node/1")
        self.assertTrue(place["observed_at"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(http.calls), 1)

    def test_streets_meeting_in_two_places_are_flagged_for_confirmation(self):
        # Two meeting points about 2 km apart: the tool must not silently pick one.
        with FakeHTTP({OVERPASS: [nodes((40.7787, -73.9820), (40.7960, -73.9710))]}):
            result = geocode_place("Broadway and 72nd Street")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["alternatives"]), 1)
        self.assertIn("separate places", result["warnings"][0])

    def test_rate_limited_overpass_falls_back_to_the_mirror(self):
        with FakeHTTP({OVERPASS: [(429, None)], OVERPASS_MIRROR: [nodes((40.78326, -73.97455))]}) as http:
            result = geocode_place("Columbus Ave & W 81st St")

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["match_type"], "intersection")
        self.assertFalse(any(NOMINATIM in url for url in http.urls()))

    def test_every_provider_down_is_a_retryable_upstream_error(self):
        answers = {OVERPASS: [(504, None)], OVERPASS_MIRROR: [(504, None)],
                   NOMINATIM: [requests.ConnectionError("offline")]}
        with FakeHTTP(answers):
            result = geocode_place("Columbus Avenue and West 81st Street")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "UPSTREAM_UNAVAILABLE")
        self.assertTrue(result["error"]["retryable"])

    def test_unknown_place_is_no_match_with_a_next_step(self):
        with FakeHTTP({NOMINATIM: [(200, [])]}):
            result = geocode_place("The Glass Pagoda of Nowhere")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "NO_MATCH")
        self.assertIn("cross street", result["error"]["next_step"])

    def test_address_uses_city_geosearch_and_flags_a_weak_match(self):
        feature = {"geometry": {"coordinates": [-73.975905, 40.776785]},
                   "properties": {"label": "1 WEST 72 STREET, New York, NY, USA", "confidence": 0.6,
                                  "addendum": {"pad": {"bbl": "1011250001"}}}}
        with FakeHTTP({GEOSEARCH: [(200, {"features": [feature]})]}):
            result = geocode_place("1 W 72nd St")

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["match_type"], "address")
        self.assertEqual(result["data"]["provider_ref"], "bbl:1011250001")
        self.assertIn("confirm", result["warnings"][0])

    def test_match_outside_the_city_is_outside_coverage(self):
        feature = {"geometry": {"coordinates": [-75.1652, 39.9526]}, "properties": {"label": "1 Market St", "confidence": 1}}
        with FakeHTTP({GEOSEARCH: [(200, {"features": [feature]})]}):
            result = geocode_place("1 Market Street")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "OUTSIDE_COVERAGE")


if __name__ == "__main__":
    unittest.main()
