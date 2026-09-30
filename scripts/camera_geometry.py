"""Where a standing spot lies relative to its camera's intersection, from OpenStreetMap street geometry.

describe(): a pin -> "north side of East 116th Street, about 30 m east of Park Avenue".
locate(): the reverse, for a spot read off a still ("north side of 116th, 20 m east of Park") -> a pin.

Directions follow Manhattan usage: on the street grid (rotated about 29 degrees from true north)
"north" means uptown along the avenues. Distances are estimates from street centerlines, good to
several meters; the words, not the pin, are what guide a visitor.
"""

import math
import re
from dataclasses import dataclass

from integrations import geocoding
from integrations.common import UpstreamError, fetch_json
from schemas import LatLng

GRID_DEG = 29.0  # Manhattan's grid is rotated this far clockwise from true north
GRID_TOLERANCE_DEG = 12.0
CORNER_SLACK_M = 8.0  # within this of both streets' sidewalk lines, a spot is a corner
LANE_M, PARKING_M, HALF_SIDEWALK_M = 3.2, 2.4, 2.5
CARDINALS = ("north", "east", "south", "west")
ALT_NAME_TAGS = ("alt_name", "official_name", "old_name", "short_name")


@dataclass(frozen=True)
class Street:
    name: str  # display name, e.g. "East 116th Street"
    direction: tuple[float, float]  # unit vector (east, north) along the street
    sidewalk_m: float  # typical distance from the street's center to the middle of its sidewalk


def local_xy(origin: LatLng, point: LatLng):
    """Meters east and north of origin (equirectangular; fine within a few hundred meters)."""
    return ((point.lng - origin.lng) * 111_320 * math.cos(math.radians(origin.lat)), (point.lat - origin.lat) * 111_320)


def from_local(origin: LatLng, east: float, north: float) -> LatLng:
    return LatLng(lat=round(origin.lat + north / 111_320, 7),
                  lng=round(origin.lng + east / (111_320 * math.cos(math.radians(origin.lat))), 7))


def _bearing(v):
    return math.degrees(math.atan2(v[0], v[1])) % 360


def on_grid(streets):
    """True when both streets run along Manhattan's rotated grid."""
    def aligned(s):
        b = _bearing(s.direction) % 90
        return min(abs(b - GRID_DEG), abs(b - GRID_DEG + 90), abs(b - GRID_DEG - 90)) <= GRID_TOLERANCE_DEG
    return all(aligned(s) for s in streets)


def cardinal(v, grid):
    b = (_bearing(v) - (GRID_DEG if grid else 0)) % 360
    return CARDINALS[int(((b + 45) % 360) // 90)]


def _normal(u):
    return (u[1], -u[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def describe(center: LatLng, streets, point: LatLng):
    """Side of street and position of a pin, from the two streets meeting at center."""
    a, b = streets
    grid = on_grid(streets)
    off = local_xy(center, point)
    across = {s: _dot(off, _normal(s.direction)) for s in (a, b)}
    side = {s: cardinal(tuple(math.copysign(1, across[s]) * c for c in _normal(s.direction)), grid) for s in (a, b)}
    # Judge by sidewalks, not centerlines: a wide avenue's sidewalk can lie 35 m from its center, farther than a
    # cross street's centerline, and a spot on that sidewalk is still on the avenue.
    if all(abs(across[s]) <= s.sidewalk_m + CORNER_SLACK_M for s in (a, b)):
        ns, ew = sorted((side[a], side[b]), key=lambda w: w in ("east", "west"))
        corner = f"{ns}{ew}" if {ns, ew} & {"north", "south"} and {ns, ew} & {"east", "west"} else ns
        text = f"{corner} corner of {a.name} and {b.name}"
        return {"side_of_street": text, "on": a.name, "cross": b.name, "along_m": 0, "corner": True,
                "stop_name": f"{a.name} and {b.name}, {corner} corner"}
    on, cross = sorted((a, b), key=lambda s: (abs(abs(across[s]) - s.sidewalk_m), abs(across[s])))
    along = round(abs(across[cross]) / 5) * 5
    text = f"{side[on]} side of {on.name}, about {along} m {side[cross]} of {cross.name}"
    return {"side_of_street": text, "on": on.name, "cross": cross.name, "along_m": along, "corner": False,
            "stop_name": f"{on.name} at {cross.name}, {side[on]} side"}


def locate(center: LatLng, on: Street, cross: Street, side: str, direction: str, along_m: float, offset_m=None):
    """The pin for "<side> side of <on>, <along_m> m <direction> of <cross>" (along_m 0 = the corner)."""
    grid = on_grid((on, cross))
    def signed(street, word, distance):
        n = _normal(street.direction)
        if cardinal(n, grid) == word:
            return distance
        if cardinal((-n[0], -n[1]), grid) == word:
            return -distance
        raise ValueError(f"{word} is not a side of {street.name}")
    target_on = signed(on, side, on.sidewalk_m if offset_m is None else offset_m)
    target_cross = signed(cross, direction, max(along_m, cross.sidewalk_m))
    # Solve p . n_on = target_on and p . n_cross = target_cross.
    n1, n2 = _normal(on.direction), _normal(cross.direction)
    det = n1[0] * n2[1] - n1[1] * n2[0]
    if abs(det) < 0.2:
        raise ValueError("The two streets are nearly parallel here")
    east = (target_on * n2[1] - target_cross * n1[1]) / det
    north = (n1[0] * target_cross - n2[0] * target_on) / det
    return from_local(center, east, north)


# --- OpenStreetMap ---


def camera_streets(name):
    """The two normalized street names in a DOT camera name such as "Park Ave @ E 116 Street"."""
    name = re.sub(r"\s*\(.*?\)|\s+-\s.*$|-\s*quad\b.*$|\bupper level\b", "", name, flags=re.IGNORECASE)
    parts = [p.strip() for p in name.split("@")]
    if len(parts) != 2 or any("/" in p for p in parts):
        return None  # three-way names ("Broadway @ 6 Ave / 33 St") must not become one invented street
    out = []
    for part in parts:
        for guess in (part, part + " avenue", part + " street"):
            normalized = geocoding.normalize_street(guess)
            if normalized:
                out.append(normalized)
                break
    return out if len(out) == 2 else None


def intersection_streets(camera_name, mount: LatLng, radius_m=250, urls=None):
    """(center, [Street, Street]) for a camera's intersection: one Overpass query around its mount."""
    names = camera_streets(camera_name)
    if not names:
        raise ValueError(f"Cannot read two streets from {camera_name!r}")
    patterns = [geocoding.street_pattern(n) for n in names]
    near = f"(around:{radius_m},{mount.lat},{mount.lng})"
    def ways(pattern, into):
        # Honorary co-names can take over "name" (2nd Avenue in East Harlem is "México-Tenochtitlan Avenue"),
        # so also match alternate names, which may hold several values separated by ";".
        listed = "(^|;) *" + pattern[1:-1] + " *(;|$)"
        tags = [("name", pattern)] + [(tag, listed) for tag in ALT_NAME_TAGS]
        union = "".join(f'way{near}["highway"]["{tag}"~"{rx.replace(chr(92), chr(92) * 2)}",i];' for tag, rx in tags)
        return f"({union})->.{into};"
    query = (f"[out:json][timeout:25];{ways(patterns[0], 'a')}{ways(patterns[1], 'b')}"
             "node(w.a)(w.b)->.x;.x out;.a out tags geom;.b out tags geom;")
    body, error = None, None
    for url in urls or geocoding.OVERPASS_URLS:
        try:
            body = fetch_json("overpass", "POST", url, data={"data": query}, timeout=30)
        except UpstreamError as e:  # rate limits and overload are common; try the mirror
            error = e
            continue
        if "error" in str(body.get("remark", "")).lower():  # an overloaded server answers 200 with no data
            body, error = None, body["remark"][:120]
            continue
        break
    if body is None:
        raise ValueError(f"OpenStreetMap (Overpass) did not answer: {error}")
    elements = body.get("elements", [])
    nodes = [LatLng(lat=e["lat"], lng=e["lon"]) for e in elements if e.get("type") == "node"]
    if not nodes:
        raise ValueError(f"OpenStreetMap shows no intersection for {camera_name!r} near the camera")
    # A divided street meets another at several nodes: average those near the one closest to the mount.
    nearest = min(nodes, key=lambda n: math.hypot(*local_xy(mount, n)))
    group = [n for n in nodes if math.hypot(*local_xy(nearest, n)) <= 60]
    center = LatLng(lat=sum(n.lat for n in group) / len(group), lng=sum(n.lng for n in group) / len(group))
    found = [e for e in elements if e.get("type") == "way" and e.get("geometry")]
    streets = []
    for name, pattern in zip(names, patterns):
        mine = [w for w in found if _named(w.get("tags", {}), pattern)]
        if not mine:
            raise ValueError(f"No OpenStreetMap geometry for {name} near the camera")
        streets.append(street_from_ways(geocoding._display(name), center, mine))
    return center, streets


def _named(tags, pattern):
    """Whether a way's name, or one of its alternate names, matches the street pattern."""
    names = [tags.get("name", "")] + [n.strip() for tag in ALT_NAME_TAGS for n in tags.get(tag, "").split(";")]
    return any(re.search(pattern, n, re.IGNORECASE) for n in names if n)


def street_from_ways(display, center, ways):
    """Direction and sidewalk distance from OSM ways (each with tags and geometry) near center."""
    reference, total, carriageways = None, [0.0, 0.0], []
    for way in ways:
        pts = [local_xy(center, LatLng(lat=g["lat"], lng=g["lon"])) for g in way["geometry"]]
        for p, q in zip(pts, pts[1:]):
            if math.hypot(*p) > 120 and math.hypot(*q) > 120:
                continue
            d = (q[0] - p[0], q[1] - p[1])
            length = math.hypot(*d)
            if length == 0:
                continue
            u = (d[0] / length, d[1] / length)
            reference = reference or u
            sign = 1 if _dot(u, reference) >= 0 else -1
            total = [total[0] + sign * d[0], total[1] + sign * d[1]]
        lanes = way.get("tags", {}).get("lanes")
        near = [p for p in pts if math.hypot(*p) <= 120]
        if near:
            carriageways.append((near, int(lanes) if lanes and lanes.isdigit() else 2))
    norm = math.hypot(*total)
    if not norm:
        raise ValueError(f"No usable geometry for {display}")
    u = (total[0] / norm, total[1] / norm)
    n = _normal(u)
    # A divided street has carriageways on both sides of its center; the sidewalk lies beyond the outer one.
    reach = max(abs(sum(_dot(p, n) for p in pts) / len(pts)) + lanes * LANE_M / 2 for pts, lanes in carriageways)
    return Street(display, u, round(reach + PARKING_M + HALF_SIDEWALK_M, 1))
