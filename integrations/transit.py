"""Subway and bus directions for one leg, from Google Routes (travelMode TRANSIT).

Transit routes take no intermediate waypoints, so every leg is its own request, timed from when
the user is ready to leave. Google's route `duration` starts at the latest moment that still
catches the first vehicle; this module reports door-to-door time from the ready time instead,
so any wait for the train counts against a deadline.

Auth, first match wins:
- GOOGLE_MAPS_API_KEY, an API key restricted to the Routes API.
- Google application-default credentials. User credentials bill to SCAVAGENT_ROUTES_PROJECT, or
  else their quota project; on Cloud Run the runtime service account's own project pays. The
  Routes API must be enabled in whichever project pays.

Guards: at most SCAVAGENT_ROUTES_DAILY_LIMIT requests per process per UTC day (default 500). A request
for the same leg in the same minute reuses Google's earlier route, re-timed for the new ready time, unless
its first vehicle would already have left.
"""

import os
import time
from datetime import datetime, timedelta, timezone

from integrations.common import NoRoute, UpstreamError, fetch_json

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = ",".join([
    "routes.duration", "routes.distanceMeters", "routes.localizedValues", "routes.warnings",
    "routes.travelAdvisory.transitFare", "routes.legs.steps.travelMode", "routes.legs.steps.staticDuration",
    "routes.legs.steps.distanceMeters", "routes.legs.steps.navigationInstruction", "routes.legs.steps.transitDetails",
])
VEHICLE_MODES = {"subway": ["SUBWAY", "TRAIN", "LIGHT_RAIL", "RAIL"], "bus": ["BUS"]}
CACHE_SECONDS = 600
MAX_PAST = timedelta(days=7)  # Google accepts departure times up to 7 days back and 100 days ahead
MAX_FUTURE = timedelta(days=100)

_credentials = None
_cache = {}  # request key -> (expires at, Google's route)
_usage = {"day": None, "count": 0}


