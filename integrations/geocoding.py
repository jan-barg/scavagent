"""Resolve a typed NYC place (street intersection, address, or named landmark) to coordinates.

Returns a schemas.LocationContext with source "geocoded", plus how the place was matched.

Providers, all keyless:
- Intersections: OpenStreetMap through Overpass, taking the node the two streets share.
  Nominatim and NYC GeoSearch both returned nothing for "Central Park West and West 86th Street"
  when checked on 2026-09-28.
- Street addresses: NYC Planning Labs GeoSearch, built on the city's Property Address Directory.
- Landmarks and other names: OpenStreetMap Nominatim, limited to the NYC area.
"""

import re
import time
from functools import lru_cache

from integrations.common import NYC_BOUNDS, UpstreamError, distance_m, fetch_json, in_nyc, reference, utc_now
from schemas import LatLng, LocationContext, tool_error, tool_ok

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    # A mirror with its own rate limit. Slower (about 8 s when checked), so only a fallback.
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"

# Shared nodes farther apart than this are separate intersections (Broadway meets some streets in
# more than one borough). The several nodes of one divided avenue or square fall well within it.
SAME_INTERSECTION_M = 150
# A named feature whose bounding box is larger than this has no single useful meeting point.
LARGE_FEATURE_M = 250

ABBREVIATIONS = {
    "w": "west", "e": "east", "n": "north", "s": "south",
    "st": "street", "str": "street", "ave": "avenue", "av": "avenue", "blvd": "boulevard",
    "pl": "place", "dr": "drive", "rd": "road", "pkwy": "parkway", "sq": "square",
    "ter": "terrace", "ln": "lane", "hwy": "highway", "plz": "plaza", "cir": "circle",
    "cpw": "central park west", "cps": "central park south",
}
NUMBER_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
}
ORDINAL_WORDS = {number: word for word, number in NUMBER_WORDS.items()}
STREET_TYPES = {
    "street", "avenue", "place", "drive", "road", "boulevard", "parkway", "square", "terrace",
    "lane", "highway", "plaza", "circle", "way", "row", "slip", "walk", "alley",
}
# Common names that carry no street-type word.
TYPELESS_STREETS = {"broadway", "bowery", "central park west", "central park south", "park row"}
# OpenStreetMap uses the official name; people often type the other one.
SYNONYMS = {"6th avenue": "avenue of the americas", "avenue of the americas": "6th avenue"}

INTERSECTION_SPLIT = re.compile(r"\s+(?:and|at)\s+|\s*[&/@]\s*", re.IGNORECASE)
ADDRESS_START = re.compile(r"^\d+[a-z]?(?:-\d+)?\s+\S", re.IGNORECASE)

_last_nominatim_call = 0.0


def geocode_place(text):
    """Resolve a typed NYC place to coordinates: a cross street, a street address, or a named place.

    Intersections resolve to the node both streets share, addresses to the city's address point,
    and names to the feature's center. data["location"] is a LocationContext for AdventureRequest.
    """
    query = " ".join(str(text or "").split())
    if not query:
        return tool_error("INVALID_ARGUMENT", "The place text is empty.", retryable=False,
                          next_step="Ask the user for a cross street, a street address, or a landmark name.")
    if len(query) > 200:
        return tool_error("INVALID_ARGUMENT", "The place text is too long to be a single place.", retryable=False,
                          next_step="Pass only the place, e.g. 'Central Park West and West 86th Street'.")

    streets = parse_intersection(query)
    lookups = []
    if streets:
        lookups.append(lambda: _intersection(*streets))
    if ADDRESS_START.match(query):
        lookups.append(lambda: _address(query))
    # Nominatim finds landmarks, and sometimes a bus stop named after an intersection.
    lookups.append(lambda: _named_place(" & ".join(map(_display, streets)) if streets else query,
                                        is_intersection=bool(streets)))

    errors = []
    for lookup in lookups:
        try:
            place = lookup()
        except UpstreamError as e:
            errors.append(str(e))
            continue
        if place:
            break
    else:
        if errors:
            return tool_error("UPSTREAM_UNAVAILABLE", "Could not finish the lookup: " + "; ".join(errors),
                              retryable=True, next_step="Try again shortly, or ask the user for a nearby landmark.")
        return tool_error("NO_MATCH", f"No NYC place matched {query!r}.", retryable=False,
                          next_step="Ask for a cross street (e.g. 'Columbus Avenue and West 81st Street'), "
                                    "a street address, or a well-known landmark.")

    warnings = place.pop("warnings") + [f"Fell back after a provider failed: {e}" for e in errors]
    if not in_nyc(place["lat"], place["lng"]):
        return tool_error("OUTSIDE_COVERAGE", f"{place['label']} is outside New York City.", retryable=False,
                          next_step="Ask for a place within the five boroughs.")
    # Geocoders report no accuracy, so accuracy_m stays None; match_type says what the point is.
    location = LocationContext(point=LatLng(lat=place.pop("lat"), lng=place.pop("lng")),
                               place_text=place["label"], source="geocoded", observed_at=utc_now())
    return tool_ok({"location": location.model_dump(mode="json"), "query": query, **place},
                   warnings=warnings, freshness=reference())


