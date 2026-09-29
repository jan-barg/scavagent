"""Walking routes and walking-time matrices between NYC coordinates.

Returns RouteLeg-shaped records (docs/CONTRACTS.md). Only walking is routed: transit needs
Google Routes, which is not enabled on the class GCP project, or another keyed provider.
Legs carry durations only. Scheduling with dwell time and contingency belongs to the plan evaluator.

Providers are keyless, low-volume public servers run by FOSSGIS on OpenStreetMap data:
- Valhalla (valhalla1.openstreetmap.de): primary, for multi-stop routes and matrices.
- OSRM with the foot profile (routing.openstreetmap.de): fallback.
Manhattan sidewalks are mapped as separate unnamed ways, so many instructions say "the walkway".
Each leg also lists the named streets it uses, so the agent can describe it in street terms.
"""

import math

from integrations.common import UpstreamError, failure, fetch_json, in_nyc, now_iso, success

VALHALLA_URL = "https://valhalla1.openstreetmap.de"
OSRM_URL = "https://routing.openstreetmap.de/routed-foot"
MAX_STOPS = 10
MAX_DESTINATIONS = 25
MAX_INSTRUCTIONS = 12
SAME_SPOT_M = 25  # consecutive stops closer than this need no directions
VALHALLA_ARRIVAL_TYPES = {4, 5, 6}  # "You have arrived" maneuvers add nothing to directions
ESTIMATE_NOTE = ("Walking estimate from OpenStreetMap routing; it excludes waiting at crossings and "
                 "finding entrances, so add contingency before promising an arrival time.")
WALK_ONLY_STEP = ("Plan the leg on foot if the user allows walking; otherwise explain that transit "
                  "directions are not available yet.")
COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
OSRM_VERBS = {"turn": "Turn", "end of road": "Turn", "fork": "Keep", "new name": "Continue", "continue": "Continue"}


class NoRoute(Exception):
    """The router answered, but no walking path connects the points."""


def get_route(stops, modes=("walk",)):
    """Walking route through `stops` in visiting order, as one RouteLeg per consecutive pair.

    stops: 2-10 points like {"id": "start", "lat": 40.7853, "lon": -73.9693}.
    modes: travel modes the user allows. Only "walk" can be routed today.
    """
    problem = _check_points(stops, "stops", 2, MAX_STOPS)
    if problem:
        return problem
    modes = [modes] if isinstance(modes, str) else list(modes or ["walk"])  # models sometimes send one string
    if "walk" not in modes:
        return failure("INVALID_ARGUMENT", f"Only walking can be routed; the allowed modes were {modes}.", WALK_ONLY_STEP)

    warnings = []
    if set(modes) - {"walk"}:
        warnings.append("Transit and car routing are not configured, so every leg is on foot.")
    try:
        try:
            raw_legs, provider = _valhalla_route(stops), "valhalla-fossgis"
        except UpstreamError as first:
            raw_legs, provider = _osrm_route(stops), "osrm-fossgis-foot"
            warnings.append(f"Valhalla was unavailable ({first}); used OSRM, whose instructions are sparser.")
    except NoRoute as e:
        return failure("NO_MATCH", f"No walking route connects these stops ({e}).",
                       "Replace the unreachable stop, or check that each point is on a public street.")
    except UpstreamError as e:
        return failure("UPSTREAM_UNAVAILABLE", f"Both walking routers failed; last error: {e}.",
                       "Try again shortly. Do not state travel times until a route succeeds.", retryable=True)

    retrieved_at = now_iso()
    legs = []
    for (origin, destination), (seconds, meters, instructions, streets) in zip(zip(stops, stops[1:]), raw_legs):
        if meters < SAME_SPOT_M:
            instructions, streets = [], []  # the router snaps both points to one street; its name misleads
        legs.append({
            "from_id": origin.get("id"),
            "to_id": destination.get("id"),
            "permitted_modes": modes,
            "mode": "walk",
            "duration_min": round(seconds / 60, 1),
            "distance_m": round(meters),
            "heading": _heading(origin, destination),
            "via_streets": streets,
            "instructions": instructions[:MAX_INSTRUCTIONS],
            "provider": provider,
            "retrieved_at": retrieved_at,
            "uncertainty": ESTIMATE_NOTE,
        })
    return success({
        "mode": "walk",
        "legs": legs,
        "total_duration_min": round(sum(leg["duration_min"] for leg in legs), 1),
        "total_distance_m": sum(leg["distance_m"] for leg in legs),
    }, warnings)


def get_walking_times(origin, destinations):
    """Walking minutes from one point to each of 1-25 destinations, for ranking and filtering candidates.

    origin and destinations are points like {"id": "wiki:9238071", "lat": ..., "lon": ...}.
    An unreachable destination gets duration_min None rather than a guessed value.
    """
    problem = _check_points([origin], "origin", 1, 1) or _check_points(destinations, "destinations", 1, MAX_DESTINATIONS)
    if problem:
        return problem

    warnings = []
    try:
        try:
            pairs, provider = _valhalla_matrix(origin, destinations), "valhalla-fossgis"
        except UpstreamError as first:
            pairs, provider = _osrm_table(origin, destinations), "osrm-fossgis-foot"
            warnings.append(f"Valhalla was unavailable ({first}); used OSRM.")
    except UpstreamError as e:
        return failure("UPSTREAM_UNAVAILABLE", f"Both walking routers failed; last error: {e}.",
                       "Try again shortly, or rank candidates by straight-line distance and say so.", retryable=True)

    times = [{
        "to_id": destination.get("id"),
        "duration_min": None if seconds is None else round(seconds / 60, 1),
        "distance_m": None if meters is None else round(meters),
    } for destination, (seconds, meters) in zip(destinations, pairs)]
    if any(t["duration_min"] is None for t in times):
        warnings.append("Some destinations are unreachable on foot; their duration_min is null.")
    return success({
        "from_id": origin.get("id"),
        "mode": "walk",
        "times": times,
        "provider": provider,
        "retrieved_at": now_iso(),
        "uncertainty": ESTIMATE_NOTE,
    }, warnings)


def _check_points(points, name, minimum, maximum):
    """A failure envelope for malformed or out-of-city points, or None when they are usable."""
    if not isinstance(points, list) or not minimum <= len(points) <= maximum:
        return failure("INVALID_ARGUMENT", f"{name} must be a list of {minimum}-{maximum} points.",
                       "Pass points like {'id': 'start', 'lat': 40.7853, 'lon': -73.9693}.")
    for i, point in enumerate(points):
        try:
            lat, lon = float(point["lat"]), float(point["lon"])
        except (KeyError, TypeError, ValueError):
            return failure("INVALID_ARGUMENT", f"{name}[{i}] needs numeric 'lat' and 'lon'.",
                           "Use coordinates returned by geocode_place or find_places.")
        if not in_nyc(lat, lon):
            return failure("OUTSIDE_COVERAGE", f"{name}[{i}] ({lat}, {lon}) is outside New York City.",
                           "Use points within the five boroughs.")
    return None


def _valhalla_route(stops):
    payload = {
        "locations": [{"lat": float(s["lat"]), "lon": float(s["lon"]), "type": "break"} for s in stops],
        "costing": "pedestrian",
        "units": "kilometers",
    }
    try:
        body = fetch_json("valhalla", "POST", f"{VALHALLA_URL}/route", json=payload)
    except UpstreamError as e:
        if e.status == 400 and isinstance(e.body, dict) and e.body.get("error_code"):
            raise NoRoute(e.body.get("error", "no path")) from e
        raise

    try:
        raw_legs = []
        for leg in body["trip"]["legs"]:
            maneuvers = leg.get("maneuvers", [])
            instructions = [m["instruction"] for m in maneuvers if m.get("type") not in VALHALLA_ARRIVAL_TYPES]
            streets = _unique(name for m in maneuvers for name in m.get("street_names", []))
            raw_legs.append((leg["summary"]["time"], leg["summary"]["length"] * 1000, instructions, streets))
    except (KeyError, TypeError) as e:
        raise UpstreamError("valhalla", f"unexpected response shape ({e!r})") from e
    if len(raw_legs) != len(stops) - 1:
        raise UpstreamError("valhalla", f"expected {len(stops) - 1} legs, got {len(raw_legs)}")
    return raw_legs


def _osrm_route(stops):
    coordinates = ";".join(f"{float(s['lon'])},{float(s['lat'])}" for s in stops)
    try:
        body = fetch_json("osrm", "GET", f"{OSRM_URL}/route/v1/foot/{coordinates}",
                          params={"steps": "true", "overview": "false"})
    except UpstreamError as e:
        if e.status == 400 and isinstance(e.body, dict) and e.body.get("code") in ("NoRoute", "NoSegment"):
            raise NoRoute(e.body.get("message") or e.body["code"]) from e
        raise
    if body.get("code") != "Ok" or not body.get("routes"):
        raise NoRoute(body.get("message") or body.get("code") or "no route")

    try:
        raw_legs = []
        for leg in body["routes"][0]["legs"]:
            steps = leg.get("steps", [])
            instructions = [_osrm_instruction(s) for s in steps if s["maneuver"]["type"] != "arrive"]
            streets = _unique(s["name"] for s in steps if s.get("name"))
            raw_legs.append((leg["duration"], leg["distance"], instructions, streets))
    except (KeyError, TypeError, IndexError) as e:
        raise UpstreamError("osrm", f"unexpected response shape ({e!r})") from e
    if len(raw_legs) != len(stops) - 1:
        raise UpstreamError("osrm", f"expected {len(stops) - 1} legs, got {len(raw_legs)}")
    return raw_legs


def _osrm_instruction(step):
    maneuver = step["maneuver"]
    road = step.get("name") or "the walkway"
    if maneuver["type"] == "depart":
        heading = COMPASS[round(maneuver.get("bearing_after", 0) / 45) % 8]
        return f"Walk {heading} on {road}."
    modifier = maneuver.get("modifier")
    turn = f" {modifier}" if modifier and modifier != "straight" else ""
    return f"{OSRM_VERBS.get(maneuver['type'], 'Continue')}{turn} onto {road}."


def _valhalla_matrix(origin, destinations):
    payload = {
        "sources": [{"lat": float(origin["lat"]), "lon": float(origin["lon"])}],
        "targets": [{"lat": float(d["lat"]), "lon": float(d["lon"])} for d in destinations],
        "costing": "pedestrian",
        "units": "kilometers",
    }
    body = fetch_json("valhalla", "POST", f"{VALHALLA_URL}/sources_to_targets", json=payload)
    try:
        cells = sorted(body["sources_to_targets"][0], key=lambda cell: cell["to_index"])
        pairs = [(c.get("time"), None if c.get("distance") is None else c["distance"] * 1000) for c in cells]
    except (KeyError, TypeError, IndexError) as e:
        raise UpstreamError("valhalla", f"unexpected matrix shape ({e!r})") from e
    if len(pairs) != len(destinations):
        raise UpstreamError("valhalla", f"expected {len(destinations)} matrix cells, got {len(pairs)}")
    return pairs


def _osrm_table(origin, destinations):
    coordinates = ";".join(f"{float(p['lon'])},{float(p['lat'])}" for p in [origin, *destinations])
    body = fetch_json("osrm", "GET", f"{OSRM_URL}/table/v1/foot/{coordinates}",
                      params={"sources": "0", "annotations": "duration,distance"})
    if body.get("code") != "Ok":
        raise UpstreamError("osrm", body.get("message") or body.get("code") or "table failed")
    try:
        pairs = list(zip(body["durations"][0][1:], body["distances"][0][1:]))
    except (KeyError, TypeError, IndexError) as e:
        raise UpstreamError("osrm", f"unexpected table shape ({e!r})") from e
    if len(pairs) != len(destinations):
        raise UpstreamError("osrm", f"expected {len(destinations)} table cells, got {len(pairs)}")
    return pairs


def _heading(origin, destination):
    """True-compass direction of the straight line between two points. Manhattan's street grid is
    rotated about 29 degrees east of true north, so "uptown" reads as north-northeast here."""
    lat1, lat2 = math.radians(float(origin["lat"])), math.radians(float(destination["lat"]))
    dlon = math.radians(float(destination["lon"]) - float(origin["lon"]))
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(x, y)) + 360) % 360
    return COMPASS[round(bearing / 45) % 8]


def _unique(names):
    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen
