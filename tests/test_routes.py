import unittest

from integrations.routes import get_route, get_walking_times
from fake_http import FakeHTTP

VALHALLA = "valhalla1.openstreetmap.de"
OSRM = "routing.openstreetmap.de"

START = {"id": "start", "lat": 40.7853, "lon": -73.9693}
EL_DORADO = {"id": "wiki:9238071", "lat": 40.7883, "lon": -73.9675}
BERESFORD = {"id": "wiki:5667636", "lat": 40.7825, "lon": -73.9719}

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


class GetRouteTests(unittest.TestCase):
    def test_multi_stop_walk_becomes_one_leg_per_consecutive_pair(self):
        with FakeHTTP({VALHALLA: [(200, VALHALLA_TWO_LEGS)]}):
            result = get_route([START, EL_DORADO, BERESFORD])

        self.assertTrue(result["ok"], result)
        first, second = result["data"]["legs"]
        self.assertEqual((first["from_id"], first["to_id"]), ("start", "wiki:9238071"))
        self.assertEqual((second["from_id"], second["to_id"]), ("wiki:9238071", "wiki:5667636"))
        self.assertEqual((first["duration_min"], first["distance_m"]), (4.5, 380))
        self.assertEqual(first["instructions"], ["Walk northeast on Central Park West.", "Turn left onto the walkway."])
        self.assertEqual(second["via_streets"], ["West 81st Street"])
        self.assertEqual(first["mode"], "walk")
        self.assertTrue(first["retrieved_at"] and first["uncertainty"])
        self.assertEqual((result["data"]["total_duration_min"], result["data"]["total_distance_m"]), (14.0, 1180))

    def test_transit_only_request_is_rejected_without_calling_a_router(self):
        with FakeHTTP({}) as http:
            result = get_route([START, EL_DORADO], modes=["transit"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertIn("foot", result["error"]["next_step"])
        self.assertEqual(http.calls, [])

    def test_walk_plus_transit_routes_on_foot_and_says_so(self):
        with FakeHTTP({VALHALLA: [(200, VALHALLA_TWO_LEGS)]}):
            result = get_route([START, EL_DORADO, BERESFORD], modes=["walk", "transit"])

        self.assertTrue(result["ok"])
        self.assertIn("not configured", result["warnings"][0])

    def test_point_outside_the_city_is_rejected_before_any_request(self):
        with FakeHTTP({}) as http:
            result = get_route([START, {"id": "london", "lat": 51.5074, "lon": -0.1278}])

        self.assertEqual(result["error"]["code"], "OUTSIDE_COVERAGE")
        self.assertEqual(http.calls, [])

    def test_valhalla_outage_falls_back_to_osrm(self):
        with FakeHTTP({VALHALLA: [(503, None)], OSRM: [(200, OSRM_ONE_LEG)]}):
            result = get_route([START, EL_DORADO])

        self.assertTrue(result["ok"], result)
        leg = result["data"]["legs"][0]
        self.assertEqual(leg["provider"], "osrm-fossgis-foot")
        self.assertEqual(leg["instructions"], ["Walk southwest on Columbus Avenue.", "Turn right onto the walkway.",
                                               "Turn left onto Amsterdam Avenue."])
        self.assertIn("Valhalla", result["warnings"][0])

    def test_disconnected_points_are_no_match_not_an_outage(self):
        no_path = {"error_code": 442, "error": "No path could be found for input", "status_code": 400}
        with FakeHTTP({VALHALLA: [(400, no_path)]}) as http:
            result = get_route([START, EL_DORADO])

        self.assertEqual(result["error"]["code"], "NO_MATCH")
        self.assertFalse(any(OSRM in url and VALHALLA not in url for url in http.urls()))

    def test_both_routers_down_is_a_retryable_upstream_error(self):
        with FakeHTTP({VALHALLA: [(502, None)], OSRM: [(502, None)]}):
            result = get_route([START, EL_DORADO])

        self.assertEqual(result["error"]["code"], "UPSTREAM_UNAVAILABLE")
        self.assertTrue(result["error"]["retryable"])


class WalkingTimesTests(unittest.TestCase):
    def test_unreachable_destination_gets_no_guessed_time(self):
        matrix = {"sources_to_targets": [[
            {"to_index": 1, "time": None, "distance": None},
            {"to_index": 0, "time": 269, "distance": 0.38},
        ]]}
        with FakeHTTP({VALHALLA: [(200, matrix)]}):
            result = get_walking_times(START, [EL_DORADO, BERESFORD])

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["times"], [
            {"to_id": "wiki:9238071", "duration_min": 4.5, "distance_m": 380},
            {"to_id": "wiki:5667636", "duration_min": None, "distance_m": None},
        ])
        self.assertIn("unreachable", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
