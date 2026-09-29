"""Live MTA subway arrivals and service alerts at one station (get_transit_arrivals).

Journey planning stays with integrations.routes; this answers "when is the next train here, and
is anything wrong on the line?"

Sources, all keyless:
- Stations: MTA Subway Stations on NY Open Data (39hk-dx4f): GTFS stop ids, names, daytime routes,
  coordinates, and the direction labels riders see on signs ("Uptown", "Downtown").
- Arrivals: the MTA's GTFS-realtime subway feeds, one per group of lines, decoded with
  gtfs-realtime-bindings. Every answer carries the feed's own timestamp.
- Alerts: the MTA's subway alerts feed in JSON, filtered to the station's lines and active now.
"""

import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from integrations.common import UpstreamError, distance_m, fetch_bytes, fetch_json, read_point, upstream_failure, utc_now
from schemas import NYC_TIMEZONE, Freshness, tool_error, tool_ok

STATIONS_API = "https://data.ny.gov/resource/39hk-dx4f.json"
FEED_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2F{feed}"
ALERTS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json"
FEEDS = {  # route id -> the realtime feed that carries it
    **dict.fromkeys(["1", "2", "3", "4", "5", "6", "7", "GS"], "gtfs"),
    **dict.fromkeys(["A", "C", "E", "H", "FS"], "gtfs-ace"),
    **dict.fromkeys(["B", "D", "F", "M"], "gtfs-bdfm"),
    "G": "gtfs-g",
    **dict.fromkeys(["J", "Z"], "gtfs-jz"),
    **dict.fromkeys(["N", "Q", "R", "W"], "gtfs-nqrw"),
    "L": "gtfs-l",
    "SI": "gtfs-si",
}
STATIONS_SECONDS = 86_400
ALERTS_SECONDS = 60
OLD_FEED_SECONDS = 180  # warn beyond this
STALE_FEED_SECONDS = 600  # refuse beyond this: old predictions are worse than none
MAX_ARRIVALS = 4  # per direction

_stations = {"expires": 0.0, "rows": []}
_alerts = {"expires": 0.0, "body": None}


def get_transit_arrivals(station, line=None, direction=None, lat=None, lng=None):
    """Next subway arrivals at a station, by direction, with the lines' active service alerts.

    station: a name as signs and transit directions give it ("86 St", "W 4 St-Wash Sq") or a GTFS stop id
    ("A20"). line narrows it to one route ("A"); lat/lng pick the nearest of several same-named stations.
    direction: "uptown"/"north" or "downtown"/"south" (or the sign's label, e.g. "Manhattan").
    """
    try:
        rows = _station_rows()
    except UpstreamError as e:
        return upstream_failure(e, "Give the scheduled times from get_route and say live arrivals are unavailable.")
    line = str(line).upper().rstrip("X") if line else None
    near = read_point({"lat": lat, "lng": lng}) if lat is not None and lng is not None else None
    matches = _matching_stations(rows, str(station or ""), line)
    if not matches:
        return tool_error("NO_MATCH", f"No subway station matches {station!r}" + (f" on the {line}" if line else "") + ".",
                          retryable=False, next_step="Use the station name from the transit directions, e.g. '86 St'.")
    if near is not None:
        matches.sort(key=lambda r: distance_m(near[0], near[1], float(r["gtfs_latitude"]), float(r["gtfs_longitude"])))
    elif len({r["gtfs_stop_id"] for r in matches}) > 1:
        options = "; ".join(f"{r['stop_name']} ({r['daytime_routes']})" for r in matches[:6])
        return tool_error("INVALID_ARGUMENT", f"{station!r} matches several stations: {options}.", retryable=False,
                          next_step="Call again with line or lat/lng to pick one.")
    stop = matches[0]
    routes = [line] if line else stop["daytime_routes"].split()

    now = utc_now()
    arrivals, oldest = [], None
    try:
        for feed_name in sorted({FEEDS[r] for r in routes if r in FEEDS} or {"gtfs"}):
            feed = _feed(feed_name)
            oldest = min(oldest or feed.header.timestamp, feed.header.timestamp)
            arrivals += _arrivals(feed, stop, line, now, rows)
    except UpstreamError as e:
        return upstream_failure(e, "Give the scheduled times from get_route and say live arrivals are unavailable.")
    age = now.timestamp() - oldest
    if age > STALE_FEED_SECONDS:
        return tool_error("STALE_DATA", f"The MTA feed was last updated {age / 60:.0f} minutes ago.", retryable=True,
                          next_step="Give the scheduled times from get_route and say live arrivals are unavailable.")

    wanted = _direction(direction, stop)
    by_direction = {}
    for arrival in sorted(arrivals, key=lambda a: a["arrives_at"]):
        if wanted and arrival["direction"] != wanted:
            continue
        group = by_direction.setdefault(arrival["direction"], [])
        if len(group) < MAX_ARRIVALS:
            group.append(arrival)

    warnings = []
    if age > OLD_FEED_SECONDS:
        warnings.append(f"The feed is {age / 60:.0f} minutes old; times may have shifted.")
    try:
        alerts = _alerts_for(set(routes) | {a["line"] for a in arrivals}, now)
    except UpstreamError as e:
        alerts = []
        warnings.append(f"Service alerts unavailable: {e}")
    if not by_direction:
        warnings.append("No arrivals are predicted here right now; service may be suspended or not running at this hour.")
    return tool_ok({
        "station": {"name": stop["stop_name"], "gtfs_stop_id": stop["gtfs_stop_id"], "lines": stop["daytime_routes"].split(),
                    "point": {"lat": float(stop["gtfs_latitude"]), "lng": float(stop["gtfs_longitude"])}},
        "arrivals": by_direction,
        "alerts": alerts,
        "feed_updated_at": datetime.fromtimestamp(oldest, timezone.utc).isoformat(),
    }, warnings=warnings, freshness=Freshness(kind="live", as_of=datetime.fromtimestamp(oldest, timezone.utc), retrieved_at=now))


# --- Stations ---


def _station_rows():
    if _stations["expires"] < time.monotonic():
        rows = fetch_json("nyc-open-data", "GET", STATIONS_API, params={
            "$select": "gtfs_stop_id,stop_name,daytime_routes,gtfs_latitude,gtfs_longitude,"
                       "north_direction_label,south_direction_label", "$limit": 1000})
        _stations.update(expires=time.monotonic() + STATIONS_SECONDS, rows=rows)
    return _stations["rows"]


def _matching_stations(rows, text, line):
    key = _station_key(text)
    if not key:
        return []
    exact = [r for r in rows if r["gtfs_stop_id"].upper() == text.strip().upper()]
    found = exact or [r for r in rows if _station_key(r["stop_name"]) == key] or [
        r for r in rows if key in _station_key(r["stop_name"])]
    if not line:
        return found
    on_line = [r for r in found if line in r["daytime_routes"].split()]
    # At night some trains stop where their line is not a daytime route (the A at 86 St on Central Park West).
    return on_line or [r for r in found if FEEDS.get(line) in {FEEDS.get(route) for route in r["daytime_routes"].split()}]


def _station_key(name):
    """'86th Street' and '86 St' compare equal; so do 'W 4 St-Washington Sq' and 'W 4 St-Wash Sq'."""
    name = name.lower().replace("washington", "wash").replace("street", "st").replace("avenue", "av")
    name = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", name).replace("square", "sq").replace("ave", "av")
    return re.sub(r"[^a-z0-9]", "", name)


# --- Arrivals ---


def _feed(name):
    body = fetch_bytes("mta-realtime", FEED_URL.format(feed=name), timeout=10)
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(body)
    except DecodeError as e:
        raise UpstreamError("mta-realtime", "feed could not be decoded") from e
    return feed


def _arrivals(feed, stop, line, now, rows):
    names = {r["gtfs_stop_id"]: r["stop_name"] for r in rows}
    labels = {"N": stop["north_direction_label"], "S": stop["south_direction_label"]}
    local = ZoneInfo(NYC_TIMEZONE)
    found = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        route = update.trip.route_id.upper().rstrip("X")
        if line and route != line:
            continue
        updates = list(update.stop_time_update)
        for stop_time in updates:
            if stop_time.stop_id[:-1] != stop["gtfs_stop_id"] or stop_time.stop_id[-1] not in labels:
                continue
            moment = stop_time.arrival.time or stop_time.departure.time
            if not moment or moment < now.timestamp() - 30:
                continue
            arrives = datetime.fromtimestamp(moment, timezone.utc)
            found.append({
                "line": route,
                "direction": labels[stop_time.stop_id[-1]],
                "toward": names.get(updates[-1].stop_id[:-1]),
                "arrives_at": arrives.isoformat(),
                "arrives_local": arrives.astimezone(local).strftime("%-I:%M %p"),
                "minutes_away": max(0, round((moment - now.timestamp()) / 60)),
            })
    return found


def _direction(direction, stop):
    if not direction:
        return None
    text = str(direction).lower()
    if text in ("n", "north", "northbound", "uptown"):
        return stop["north_direction_label"]
    if text in ("s", "south", "southbound", "downtown"):
        return stop["south_direction_label"]
    for label in (stop["north_direction_label"], stop["south_direction_label"]):
        if text in label.lower():
            return label
    return None


# --- Alerts ---


def _alerts_for(routes, now):
    if _alerts["expires"] < time.monotonic():
        _alerts.update(expires=time.monotonic() + ALERTS_SECONDS, body=fetch_json("mta-alerts", "GET", ALERTS_URL, timeout=10))
    stamp = now.timestamp()
    found = []
    for entity in _alerts["body"].get("entity", []):
        alert = entity.get("alert", {})
        lines = sorted({e.get("route_id") for e in alert.get("informed_entity", []) if e.get("route_id") in routes})
        periods = alert.get("active_period") or [{}]
        active = any(int(p.get("start", 0)) <= stamp <= int(p.get("end") or stamp + 1) for p in periods)
        if not lines or not active:
            continue
        text = next((t["text"] for t in alert.get("header_text", {}).get("translation", []) if t.get("language") == "en"), None)
        if text:
            kind = alert.get("transit_realtime.mercury_alert", {}).get("alert_type")
            found.append({"lines": lines, "type": kind, "text": text})
    return found[:5]


def reset_for_tests():
    _stations.update(expires=0.0, rows=[])
    _alerts.update(expires=0.0, body=None)
