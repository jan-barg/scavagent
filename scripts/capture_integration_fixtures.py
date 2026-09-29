"""Capture live adapter outputs for the development scenarios.

Run from the repository root (transit needs the Routes API enabled in the billing project):
    SCAVAGENT_ROUTES_PROJECT=<project> uv run python scripts/capture_integration_fixtures.py

Writes fixtures/integrations/*.json. Each file records real provider responses on the capture date
in the /chat tool-call shape ({name, args, result}). They are labeled development fixtures, not
field-verified routes, places, or activities, and not an adventure plan.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so the script runs without installing the package

from integrations.common import utc_now  # noqa: E402
from integrations.geocoding import geocode_place  # noqa: E402
from integrations.research import find_places, research_place  # noqa: E402
from integrations.routes import get_route, get_walking_times  # noqa: E402

OUT_DIR = ROOT / "fixtures" / "integrations"
LABEL = ("LIVE CAPTURE for development: real provider responses on the capture date, in the /chat "
         "tool-call shape. Not field-verified and not an adventure plan.")
ATTRIBUTION = [
    "Wikipedia text is CC BY-SA 4.0; each claim links to the revision it was quoted from.",
    "Map and route data © OpenStreetMap contributors (ODbL), via Overpass, Nominatim, Valhalla, and OSRM.",
    "Transit directions: Google Routes API.",
    "Landmark records: NYC Landmarks Preservation Commission, via NYC Open Data.",
    "Addresses: NYC Planning Labs GeoSearch.",
]
CONTINGENCY_MIN = 5  # the planning example in docs/PLAN.md reserves 5 minutes


class Recorder:
    """Runs adapter calls and keeps them as a /chat-style tool trace."""

    def __init__(self):
        self.tool_calls = []

    def call(self, fn, **args):
        result = fn(**args)
        self.tool_calls.append({"name": fn.__name__, "args": args, "result": result})
        outcome = "ok" if result["ok"] else f"{result['error']['code']}: {result['error']['message']}"
        print(f"  {fn.__name__}: {outcome}")
        return result["data"] if result["ok"] else None


def located(geocoded, point_id):
    point = geocoded["location"]["point"]
    return {"id": point_id, "lat": point["lat"], "lng": point["lng"]}


def candidate_point(candidate):
    return {"id": candidate["place_id"], **candidate["point"]}


def start_only():
    """Fixture 1: only a starting place; discover nearby candidates and route a short chapter."""
    trace, notes = Recorder(), ["Start-only request (grader example 1): Central Park West & West 86th Street."]
    start = trace.call(geocode_place, text="Central Park West and West 86th Street")
    if not start:
        return trace, notes
    origin = located(start, "start")
    places = trace.call(find_places, lat=origin["lat"], lng=origin["lng"], radius_m=700)
    if not places:
        return trace, notes
    candidates = places["candidates"]
    trace.call(get_walking_times, origin=origin, destinations=[candidate_point(c) for c in candidates[:12]])
    # A stand-in for the planner's choice: designated landmarks (an official record as well as an
    # article) that are a real walk from the start rather than at the corner itself.
    away = [c for c in candidates if c["distance_m"] >= 150]
    picks = ([c for c in away if c["designation"]] + [c for c in away if not c["designation"]])[:2]
    for candidate in picks:
        trace.call(research_place, place_id=candidate["place_id"], focus="history")
    route = trace.call(get_route, stops=[origin, *[{**candidate_point(c), "dwell_minutes": 5} for c in picks]])
    if route:
        notes.append(f"Arithmetic on the captured route only (not evaluator output): walking start -> "
                     f"{' -> '.join(c['name'] for c in picks)} takes {route['travel_minutes']} min of travel, "
                     "with 5 minutes at the first stop before the second leg.")
    return trace, notes


def constrained_route():
    """Fixture 2: destination, deadline, required stop, walking only, architecture theme."""
    trace = Recorder()
    notes = ["Grader example 2: 45 minutes, walking only, must pass Columbus Avenue & West 81st Street, "
             "finish at Broadway & West 72nd Street, architecture theme."]
    start = trace.call(geocode_place, text="Central Park West and West 86th Street")
    required = trace.call(geocode_place, text="Columbus Avenue and West 81st Street")
    destination = trace.call(geocode_place, text="Broadway and West 72nd Street")
    if not (start and required and destination):
        return trace, notes
    route = trace.call(get_route, stops=[located(start, "start"), located(required, "required_stop"),
                                         located(destination, "destination")], modes=["walk"])
    stop = located(required, "required_stop")
    places = trace.call(find_places, lat=stop["lat"], lng=stop["lng"], radius_m=500, query="architecture")
    if places:
        trace.call(research_place, place_id=places["candidates"][0]["place_id"], focus="architecture")
    if route:
        spare = 45 - route["travel_minutes"] - CONTINGENCY_MIN
        notes.append(f"Arithmetic on the captured route only (not evaluator output): the baseline walk takes "
                     f"{route['travel_minutes']} min; with {CONTINGENCY_MIN} min contingency, {spare:.1f} of the "
                     "45 minutes remain for detours and activities.")
    return trace, notes


def revision_after_skip():
    """Fixture 3: mid-adventure revision after a skip, with 15 minutes left and a fixed destination."""
    trace = Recorder()
    notes = ["Follow-up (grader example 3): the user is at the required stop, skipped the next optional stop, "
             "has 15 minutes left, and must still reach the destination."]
    here = trace.call(geocode_place, text="Columbus Avenue and West 81st Street")
    destination = trace.call(geocode_place, text="Broadway and West 72nd Street")
    if not (here and destination):
        return trace, notes
    current = located(here, "current_location")
    route = trace.call(get_route, stops=[current, located(destination, "destination")])
    places = trace.call(find_places, lat=current["lat"], lng=current["lng"], radius_m=400)
    if places:
        trace.call(get_walking_times, origin=current, destinations=[candidate_point(c) for c in places["candidates"][:8]])
    if route:
        spare = 15 - route["travel_minutes"] - CONTINGENCY_MIN
        notes.append(f"Arithmetic on the captured route only (not evaluator output): getting to the destination "
                     f"takes {route['travel_minutes']} min; with {CONTINGENCY_MIN} min contingency, {spare:.1f} min "
                     "remain for any detour or activity.")
    return trace, notes


def subway_trip():
    """Fixture 4: a leg long enough that the subway beats walking (Upper West Side to Washington Square)."""
    trace = Recorder()
    notes = ["Transit check: start at Central Park West & West 86th Street, finish at Washington Square Park."]
    start = trace.call(geocode_place, text="Central Park West and West 86th Street")
    destination = trace.call(geocode_place, text="Washington Square Park")
    if not (start and destination):
        return trace, notes
    route = trace.call(get_route, stops=[located(start, "start"), located(destination, "destination")],
                       modes=["walk", "transit"])
    if route:
        detail = route["details"][0]
        notes.append(f"Captured leg mode: {detail['mode']}; door to door {route['travel_minutes']} min against a "
                     f"{detail['walking_minutes']} min walk.")
    return trace, notes


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, scenario in [("start_only", start_only), ("constrained_route", constrained_route),
                           ("revision_after_skip", revision_after_skip), ("subway_trip", subway_trip)]:
        print(f"{name}:")
        trace, notes = scenario()
        document = {
            "fixture": {
                "label": LABEL,
                "scenario": scenario.__doc__.strip(),
                "captured_at": utc_now().isoformat(timespec="seconds"),
                "generator": "scripts/capture_integration_fixtures.py",
                "notes": notes,
                "attribution": ATTRIBUTION,
            },
            "tool_calls": trace.tool_calls,
        }
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        for note in notes[1:]:
            print(f"  note: {note}")
        print(f"  wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