def transit_leg(origin, destination, depart_at: datetime, transit_types=("subway", "bus")) -> dict:
    """The best transit option from origin to destination for someone ready at `depart_at`.

    origin/destination are (lat, lng). Returns has_transit False when Google's best answer is to
    walk the whole way. Raises NoRoute when there is no answer, UpstreamError when the call fails.
    """
    now = datetime.now(timezone.utc)
    if not now - MAX_PAST <= depart_at <= now + MAX_FUTURE:
        raise UpstreamError("google-routes", "transit times are only available from 7 days ago to 100 days ahead",
                            retryable=False)
    modes = sorted({mode for kind in transit_types for mode in VEHICLE_MODES[kind]})
    key = (round(origin[0], 5), round(origin[1], 5), round(destination[0], 5), round(destination[1], 5),
           tuple(modes), int(depart_at.timestamp() // 60))
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        leg = parse_route(cached[1], depart_at)  # times are relative to this caller's ready time
        if not leg["has_transit"] or datetime.fromisoformat(leg["leave_by"]) >= depart_at - timedelta(seconds=30):
            return leg

    _count_request()
    body = {
        "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
        "destination": {"location": {"latLng": {"latitude": destination[0], "longitude": destination[1]}}},
        "travelMode": "TRANSIT",
        "departureTime": depart_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "transitPreferences": {"allowedTravelModes": modes},
        "languageCode": "en-US",
        "units": "IMPERIAL",
    }
    try:
        response = fetch_json("google-routes", "POST", ROUTES_URL, json=body, timeout=20,
                              headers={**_auth_headers(), "X-Goog-FieldMask": FIELD_MASK})
    except UpstreamError as e:
        raise _explained(e) from e
    if not response.get("routes"):
        raise NoRoute("Google Routes found no transit route")
    route = response["routes"][0]
    leg = parse_route(route, depart_at)
    if leg["has_transit"] and datetime.fromisoformat(leg["leave_by"]) < depart_at - timedelta(minutes=2):
        raise UpstreamError("google-routes", "the answer's first vehicle leaves before the user is ready")
    _cache[key] = (time.monotonic() + CACHE_SECONDS, route)
    return leg


def parse_route(route: dict, ready_at: datetime) -> dict:
    """Door-to-door timing and readable segments for one Google transit route."""
    segments, walk_before, walk_after = [], 0.0, 0.0
    first_departure = last_arrival = None
    for step in route.get("legs", [{}])[0].get("steps", []):
        seconds = _seconds(step.get("staticDuration"))
        details = step.get("transitDetails")
        if step.get("travelMode") == "TRANSIT" and details:
            stops = details["stopDetails"]
            depart, arrive = _time(stops["departureTime"]), _time(stops["arrivalTime"])
            first_departure = first_departure or depart
            last_arrival, walk_after = arrive, 0.0
            line = details.get("transitLine", {})
            local = details.get("localizedValues", {})
            segments.append({
                "mode": "bus" if line.get("vehicle", {}).get("type") == "BUS" else "subway",
                "line": _short_name(line),
                "line_name": line.get("name"),
                "color": line.get("color"),
                "headsign": details.get("headsign"),
                "from_stop": stops["departureStop"]["name"],
                "to_stop": stops["arrivalStop"]["name"],
                "depart_at": depart.isoformat(),
                "arrive_at": arrive.isoformat(),
                "depart_local": _local_text(local.get("departureTime")),
                "arrive_local": _local_text(local.get("arrivalTime")),
                "stops": details.get("stopCount"),
                "headway_minutes": round(_seconds(details.get("headway")) / 60) if details.get("headway") else None,
            })
            continue
        instruction = (step.get("navigationInstruction") or {}).get("instructions")
        if segments and segments[-1]["mode"] == "walk":
            walk = segments[-1]
        else:
            walk = {"mode": "walk", "seconds": 0.0, "meters": 0, "notes": []}
            segments.append(walk)
        walk["seconds"] += seconds
        walk["meters"] += step.get("distanceMeters", 0)
        if instruction and instruction.startswith(("Take entrance", "Take exit")):
            walk["notes"].append(instruction)
        if first_departure is None:
            walk_before += seconds
        else:
            walk_after += seconds

    for segment in segments:
        if segment["mode"] == "walk":
            segment["minutes"] = round(segment.pop("seconds") / 60, 1)
    fare = route.get("localizedValues", {}).get("transitFare", {}).get("text")
    if first_departure is None:
        minutes = round(_seconds(route.get("duration")) / 60, 1)
        return {"has_transit": False, "duration_minutes": minutes, "distance_m": route.get("distanceMeters"),
                "segments": segments, "fare": None}

    leave_by = first_departure - timedelta(seconds=walk_before)
    arrive_at = last_arrival + timedelta(seconds=walk_after)
    return {
        "has_transit": True,
        "leave_by": leave_by.isoformat(),
        "arrive_at": arrive_at.isoformat(),
        "wait_minutes": round(max(0.0, (leave_by - ready_at).total_seconds()) / 60, 1),
        "duration_minutes": round((arrive_at - ready_at).total_seconds() / 60, 1),
        "distance_m": route.get("distanceMeters"),
        "segments": segments,
        "fare": fare,
        "instructions": _instructions(segments),
    }


def _instructions(segments):
    lines = []
    for i, segment in enumerate(segments):
        if segment["mode"] == "walk":
            target = next((s["from_stop"] for s in segments[i + 1:] if s["mode"] != "walk"), "the destination")
            notes = f" ({'; '.join(segment['notes'])})" if segment["notes"] else ""
            lines.append(f"Walk {segment['minutes']:.0f} min to {target}{notes}.")
        else:
            vehicle = "bus" if segment["mode"] == "bus" else "train"
            lines.append(f"Take the {segment['line']} {vehicle} toward {segment['headsign']} from {segment['from_stop']} "
                         f"at {segment['depart_local']}; ride {segment['stops']} stops to {segment['to_stop']} "
                         f"(arrives {segment['arrive_local']}).")
    return lines


def _auth_headers():
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        return {"X-Goog-Api-Key": key}
    global _credentials
    try:
        import google.auth
        import google.auth.transport.requests

        if _credentials is None:
            _credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not _credentials.valid:
            _credentials.refresh(google.auth.transport.requests.Request())
    except Exception as e:  # missing or expired credentials must not crash the tool loop
        raise UpstreamError("google-routes", f"no usable Google credentials ({type(e).__name__})", retryable=False) from e
    headers = {"Authorization": f"Bearer {_credentials.token}"}
    project = os.environ.get("SCAVAGENT_ROUTES_PROJECT") or getattr(_credentials, "quota_project_id", None)
    if project:
        headers["x-goog-user-project"] = project
    return headers


def _count_request():
    today = datetime.now(timezone.utc).date()
    if _usage["day"] != today:
        _usage.update(day=today, count=0)
    limit = int(os.environ.get("SCAVAGENT_ROUTES_DAILY_LIMIT", "500"))
    if _usage["count"] >= limit:
        raise UpstreamError("google-routes", f"this server's daily limit of {limit} transit lookups is used up",
                            retryable=False)
    _usage["count"] += 1


def _explained(error: UpstreamError) -> UpstreamError:
    """Name the fix for the failures a deployment is likely to hit, without quoting credentials."""
    body = error.body if isinstance(error.body, dict) else {}
    details = body.get("error", {}).get("details", [])
    reason = next((d.get("reason") for d in details if d.get("reason")), None)
    if reason == "SERVICE_DISABLED":
        project = next((d.get("metadata", {}).get("consumer") for d in details if d.get("metadata")), "the project")
        return UpstreamError("google-routes", f"the Routes API is not enabled for {project}", retryable=False)
    if error.status in (401, 403):
        return UpstreamError("google-routes", f"not authorized (HTTP {error.status}); check the API key or "
                                              "credentials and the billing project", retryable=False)
    return error


def _seconds(value):
    if not value:
        return 0.0
    try:
        return float(str(value).rstrip("s"))
    except ValueError:
        return 0.0


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _local_text(value):
    text = ((value or {}).get("time") or {}).get("text")
    return text.replace(" ", " ") if text else None


def _short_name(line):
    name = line.get("nameShort") or line.get("name") or "?"
    for suffix in (" Line", " Train"):
        if name.endswith(suffix) and len(name) - len(suffix) <= 3:
            return name[: -len(suffix)]
    return name


def reset_for_tests():
    _cache.clear()
    _usage.update(day=None, count=0)
    global _credentials
    _credentials = None

