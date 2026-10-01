"""Routes between NYC points on foot or by subway and bus, as schemas.RouteLeg records.

Walking uses keyless public servers run by FOSSGIS on OpenStreetMap data: Valhalla (primary) and
OSRM with the foot profile (fallback). Manhattan sidewalks are mapped as unnamed ways, so many
walking instructions say "the walkway"; each leg's details also list the named streets it uses
and a compass heading.

Transit comes from Google Routes (integrations/transit.py), one request per leg. When walking is
allowed, a leg rides transit only if that beats walking by MIN_TRANSIT_SAVING_MIN, and a walk of
at most WALK_WITHOUT_TRANSIT_MIN is never looked up. Each leg is timed from when the user is
ready: `depart_at` plus the dwell time at every earlier stop.
"""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from integrations import transit
from integrations.common import NoRoute, UpstreamError, fetch_json, in_nyc, read_point, reference, utc_now
from schemas import NYC_TIMEZONE, Freshness, RouteLeg, tool_error, tool_ok

VALHALLA_URL = "https://valhalla1.openstreetmap.de"
OSRM_URL = "https://routing.openstreetmap.de/routed-foot"
MAX_STOPS = 10
MAX_INSTRUCTIONS = 12
MAX_DWELL_MINUTES = 240
SAME_SPOT_M = 25  # consecutive stops closer than this need no directions
MIN_TRANSIT_SAVING_MIN = 4  # ride only when it beats walking by at least this much
WALK_WITHOUT_TRANSIT_MIN = 12  # a walk this short is never worth a transit lookup
VALHALLA_ARRIVAL_TYPES = {4, 5, 6}  # "You have arrived" maneuvers add nothing to directions
WALK_NOTE = ("Walking estimate from OpenStreetMap routing; it excludes waiting at crossings and finding "
             "entrances, so add contingency before promising an arrival time.")
TRANSIT_NOTE = ("Google transit schedule, with live data where Google has it; the time includes the wait for "
                "the first vehicle. Refresh this leg shortly before the user leaves.")
COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
OSRM_VERBS = {"turn": "Turn", "end of road": "Turn", "fork": "Keep", "new name": "Continue", "continue": "Continue"}


