import unittest

from integrations.research import find_places, research_place
from tests.fake_http import FakeHTTP

WIKIPEDIA = "en.wikipedia.org"
LPC_SITES = "buis-pvji"
LPC_BUILDINGS = "gpmc-yuvp"
START = (40.7853, -73.9693)  # Central Park West & West 86th Street


def article(pageid, title, lat, lon, description="Apartment building in Manhattan, New York"):
    return {"pageid": pageid, "title": title, "description": description, "coordinates": [{"lat": lat, "lon": lon}],
            "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}


def wikipedia_answers(*pages):
    """The two Wikipedia calls find_places makes: the geosearch, then page details."""
    found = {"query": {"geosearch": [{"pageid": p["pageid"], "title": p["title"]} for p in pages]}}
    return [(200, found), (200, {"query": {"pages": list(pages)}})]


def landmark(name, lp, lat, lon, address="300 Central Park West"):
    return {"lpc_name": name, "lpc_lpnumb": lp, "address": address, "desdate": "7/9/1985",
            "landmarkty": "Individual Landmark", "url_report": f"http://s-media.nyc.gov/agencies/lpc/lp/{lp[3:]}.pdf",
            "latitude": str(lat), "longitude": str(lon)}


EL_DORADO = article(9238071, "The El Dorado", 40.78833, -73.9675, "Residential skyscraper in Manhattan, New York")


class FindPlacesTests(unittest.TestCase):
    def test_landmark_record_folds_into_the_article_about_the_same_building(self):
        upper_west_side = article(327244, "Upper West Side", 40.787, -73.9754, "Neighborhood in Manhattan, New York City")
        answers = {WIKIPEDIA: wikipedia_answers(EL_DORADO, upper_west_side),
                   LPC_SITES: [(200, [landmark("Eldorado Apartments", "LP-01521", 40.788359, -73.9677083)])]}
        with FakeHTTP(answers):
            result = find_places(*START, radius_m=700)

        self.assertTrue(result["ok"], result)
        [candidate] = result["data"]["candidates"]  # the neighborhood article is not a place to walk to
        self.assertEqual(candidate["place_id"], "wiki:9238071")
        self.assertEqual(candidate["related_ids"], ["lpc:LP-01521"])
        self.assertEqual(candidate["designation"]["designated_on"], "1985-07-09")
        self.assertEqual(candidate["address"], "300 Central Park West")
        self.assertIn("http://s-media.nyc.gov/agencies/lpc/lp/01521.pdf", [s["url"] for s in candidate["sources"]])

    def test_landmarks_recorded_at_a_shared_lot_center_are_skipped_with_a_warning(self):
        lot_center = (40.7824929, -73.9655482)  # Central Park's tax lot holds both landmarks
        rows = [landmark("Metropolitan Museum of Art", "LP-00410", *lot_center),
                landmark("The Arsenal", "LP-00312", *lot_center),
                landmark("Beresford Apartments", "LP-01520", 40.782577, -73.9719251)]
        answers = {WIKIPEDIA: [(200, {"query": {"geosearch": []}})], LPC_SITES: [(200, rows)]}
        with FakeHTTP(answers):
            result = find_places(*START, radius_m=800)

        self.assertEqual([c["place_id"] for c in result["data"]["candidates"]], ["lpc:LP-01520"])
        self.assertIn("Metropolitan Museum of Art", result["warnings"][0])
        self.assertIn("The Arsenal", result["warnings"][0])

    def test_one_source_down_still_returns_the_other_with_a_warning(self):
        answers = {WIKIPEDIA: wikipedia_answers(EL_DORADO), LPC_SITES: [(500, None)]}
        with FakeHTTP(answers):
            result = find_places(*START, radius_m=700)

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["candidates"][0]["place_id"], "wiki:9238071")
        self.assertIn("Partial results", result["warnings"][0])

    def test_both_sources_down_is_an_upstream_error_not_an_empty_list(self):
        with FakeHTTP({WIKIPEDIA: [(503, None)], LPC_SITES: [(503, None)]}):
            result = find_places(*START)

        self.assertEqual(result["error"]["code"], "UPSTREAM_UNAVAILABLE")

    def test_designated_landmarks_are_not_crowded_out_by_nearer_articles(self):
        nearby = [article(1000 + i, f"Row House {i}", 40.7853 + i * 0.00005, -73.9693) for i in range(20)]
        rows = [landmark("Belnord Apartments", "LP-00289", 40.7880, -73.9760, "225 West 86th Street"),
                landmark("Claremont Stables", "LP-01658", 40.7896, -73.9729, "175 West 89th Street")]
        answers = {WIKIPEDIA: wikipedia_answers(*nearby), LPC_SITES: [(200, rows)]}
        with FakeHTTP(answers):
            result = find_places(*START, radius_m=800, limit=6)

        ids = [c["place_id"] for c in result["data"]["candidates"]]
        self.assertEqual(len(ids), 6)
        self.assertIn("lpc:LP-00289", ids)
        self.assertIn("lpc:LP-01658", ids)

    def test_point_outside_the_city_is_rejected_before_any_request(self):
        with FakeHTTP({}) as http:
            result = find_places(51.5074, -0.1278)

        self.assertEqual(result["error"]["code"], "OUTSIDE_COVERAGE")
        self.assertEqual(http.calls, [])


