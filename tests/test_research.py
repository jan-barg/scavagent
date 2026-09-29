from fake_http import FakeHTTP
from integrations.research import LPC_BUILDINGS_PAGE, find_places, research_place
from schemas import PlaceEvidence, ToolResult

WIKIPEDIA = "en.wikipedia.org"
LPC_SITES = "buis-pvji"
LPC_BUILDINGS = "gpmc-yuvp"
START = (40.7853, -73.9693)  # Central Park West & West 86th Street


def article(pageid, title, lat, lng, description="Apartment building in Manhattan, New York"):
    return {"pageid": pageid, "title": title, "description": description, "coordinates": [{"lat": lat, "lon": lng}],
            "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}


def wikipedia_answers(*pages):
    """The two Wikipedia calls find_places makes: the geosearch, then page details."""
    found = {"query": {"geosearch": [{"pageid": p["pageid"], "title": p["title"]} for p in pages]}}
    return [(200, found), (200, {"query": {"pages": list(pages)}})]


def landmark(name, lp, lat, lng, address="300 Central Park West"):
    return {"lpc_name": name, "lpc_lpnumb": lp, "address": address, "desdate": "7/9/1985",
            "landmarkty": "Individual Landmark", "url_report": f"http://s-media.nyc.gov/agencies/lpc/lp/{lp[3:]}.pdf",
            "latitude": str(lat), "longitude": str(lng)}


EL_DORADO = article(9238071, "The El Dorado", 40.78833, -73.9675, "Residential skyscraper in Manhattan, New York")


# --- find_places ---


def test_landmark_record_folds_into_the_article_about_the_same_building():
    upper_west_side = article(327244, "Upper West Side", 40.787, -73.9754, "Neighborhood in Manhattan, New York City")
    answers = {WIKIPEDIA: wikipedia_answers(EL_DORADO, upper_west_side),
               LPC_SITES: [(200, [landmark("Eldorado Apartments", "LP-01521", 40.788359, -73.9677083)])]}
    with FakeHTTP(answers):
        result = find_places(*START, radius_m=700)

    ToolResult.model_validate(result)
    [candidate] = result["data"]["candidates"]  # the neighborhood article is not a place to walk to
    assert candidate["place_id"] == "wiki:9238071"
    assert candidate["point"] == {"lat": 40.78833, "lng": -73.9675}
    assert candidate["related_ids"] == ["lpc:LP-01521"]
    assert candidate["designation"]["designated_on"] == "1985-07-09"
    assert candidate["address"] == "300 Central Park West"
    assert "http://s-media.nyc.gov/agencies/lpc/lp/01521.pdf" in [s["url"] for s in candidate["sources"]]


def test_landmarks_recorded_at_a_shared_lot_center_are_skipped_with_a_warning():
    lot_center = (40.7824929, -73.9655482)  # Central Park's tax lot holds both landmarks
    rows = [landmark("Metropolitan Museum of Art", "LP-00410", *lot_center),
            landmark("The Arsenal", "LP-00312", *lot_center),
            landmark("Beresford Apartments", "LP-01520", 40.782577, -73.9719251)]
    answers = {WIKIPEDIA: [(200, {"query": {"geosearch": []}})], LPC_SITES: [(200, rows)]}
    with FakeHTTP(answers):
        result = find_places(*START, radius_m=800)

    assert [c["place_id"] for c in result["data"]["candidates"]] == ["lpc:LP-01520"]
    assert "Metropolitan Museum of Art" in result["warnings"][0] and "The Arsenal" in result["warnings"][0]


def test_one_source_down_still_returns_the_other_with_a_warning():
    with FakeHTTP({WIKIPEDIA: wikipedia_answers(EL_DORADO), LPC_SITES: [(500, None)]}):
        result = find_places(*START, radius_m=700)

    assert result["ok"]
    assert result["data"]["candidates"][0]["place_id"] == "wiki:9238071"
    assert "Partial results" in result["warnings"][0]


def test_both_sources_down_is_an_upstream_error_not_an_empty_list():
    with FakeHTTP({WIKIPEDIA: [(503, None)], LPC_SITES: [(503, None)]}):
        result = find_places(*START)

    assert result["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_designated_landmarks_are_not_crowded_out_by_nearer_articles():
    nearby = [article(1000 + i, f"Row House {i}", 40.7853 + i * 0.00005, -73.9693) for i in range(20)]
    rows = [landmark("Belnord Apartments", "LP-00289", 40.7880, -73.9760, "225 West 86th Street"),
            landmark("Claremont Stables", "LP-01658", 40.7896, -73.9729, "175 West 89th Street")]
    with FakeHTTP({WIKIPEDIA: wikipedia_answers(*nearby), LPC_SITES: [(200, rows)]}):
        result = find_places(*START, radius_m=800, limit=6)

    ids = [c["place_id"] for c in result["data"]["candidates"]]
    assert len(ids) == 6
    assert "lpc:LP-00289" in ids and "lpc:LP-01658" in ids


def test_point_outside_the_city_is_rejected_before_any_request():
    with FakeHTTP({}) as http:
        result = find_places(51.5074, -0.1278)

    assert result["error"]["code"] == "OUTSIDE_COVERAGE"
    assert http.calls == []


# --- research_place ---

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


def research(answers, place_id="wiki:9238071", focus=None):
    with FakeHTTP(answers):
        return research_place(place_id, focus=focus)


def test_claims_cite_the_exact_revision_and_section_they_quote():
    result = research({WIKIPEDIA: [(200, {"query": {"pages": [EL_DORADO_PAGE]}})],
                       LPC_BUILDINGS: [(200, [ELDORADO_BUILDING])]}, focus="history")

    ToolResult.model_validate(result)
    evidence = PlaceEvidence.model_validate(result["data"]["evidence"])
    claims = evidence.claims
    assert str(claims[0].source_urls[0]) == "https://en.wikipedia.org/w/index.php?oldid=123"
    assert claims[1].kind == "architectural"  # "designed by ... architect"
    history = [c for c in claims if str(c.source_urls[0]).endswith("#History")]
    assert [c.text[:8] for c in history] == ["In March", "Work on "]  # includes the subsection
    assert "James J. Walker" in history[1].text  # an initial does not end the sentence
    assert not any("Reference list" in c.text for c in claims)
    assert all(c.basis == "source" and c.claim_id.startswith("wiki:9238071#") for c in claims)
    building = claims[-1]
    assert str(building.source_urls[0]) == LPC_BUILDINGS_PAGE and "Art Deco" in building.text
    assert evidence.address == "300 Central Park West"
    assert "visible today" in evidence.uncertainty


def test_researching_again_gives_the_same_claim_ids():
    answers = {WIKIPEDIA: [(200, {"query": {"pages": [EL_DORADO_PAGE]}})], LPC_BUILDINGS: [(200, [ELDORADO_BUILDING])]}
    first, second = research(answers), research(answers)

    ids = [c["claim_id"] for c in first["data"]["evidence"]["claims"]]
    assert ids == [c["claim_id"] for c in second["data"]["evidence"]["claims"]]
    assert len(set(ids)) == len(ids)


def test_a_neighboring_buildings_record_is_not_attached():
    neighbor = {**ELDORADO_BUILDING, "build_nme": "The Kenilworth", "des_addres": "151 Central Park West",
                "arch_build": "Townsend, Steinle & Haskell"}
    result = research({WIKIPEDIA: [(200, {"query": {"pages": [EL_DORADO_PAGE]}})], LPC_BUILDINGS: [(200, [neighbor])]})

    assert not any(LPC_BUILDINGS_PAGE in c["source_urls"] for c in result["data"]["evidence"]["claims"])


def test_missing_section_falls_back_to_the_introduction_with_a_warning():
    result = research({WIKIPEDIA: [(200, {"query": {"pages": [EL_DORADO_PAGE]}})], LPC_BUILDINGS: [(200, [])]},
                      focus="jazz")

    assert len(result["data"]["evidence"]["claims"]) == 2
    assert "jazz" in result["warnings"][0]


def test_landmark_on_a_shared_lot_reports_no_coordinates():
    site = {**landmark("The Arsenal", "LP-00312", 40.7824929, -73.9655482, "Central Park at East 64th Street"),
            "bbl": "1011110001"}
    answers = {LPC_SITES: [(200, [site]), (200, [{"lpc_lpnumb": "LP-00312"}, {"lpc_lpnumb": "LP-00410"}])],
               LPC_BUILDINGS: [(200, [])]}
    result = research(answers, place_id="lpc:LP-00312")

    evidence = PlaceEvidence.model_validate(result["data"]["evidence"])
    assert evidence.point is None
    assert "tax lot" in evidence.uncertainty
    assert "1985-07-09" in evidence.claims[0].text


def test_missing_article_is_no_match():
    result = research({WIKIPEDIA: [(200, {"query": {"pages": [{"pageid": 42, "missing": True}]}})]}, place_id="wiki:42")

    assert result["error"]["code"] == "NO_MATCH"


def test_unknown_place_id_is_rejected_before_any_request():
    with FakeHTTP({}) as http:
        result = research_place("google:ChIJ8anpOoNZwokR")

    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert http.calls == []