def get_route(stops, modes=("walk", "transit"), depart_at=None, transit_types=("subway", "bus")):
    """Route through `stops` in visiting order: one RouteLeg per consecutive pair, on foot or by transit.

    stops: 2-10 points like {"id": "start", "lat": 40.7853, "lng": -73.9693, "dwell_minutes": 0};
      dwell_minutes is time spent at a stop before leaving it.
    modes: travel modes the user allows, "walk" and/or "transit". Car routing is not available.
    depart_at: ISO time the user is ready at the first stop (default now; no zone means New York time).
    transit_types: "subway" and/or "bus".
    """
    points = _read_points(stops, "stops", 2, MAX_STOPS)
    if isinstance(points, dict):
        return points
    allowed = _read_modes(modes)
    if isinstance(allowed, dict):
        return allowed
    ready = _read_time(depart_at)
    if isinstance(ready, dict):
        return ready
    types = _read_transit_types(transit_types)
    if isinstance(types, dict):
        return types

    warnings = []
    if "car" in allowed:
        warnings.append("Car routing is not available; car was ignored. Suggest the user arrange a ride themselves.")
    use_transit = "transit" in allowed
    walk_allowed = "walk" in allowed

    walking, walk_error = [None] * (len(points) - 1), None
    try:
        walking, walk_source = _walking_legs(points)
    except NoRoute as e:
        walk_error, walk_source = e, None
    except UpstreamError as e:
        walk_error, walk_source = e, None
        if not use_transit:
            return tool_error("UPSTREAM_UNAVAILABLE", f"Walking routers failed: {e}.", retryable=True,
                              next_step="Try again shortly. Do not state travel times until a route succeeds.")
    if isinstance(walk_error, NoRoute) and not use_transit:
        return tool_error("NO_MATCH", f"No walking route connects these stops ({walk_error}).", retryable=False,
                          next_step="Replace the unreachable stop, or check that each point is on a public street.")
    if walk_error:
        warnings.append(f"Walking comparison unavailable ({walk_error}); legs use transit where possible.")

    retrieved_at = utc_now()
    legs, details, any_transit = [], [], False
    for i, (origin, destination) in enumerate(zip(points, points[1:])):
        ready += timedelta(minutes=origin["dwell"])
        leg_id = f"leg_{i + 1}"
        walk = walking[i]
        ride, ride_error = None, None
        should_look_up = use_transit and (not walk_allowed or walk is None or walk["minutes"] > WALK_WITHOUT_TRANSIT_MIN)
        if should_look_up and (walk is None or walk["meters"] >= SAME_SPOT_M):
            try:
                ride = transit.transit_leg((origin["lat"], origin["lng"]), (destination["lat"], destination["lng"]),
                                           ready, types)
            except (NoRoute, UpstreamError) as e:
                ride_error = e

        takes_ride = bool(ride and ride["has_transit"] and (
            walk is None or not walk_allowed or ride["duration_minutes"] <= walk["minutes"] - MIN_TRANSIT_SAVING_MIN))
        if takes_ride:
            any_transit = True
            minutes, meters, instructions = ride["duration_minutes"], ride["distance_m"], ride["instructions"]
            actual, source, note = ["walk", "transit"], "google-routes", TRANSIT_NOTE
            extra = {"transit": {k: ride[k] for k in ("leave_by", "wait_minutes", "segments", "fare")}}
        elif walk is not None:
            if ride_error:
                warnings.append(f"Transit lookup failed for {leg_id} ({ride_error}); it is a {walk['minutes']:.0f}-minute "
                                "walk instead.")
            minutes, meters = walk["minutes"], walk["meters"]
            instructions = _readable(walk, _heading(origin, destination))
            actual, source, note = ["walk"], walk_source, WALK_NOTE
            extra = {"via_streets": walk["streets"]}
        elif ride and not ride["has_transit"]:  # Google's best transit answer is to walk
            minutes, meters, instructions = ride["duration_minutes"], ride["distance_m"], []
            actual, source, note = ["walk"], "google-routes", WALK_NOTE
            extra = {}
        else:
            errors = [e for e in (walk_error, ride_error) if e]
            if errors and all(isinstance(e, NoRoute) for e in errors):
                return tool_error("NO_MATCH", f"No route for {leg_id}: " + "; ".join(map(str, errors)), retryable=False,
                                  next_step="Replace the unreachable stop, or allow another travel mode.")
            return tool_error("UPSTREAM_UNAVAILABLE", f"No route for {leg_id}: " + "; ".join(map(str, errors)),
                              retryable=True, next_step="Try again shortly. Do not state travel times until a route succeeds.")

        arrive = ready + timedelta(minutes=minutes)
        legs.append(RouteLeg(
            leg_id=leg_id, from_id=origin["id"], to_id=destination["id"], allowed_modes=sorted(allowed),
            actual_modes=actual, depart_at=ready, arrive_at=arrive, duration_minutes=minutes, distance_m=meters,
            instructions=instructions[:MAX_INSTRUCTIONS], source=source, retrieved_at=retrieved_at, uncertainty=note,
        ).model_dump(mode="json"))
        details.append({"leg_id": leg_id, "mode": "transit" if takes_ride else "walk",
                        "heading": _heading(origin, destination),
                        "walking_minutes": walk["minutes"] if walk else None, **extra})
        ready = arrive

    freshness = Freshness(kind="scheduled", retrieved_at=retrieved_at) if any_transit else reference()
    return tool_ok({
        "legs": legs,
        "details": details,
        "travel_minutes": round(sum(leg["duration_minutes"] for leg in legs), 1),
        "arrive_at": legs[-1]["arrive_at"],
        "arrive_local": _local(datetime.fromisoformat(legs[-1]["arrive_at"])),
    }, warnings=warnings, freshness=freshness)


# --- Arguments ---


def _read_points(points, name, minimum, maximum):
    """Validated points as dicts with id, lat, lng, and dwell, or a tool_error."""
    if not isinstance(points, list) or not minimum <= len(points) <= maximum:
        return tool_error("INVALID_ARGUMENT", f"{name} must be a list of {minimum}-{maximum} points.", retryable=False,
                          next_step="Pass points like {'id': 'start', 'lat': 40.7853, 'lng': -73.9693}.")
    out = []
    for i, point in enumerate(points):
        position = read_point(point)
        if position is None:
            return tool_error("INVALID_ARGUMENT", f"{name}[{i}] needs numeric 'lat' and 'lng'.", retryable=False,
                              next_step="Use coordinates returned by geocode_place or find_places.")
        if not in_nyc(*position):
            return tool_error("OUTSIDE_COVERAGE", f"{name}[{i}] {position} is outside New York City.", retryable=False,
                              next_step="Use points within the five boroughs.")
        try:
            dwell = float(point.get("dwell_minutes") or 0)
        except (TypeError, ValueError):
            dwell = -1
        if not 0 <= dwell <= MAX_DWELL_MINUTES:
            return tool_error("INVALID_ARGUMENT", f"{name}[{i}].dwell_minutes must be 0-{MAX_DWELL_MINUTES}.",
                              retryable=False, next_step="Give the minutes spent at the stop.")
        out.append({"id": str(point.get("id") or f"point_{i}"), "lat": position[0], "lng": position[1], "dwell": dwell})
    return out