def parse_intersection(text):
    """Split "X and Y", "X & Y", or "corner of X and Y" into two normalized street names, or None."""
    text = re.sub(r"^(?:the\s+)?(?:corner|intersection)\s+of\s+", "", text.strip(), flags=re.IGNORECASE)
    text = text.split(",")[0]  # drop ", New York, NY" and similar qualifiers
    parts = INTERSECTION_SPLIT.split(text)
    if len(parts) != 2:
        return None
    streets = [normalize_street(part) for part in parts]
    return streets if all(streets) else None


def normalize_street(text):
    """Spell out a typed street name the way OpenStreetMap names NYC streets, or None.

    "W 86 St" -> "west 86th street", "CPW" -> "central park west", "86th" -> "86th street".
    Returns None when the text does not look like a street, so "Barnes & Noble" is not treated
    as an intersection.
    """
    words = re.sub(r"[^a-z0-9\s-]", " ", text.lower()).split()
    out = []
    for i, word in enumerate(words):
        if word == "st" and i == 0 and len(words) > 1:
            out.append("saint")  # "St Nicholas Ave"
        elif word in ABBREVIATIONS:
            out.extend(ABBREVIATIONS[word].split())
        elif word in NUMBER_WORDS:
            out.append(ordinal(NUMBER_WORDS[word]))
        elif word.isdigit():
            out.append(ordinal(int(word)))
        else:
            out.append(word)
    name = " ".join(out)
    if re.fullmatch(r"((west|east|north|south) )?\d+(st|nd|rd|th)", name):
        name += " street"  # a bare "86th" or "West 86th" means the street
    if not (set(name.split()) & STREET_TYPES or name in TYPELESS_STREETS):
        return None
    return name


def street_pattern(name):
    """Anchored regex for a normalized street name, in the POSIX ERE subset Overpass accepts.

    Written-out avenue ordinals ("Fifth Avenue") and known synonyms also match. A numbered street
    typed without a direction matches West, East, or neither.
    """
    alternatives = []
    for option in filter(None, [name, SYNONYMS.get(name)]):
        parts = []
        for token in option.split():
            number = re.fullmatch(r"(\d+)(st|nd|rd|th)", token)
            if number and option.endswith("avenue") and int(number.group(1)) in ORDINAL_WORDS:
                parts.append(f"({token}|{ORDINAL_WORDS[int(number.group(1))]})")
            elif token in ("jr", "sr"):
                parts.append(token + r"\.?")
            else:
                parts.append(token)
        alternatives.append(" ".join(parts))
    pattern = alternatives[0] if len(alternatives) == 1 else "(" + "|".join(alternatives) + ")"
    if re.fullmatch(r"\d+(st|nd|rd|th) street", name):
        pattern = "((west|east|north|south) )?" + pattern
    return f"^{pattern}$"


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _display(name):
    return " ".join(word.capitalize() for word in name.split())


def _intersection(street_a, street_b):
    nodes = _shared_nodes(street_pattern(street_a), street_pattern(street_b))
    if not nodes:
        return None

    # Group nodes into distinct intersections; a divided avenue meets a street at several nodes.
    clusters = []
    for node in nodes:
        home = next((c for c in clusters if distance_m(node[1], node[2], c[0][1], c[0][2]) <= SAME_INTERSECTION_M), None)
        if home:
            home.append(node)
        else:
            clusters.append([node])
    centers = [(round(sum(n[1] for n in c) / len(c), 7), round(sum(n[2] for n in c) / len(c), 7)) for c in clusters]

    place = {
        "label": f"{_display(street_a)} & {_display(street_b)}",
        "lat": centers[0][0],
        "lng": centers[0][1],
        "match_type": "intersection",
        "provider": "openstreetmap-overpass",
        "provider_ref": f"osm:node/{clusters[0][0][0]}",
        "warnings": [],
    }
    if len(clusters) > 1:
        place["alternatives"] = [{"lat": lat, "lng": lng} for lat, lng in centers[1:]]
        place["warnings"].append(f"These streets meet at {len(clusters)} separate places; confirm the "
                                 "neighborhood or borough with the user before planning from here.")
    return place


@lru_cache(maxsize=256)
def _shared_nodes(pattern_a, pattern_b):
    """OSM nodes (id, lat, lng) where a way named like pattern_a meets one named like pattern_b."""
    south, west, north, east = NYC_BOUNDS
    bbox = f"({south},{west},{north},{east})"
    a, b = (p.replace("\\", "\\\\") for p in (pattern_a, pattern_b))  # escape for the QL string
    query = (f'[out:json][timeout:20];'
             f'way["highway"]["name"~"{a}",i]{bbox}->.a;'
             f'way["highway"]["name"~"{b}",i]{bbox}->.b;'
             f'node(w.a)(w.b);out;')
    error = None
    for url in OVERPASS_URLS:
        try:
            body = fetch_json("overpass", "POST", url, data={"data": query}, timeout=25)
        except UpstreamError as e:
            error = e  # rate limits and overload are common; try the mirror
            continue
        return tuple((el["id"], el["lat"], el["lon"]) for el in body.get("elements", []) if el.get("type") == "node")
    raise error


def _address(query):
    body = fetch_json("nyc-geosearch", "GET", GEOSEARCH_URL, params={"text": query, "size": 1})
    features = body.get("features") or []
    if not features:
        return None
    props = features[0]["properties"]
    lng, lat = features[0]["geometry"]["coordinates"]
    pad = (props.get("addendum") or {}).get("pad") or {}
    place = {
        "label": props.get("label"),
        "lat": lat,
        "lng": lng,
        "match_type": "address",
        "provider": "nyc-geosearch",
        "provider_ref": f"bbl:{pad['bbl']}" if pad.get("bbl") else None,
        "warnings": [],
    }
    if (props.get("confidence") or 0) < 0.8:
        place["warnings"].append(f"Closest address match was {props.get('label')!r} "
                                 f"(confidence {props.get('confidence')}); confirm it with the user.")
    return place


def _named_place(query, is_intersection=False):
    global _last_nominatim_call
    # Nominatim's usage policy allows at most one request per second.
    wait = 1.0 - (time.monotonic() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim_call = time.monotonic()

    south, west, north, east = NYC_BOUNDS
    hits = fetch_json("nominatim", "GET", NOMINATIM_URL, params={
        "q": query, "format": "jsonv2", "limit": 1, "bounded": 1,
        "viewbox": f"{west},{north},{east},{south}",
    })
    if not hits:
        return None
    hit = hits[0]
    lat, lng = float(hit["lat"]), float(hit["lon"])
    place = {
        "label": hit.get("name") or hit["display_name"].split(",")[0],
        "lat": lat,
        "lng": lng,
        "match_type": "place",
        "provider": "openstreetmap-nominatim",
        "provider_ref": f"osm:{hit['osm_type']}/{hit['osm_id']}",
        "warnings": [],
    }
    if is_intersection:
        place["warnings"].append(f"Matched {place['label']!r} by name because the street lookup found no "
                                 "shared corner; confirm it is the intended intersection.")
    s, n, w, e = (float(v) for v in hit.get("boundingbox", [lat, lat, lng, lng]))
    if distance_m(s, w, n, e) > LARGE_FEATURE_M:
        place["warnings"].append(f"{place['label']} is a large area and this point is its center; ask "
                                 "which entrance or side the user means before giving directions.")
    return place