EXTRACT = (
    "The El Dorado is a cooperative apartment building at 300 Central Park West. "
    "It was designed by Margon & Holder with consulting architect Emery Roth.\n\n"
    "== History ==\n"
    "In March 1929, developer Frederick Brown acquired the original El Dorado Apartments.\n\n"
    "=== Construction ===\n"
    "Work on the towers began under Mayor James J. Walker in the spring of 1929.\n\n"
    "== References ==\n"
    "Reference list text that must never become a claim about the building."
)
EL_DORADO_PAGE = {**EL_DORADO, "extract": EXTRACT, "revisions": [{"revid": 123, "timestamp": "2026-09-06T01:37:31Z"}]}
ELDORADO_BUILDING = {"des_addres": "300 Central Park West", "build_nme": "The Eldorado",
                     "arch_build": "Margon & Holder and Emery Roth", "style_prim": "Art Deco",
                     "date_combo": "1929 - 1931", "hist_dist": "Upper West Side / Central Park West", "bbl": "1012040029"}


class ResearchPlaceTests(unittest.TestCase):
    def test_claims_cite_the_exact_revision_and_section_they_quote(self):
        answers = {WIKIPEDIA: [(200, {"query": {"pages": [EL_DORADO_PAGE]}})],
                   LPC_BUILDINGS: [(200, [ELDORADO_BUILDING])]}
        with FakeHTTP(answers):
            result = research_place("wiki:9238071", focus="history")

        self.assertTrue(result["ok"], result)
        evidence = result["data"]
        claims = evidence["claims"]
        self.assertEqual(claims[0]["source_url"], "https://en.wikipedia.org/w/index.php?oldid=123")
        history = [c for c in claims if c["source_url"].endswith("#History")]
        self.assertEqual([c["text"][:8] for c in history], ["In March", "Work on "])  # includes the subsection
        self.assertIn("James J. Walker", history[1]["text"])  # an initial does not end the sentence
        self.assertFalse(any("Reference list" in c["text"] for c in claims))
        self.assertEqual(claims[-1]["source_kind"], "official_record")
        self.assertIn("Art Deco", claims[-1]["text"])
        self.assertEqual(evidence["address"], "300 Central Park West")
        self.assertEqual(evidence["physical_features"], [])
        self.assertEqual(evidence["access"]["status"], "unknown")

    def test_a_neighboring_buildings_record_is_not_attached(self):
        neighbor = {**ELDORADO_BUILDING, "build_nme": "The Kenilworth", "des_addres": "151 Central Park West",
                    "arch_build": "Townsend, Steinle & Haskell"}
        answers = {WIKIPEDIA: [(200, {"query": {"pages": [EL_DORADO_PAGE]}})], LPC_BUILDINGS: [(200, [neighbor])]}
        with FakeHTTP(answers):
            result = research_place("wiki:9238071")

        self.assertFalse(any(c["source_kind"] == "official_record" for c in result["data"]["claims"]))

    def test_missing_section_falls_back_to_the_introduction_with_a_warning(self):
        answers = {WIKIPEDIA: [(200, {"query": {"pages": [EL_DORADO_PAGE]}})], LPC_BUILDINGS: [(200, [])]}
        with FakeHTTP(answers):
            result = research_place("wiki:9238071", focus="jazz")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["claims"]), 2)
        self.assertIn("jazz", result["warnings"][0])

    def test_landmark_on_a_shared_lot_reports_no_coordinates(self):
        site = {**landmark("The Arsenal", "LP-00312", 40.7824929, -73.9655482, "Central Park at East 64th Street"),
                "bbl": "1011110001"}
        answers = {LPC_SITES: [(200, [site]), (200, [{"lpc_lpnumb": "LP-00312"}, {"lpc_lpnumb": "LP-00410"}])],
                   LPC_BUILDINGS: [(200, [])]}
        with FakeHTTP(answers):
            result = research_place("lpc:LP-00312")

        evidence = result["data"]
        self.assertIsNone(evidence["lat"])
        self.assertIn("tax lot", evidence["uncertainty"][0])
        self.assertIn("1985-07-09", evidence["claims"][0]["text"])

    def test_missing_article_is_no_match(self):
        with FakeHTTP({WIKIPEDIA: [(200, {"query": {"pages": [{"pageid": 42, "missing": True}]}})]}):
            result = research_place("wiki:42")

        self.assertEqual(result["error"]["code"], "NO_MATCH")

    def test_unknown_place_id_is_rejected_before_any_request(self):
        with FakeHTTP({}) as http:
            result = research_place("google:ChIJ8anpOoNZwokR")

        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(http.calls, [])


if __name__ == "__main__":
    unittest.main()