def _read_modes(modes):
    if isinstance(modes, str):
        modes = [modes]
    modes = {str(m).lower() for m in (modes or ["walk", "transit"])}
    unknown = modes - {"walk", "transit", "car"}
    if unknown:
        return tool_error("INVALID_ARGUMENT", f"Unknown travel modes {sorted(unknown)}.", retryable=False,
                          next_step="Use 'walk' and/or 'transit'.")
    if not modes & {"walk", "transit"}:
        return tool_error("INVALID_ARGUMENT", "Car routing is not available.", retryable=False,
                          next_step="Plan on foot or by transit, or let the user arrange a ride and give its time.")
    return modes


def _read_time(value):
    if value in (None, ""):
        return utc_now().replace(microsecond=0)
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return tool_error("INVALID_ARGUMENT", f"depart_at {value!r} is not an ISO time.", retryable=False,
                          next_step="Use a time like '2026-09-29T15:30:00-04:00', or omit it to mean now.")
    return moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo(NYC_TIMEZONE))


def _read_transit_types(value):
    if isinstance(value, str):
        value = [value]
    types = tuple(sorted({str(t).lower() for t in (value or ["subway", "bus"])}))
    if not types or set(types) - set(transit.VEHICLE_MODES):
        return tool_error("INVALID_ARGUMENT", f"Unknown transit types {list(types)}.", retryable=False,
                          next_step="Use 'subway' and/or 'bus'.")
    return types


# --- Walking providers ---


def _walking_legs(points):
    """Per-leg walking dicts (minutes, meters, instructions, streets) and the provider that answered."""
    try:
        raw, source = _valhalla_route(points), "valhalla-fossgis"
    except UpstreamError:
        raw, source = _osrm_route(points), "osrm-fossgis-foot"
    legs = []
    for seconds, meters, instructions, streets in raw:
        if meters < SAME_SPOT_M:
            instructions, streets = [], []  # the router snaps both points to one street; its name misleads
        legs.append({"minutes": round(seconds / 60, 1), "meters": round(meters),
                     "instructions": instructions, "streets": streets})
    return legs, source


def _valhalla_route(points):
    payload = {
        "locations": [{"lat": p["lat"], "lon": p["lng"], "type": "break"} for p in points],
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
    if len(raw_legs) != len(points) - 1:
        raise UpstreamError("valhalla", f"expected {len(points) - 1} legs, got {len(raw_legs)}")
    return raw_legs


def _osrm_route(points):
    coordinates = ";".join(f"{p['lng']},{p['lat']}" for p in points)
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
    if len(raw_legs) != len(points) - 1:
        raise UpstreamError("osrm", f"expected {len(points) - 1} legs, got {len(raw_legs)}")
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


def _heading(origin, destination):
    """True-compass direction of the straight line between two points. Manhattan's street grid is
    rotated about 29 degrees east of true north, so "uptown" reads as north-northeast here."""
    lat1, lat2 = math.radians(origin["lat"]), math.radians(destination["lat"])
    dlng = math.radians(destination["lng"] - origin["lng"])
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    bearing = (math.degrees(math.atan2(x, y)) + 360) % 360
    return COMPASS[round(bearing / 45) % 8]


def _readable(walk, heading):
    """A summary line, then the turns onto named streets. OpenStreetMap maps Manhattan sidewalks as unnamed
    ways, so steps like "Turn left onto the walkway" say nothing useful and are left out."""
    if walk["meters"] < SAME_SPOT_M:
        return []
    via = f" via {', then '.join(walk['streets'])}" if walk["streets"] else ""
    named = [step for step in walk["instructions"] if "walkway" not in step.lower()]
    return [f"Walk about {max(1, int(walk['minutes'] + 0.5))} min heading {heading}{via}.", *named]


def _local(moment):
    return moment.astimezone(ZoneInfo(NYC_TIMEZONE)).strftime("%-I:%M %p")


def _unique(names):
    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen
