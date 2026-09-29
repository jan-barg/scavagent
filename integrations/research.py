"""Find candidate places near a point and gather sourced claims about one of them.

Returns candidate lists and schemas.PlaceEvidence records.

Sources, all keyless:
- Wikipedia: articles with coordinates (geosearch, or text search limited to a radius), and
  revision-pinned article text, so each claim links to the exact version it was quoted from.
- NYC Landmarks Preservation Commission (LPC) open data: individual landmark sites (buis-pvji)
  and the landmark and historic-district building database (gpmc-yuvp), which records architect,
  style, and construction dates.

Claims are quoted or transcribed from the source with a URL; this module does not verify them.
Nothing here shows that a physical detail is visible today, so no claim has kind
"physical_feature" until fieldwork or a current source supports one.
"""

import hashlib
import math
import re
from collections import defaultdict
from datetime import datetime

from integrations.common import (
    UpstreamError,
    distance_m,
    fetch_json,
    in_nyc,
    read_point,
    reference,
    upstream_failure,
    utc_now,
)
from schemas import Claim, LatLng, PlaceEvidence, tool_error, tool_ok

WIKI_API = "https://en.wikipedia.org/w/api.php"
LPC_SITES_API = "https://data.cityofnewyork.us/resource/buis-pvji.json"
LPC_SITES_PAGE = "https://data.cityofnewyork.us/d/buis-pvji"
LPC_BUILDINGS_API = "https://data.cityofnewyork.us/resource/gpmc-yuvp.json"
LPC_BUILDINGS_PAGE = "https://data.cityofnewyork.us/d/gpmc-yuvp"

MAX_RADIUS_M = 2000
MAX_CANDIDATES = 25
SAME_PLACE_M = 120  # a landmark record and an article this close, with matching names, are one place
BUILDING_SEARCH_M = 80  # article coordinates can sit a little off the building footprint
MAX_INTRO_CLAIMS = 4
MAX_FOCUS_CLAIMS = 8
NAME_NOISE = {"the", "apartments", "apartment", "building", "manhattan", "new", "york", "city"}

CLAIMS_NOTE = "Claims are quoted or transcribed from the cited source; they are not independently verified."
FEATURES_NOTE = ("No source here shows that a plaque, inscription, or facade detail is visible today. Use a "
                 "user-observation or chat-delivered activity unless fieldwork confirms a feature.")
ACCESS_UNKNOWN = "Unknown: these sources do not establish current public access or opening hours."

AREA_DESCRIPTION = re.compile(r"^(neighbou?rhood|borough|county|country|census)\b", re.IGNORECASE)
ARCHITECTURAL = re.compile(r"\b(architect\w*|design\w*|facade|façade|style|stor(?:y|ies)|towers?|Beaux-Arts|"
                           r"Art Deco|Renaissance|Gothic|Romanesque|Revival|cornice|limestone|brick|terra[- ]cotta)\b",
                           re.IGNORECASE)
_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$", re.MULTILINE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")
_ABBREVIATION_END = re.compile(r"\b(?:[A-Z]|St|Ave|Jr|Sr|Mr|Mrs|Dr|Co|Inc|No|Mt|Ft|ca|c)\.$")


def find_places(lat, lng=None, radius_m=800, query=None, limit=15, lon=None):
    """Real places near a point that could anchor an adventure stop, nearest first.

    Candidates come from Wikipedia articles with coordinates and from designated NYC landmarks.
    `query` narrows the Wikipedia results to articles mentioning it (e.g. "architecture", "jazz").
    Designated landmarks keep up to half the slots even when nearer, less documented places would
    fill the list. Distances are straight-line; use get_walking_times for travel time. A candidate is
    a lead for research_place, not evidence that it suits an activity or is open.
    """
    point = read_point({"lat": lat, "lng": lng if lng is not None else lon})
    try:
        radius_m, limit = int(radius_m), int(limit)
    except (TypeError, ValueError):
        point = None
    if point is None:
        return tool_error("INVALID_ARGUMENT", "lat, lng, radius_m, and limit must be numbers.", retryable=False,
                          next_step="Use coordinates returned by geocode_place.")
    lat, lng = point
    if not in_nyc(lat, lng):
        return tool_error("OUTSIDE_COVERAGE", f"({lat}, {lng}) is outside New York City.", retryable=False,
                          next_step="Search around a point within the five boroughs.")
    radius_m = max(100, min(radius_m, MAX_RADIUS_M))
    limit = max(1, min(limit, MAX_CANDIDATES))
    query = " ".join(str(query or "").split())[:80] or None

    errors = []
    try:
        articles = _article_candidates(lat, lng, radius_m, query)
    except UpstreamError as e:
        articles = []
        errors.append(str(e))
    try:
        landmarks, unlocated = _landmark_candidates(lat, lng, radius_m)
    except UpstreamError as e:
        landmarks, unlocated = [], []
        errors.append(str(e))
    if len(errors) == 2:
        return tool_error("UPSTREAM_UNAVAILABLE", "Place sources are unavailable: " + "; ".join(errors),
                          retryable=True, next_step="Try again shortly.")

    warnings = [f"Partial results; a source failed: {e}" for e in errors]
    if unlocated:
        warnings.append(f"Skipped {len(unlocated)} landmark(s) recorded only at a shared tax-lot center, which "
                        f"may be far from the structure: {', '.join(sorted(unlocated)[:5])}.")
    candidates = sorted(_merge(articles, landmarks), key=lambda c: c["distance_m"])
    # In a dense block the nearest dozen can all be congregations and schools. Designated landmarks
    # carry an official record, so they keep up to half the slots; the rest fill by distance.
    designated = [c for c in candidates if c["designation"]][: limit // 2]
    kept = {id(c) for c in designated}
    nearest = [c for c in candidates if id(c) not in kept][: limit - len(designated)]
    candidates = sorted(designated + nearest, key=lambda c: c["distance_m"])
    if not candidates:
        matching = f" matching {query!r}" if query else ""
        return tool_error("NO_MATCH", f"No candidate places within {radius_m} m{matching}.", retryable=False,
                          next_step="Widen radius_m (up to 2000) or drop the query.")
    return tool_ok({"center": {"lat": lat, "lng": lng}, "radius_m": radius_m, "query": query,
                    "candidates": candidates}, warnings=warnings, freshness=reference())


def research_place(place_id, focus=None):
    """Sourced claims about one place from find_places, as a PlaceEvidence record.

    place_id: "wiki:<pageid>" or "lpc:<LP number>". focus: an optional topic such as
    "architecture" or "history"; for Wikipedia places it adds claims from the matching section.
    Claim ids come from the claim text, so researching the same place again gives the same ids.
    """
    kind, _, key = str(place_id or "").partition(":")
    focus = " ".join(str(focus or "").split())[:60] or None
    try:
        if kind == "wiki" and key.isdigit():
            return _research_article(int(key), focus)
        if kind == "lpc" and re.fullmatch(r"LP-\d+[A-Z]?", key):
            return _research_landmark(key)
    except UpstreamError as e:
        return upstream_failure(e, "Try again shortly, or research another candidate.")
    return tool_error("INVALID_ARGUMENT", f"Unknown place_id {place_id!r}.", retryable=False,
                      next_step="Use a place_id returned by find_places, such as 'wiki:9238071' or 'lpc:LP-01521'.")


# --- Candidates ---


def _article_candidates(lat, lng, radius_m, query):
    if query:
        found = _wiki({"list": "search", "srsearch": f"nearcoord:{radius_m}m,{lat},{lng} {query}",
                       "srlimit": 30, "srprop": ""}).get("search", [])
    else:
        found = _wiki({"list": "geosearch", "gscoord": f"{lat}|{lng}", "gsradius": radius_m,
                       "gslimit": 50}).get("geosearch", [])
    if not found:
        return []
    # colimit defaults to 10 coordinates per request, which silently drops most of a 50-page batch.
    pages = _wiki({"prop": "description|coordinates|info", "inprop": "url", "colimit": "max",
                   "pageids": "|".join(str(p["pageid"]) for p in found)}).get("pages", [])

    candidates = []
    for page in pages:
        coords = (page.get("coordinates") or [None])[0]
        if not coords or AREA_DESCRIPTION.match(page.get("description") or ""):
            continue  # a neighborhood's article point is not somewhere to walk to
        d = distance_m(lat, lng, coords["lat"], coords["lon"])
        if d > radius_m:
            continue
        candidates.append({
            "place_id": f"wiki:{page['pageid']}",
            "name": page["title"],
            "description": page.get("description"),
            "point": {"lat": coords["lat"], "lng": coords["lon"]},
            "distance_m": round(d),
            "address": None,
            "designation": None,
            "related_ids": [],
            "sources": [{"title": f"Wikipedia: {page['title']}", "url": page.get("fullurl")}],
        })
    return candidates


def _landmark_candidates(lat, lng, radius_m):
    """Designated individual landmarks within the radius, plus names skipped for having no usable point."""
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * math.cos(math.radians(lat)))
    rows = fetch_json("nyc-open-data", "GET", LPC_SITES_API, params={
        "$select": "lpc_name,lpc_lpnumb,address,desdate,landmarkty,url_report,latitude,longitude",
        "$where": (f"latitude between {lat - dlat} and {lat + dlat} "
                   f"and longitude between {lng - dlng} and {lng + dlng}"),
        "$limit": 200,
    })

    # Each landmark is recorded at its tax lot's center. When several landmarks share one lot (Central
    # Park holds both the Met and the Arsenal, many blocks apart), that point does not locate any of them.
    landmarks_at = defaultdict(set)
    for row in rows:
        landmarks_at[(row.get("latitude"), row.get("longitude"))].add(row.get("lpc_lpnumb"))

    candidates, unlocated = {}, set()
    for row in rows:
        lp = row.get("lpc_lpnumb")
        if not lp or not row.get("latitude") or not row.get("longitude"):
            continue
        row_lat, row_lng = float(row["latitude"]), float(row["longitude"])
        d = distance_m(lat, lng, row_lat, row_lng)
        if d > radius_m:
            continue
        if len(landmarks_at[(row["latitude"], row["longitude"])]) > 1:
            unlocated.add(row.get("lpc_name") or lp)
            continue
        if lp in candidates and candidates[lp]["distance_m"] <= d:
            continue  # a landmark spanning several lots appears once, at its nearest lot
        candidates[lp] = {
            "place_id": f"lpc:{lp}",
            "name": row.get("lpc_name"),
            "description": "Designated New York City landmark",
            "point": {"lat": row_lat, "lng": row_lng},
            "distance_m": round(d),
            "address": row.get("address"),
            "designation": _designation(row),
            "related_ids": [],
            "sources": _landmark_sources(row),
        }
    return list(candidates.values()), unlocated


def _merge(articles, landmarks):
    """Fold each landmark record into the Wikipedia article about the same place, when there is one."""
    merged = list(articles)
    for landmark in landmarks:
        article = next((a for a in articles
                        if distance_m(a["point"]["lat"], a["point"]["lng"],
                                      landmark["point"]["lat"], landmark["point"]["lng"]) <= SAME_PLACE_M
                        and _same_name(a["name"], landmark["name"])), None)
        if article:
            article["related_ids"].append(landmark["place_id"])
            article["designation"] = landmark["designation"]
            article["address"] = article["address"] or landmark["address"]
            article["sources"] += landmark["sources"]
        else:
            merged.append(landmark)
    return merged


# --- Research ---


def _research_article(pageid, focus):
    pages = _wiki({"prop": "extracts|revisions|coordinates|description|info", "pageids": pageid,
                   "explaintext": 1, "exsectionformat": "wiki", "rvprop": "ids|timestamp",
                   "inprop": "url"}).get("pages", [])
    page = pages[0] if pages else {}
    if not page or page.get("missing") or page.get("invalid") or not page.get("revisions"):
        return tool_error("NO_MATCH", f"Wikipedia has no page with id {pageid}.", retryable=False,
                          next_step="Use a place_id from a fresh find_places call.")

    place_id, title = f"wiki:{pageid}", page["title"]
    revision = page["revisions"][0]
    permalink = f"https://en.wikipedia.org/w/index.php?oldid={revision['revid']}"
    note = f"Quoted from Wikipedia revision {revision['revid']} (edited {revision['timestamp'][:10]})."
    extract = page.get("extract") or ""
    first_heading = _HEADING.search(extract)
    intro = extract[:first_heading.start()] if first_heading else extract
    checked_at = utc_now()

    claims, warnings = [], []
    for sentence in _sentences(intro)[:MAX_INTRO_CLAIMS]:
        claims.append(_claim(place_id, sentence, _kind(sentence), permalink, checked_at, note))
    if focus:
        section, text = _section_about(extract, focus)
        if section:
            url = f"{permalink}#{section.replace(' ', '_')}"
            for sentence in _sentences(text)[:MAX_FOCUS_CLAIMS]:
                claims.append(_claim(place_id, sentence, _kind(sentence, section), url, checked_at, note))
        else:
            warnings.append(f"The article has no section about {focus!r}; returned its introduction only.")

    coords = (page.get("coordinates") or [None])[0]
    point = LatLng(lat=coords["lat"], lng=coords["lon"]) if coords else None
    address = None
    if point:
        try:
            building = _matching_building(point.lat, point.lng, title)
        except UpstreamError as e:
            building = None
            warnings.append(f"LPC building records were unavailable: {e}")
        if building:
            claims.append(_building_claim(place_id, building, checked_at))
            address = building.get("des_addres")
    if not claims:
        warnings.append("The article text yielded no quotable sentences.")

    evidence = _evidence(place_id, title, address, point, claims, checked_at)
    return tool_ok({"evidence": evidence, "description": page.get("description")},
                   warnings=warnings, freshness=reference())


def _research_landmark(lp):
    rows = fetch_json("nyc-open-data", "GET", LPC_SITES_API, params={
        "$select": "lpc_name,lpc_lpnumb,address,desdate,landmarkty,url_report,latitude,longitude,bbl",
        "$where": f"lpc_lpnumb = '{lp}'",
        "$limit": 20,
    })
    if not rows:
        return tool_error("NO_MATCH", f"No LPC landmark {lp}.", retryable=False,
                          next_step="Use a place_id from a fresh find_places call.")
    site = rows[0]
    designation = _designation(site)
    place_id, checked_at = f"lpc:{lp}", utc_now()

    notes = []
    lat, lng = _number(site.get("latitude")), _number(site.get("longitude"))
    buildings = []
    if site.get("bbl"):
        lot_landmarks = fetch_json("nyc-open-data", "GET", LPC_SITES_API, params={
            "$select": "lpc_lpnumb", "$where": f"bbl = '{site['bbl']}'", "$limit": 50})
        if len({r.get("lpc_lpnumb") for r in lot_landmarks}) > 1:
            lat = lng = None
            notes.append("The recorded point is the center of a tax lot shared with other landmarks, so it does "
                         "not locate this structure; geocode its address instead.")
        buildings = fetch_json("nyc-open-data", "GET", LPC_BUILDINGS_API, params={
            "$select": "des_addres,build_nme,arch_build,style_prim,date_combo,hist_dist,bbl",
            "$where": f"bbl = '{site['bbl']}'", "$limit": 3})

    kind = (designation["type"] or "landmark").lower()
    claims = [_claim(place_id,
                     f"{site.get('lpc_name')} ({site.get('address')}) was designated a New York City {kind} "
                     f"on {designation['designated_on']}.",
                     "historical", designation["report_url"] or LPC_SITES_PAGE, checked_at,
                     "Transcribed from the LPC Individual Landmark Sites record.")]
    claims += [_building_claim(place_id, building, checked_at) for building in buildings]

    point = LatLng(lat=lat, lng=lng) if lat is not None and lng is not None else None
    evidence = _evidence(place_id, site.get("lpc_name"), site.get("address"), point, claims, checked_at, notes)
    return tool_ok({"evidence": evidence, "description": "Designated New York City landmark",
                    "designation": designation}, freshness=reference())


def _matching_building(lat, lng, name):
    """The LPC building record near this point whose name or address matches `name`, if exactly one does."""
    rows = fetch_json("nyc-open-data", "GET", LPC_BUILDINGS_API, params={
        "$select": "des_addres,build_nme,arch_build,style_prim,date_combo,hist_dist,bbl",
        "$where": f"within_circle(the_geom, {lat}, {lng}, {BUILDING_SEARCH_M})",
        "$limit": 100,
    })
    key = _name_key(name)
    matches = {}
    for row in rows:
        named = _blank(row.get("build_nme"))
        if (named and _same_name(named, name)) or (len(key) >= 6 and key in _name_key(row.get("des_addres") or "")):
            matches.setdefault(row.get("bbl"), row)
    return next(iter(matches.values())) if len(matches) == 1 else None


def _building_claim(place_id, row, checked_at):
    name, address = _blank(row.get("build_nme")), row.get("des_addres")
    details = [f"architect/builder {_blank(row.get('arch_build')) or 'not recorded'}",
               f"primary style {_blank(row.get('style_prim')) or 'not recorded'}",
               f"date {_blank(row.get('date_combo')) or 'not recorded'}"]
    if _blank(row.get("hist_dist")):
        details.append(f"historic district {row['hist_dist']}")
    text = f"The LPC building database lists {address}" + (f" ({name})" if name else "") + ": " + "; ".join(details) + "."
    return _claim(place_id, text, "architectural", LPC_BUILDINGS_PAGE, checked_at,
                  "Transcribed from the LPC Individual Landmark and Historic District Building Database.")


def _claim(place_id, text, kind, url, checked_at, note):
    digest = hashlib.sha1(text.encode()).hexdigest()[:8]
    return Claim(claim_id=f"{place_id}#{digest}", text=text, kind=kind, basis="source", source_urls=[url],
                 checked_at=checked_at, uncertainty=f"{note} {CLAIMS_NOTE}")


def _kind(sentence, section=None):
    return "architectural" if ARCHITECTURAL.search(section or "") or ARCHITECTURAL.search(sentence) else "historical"


def _evidence(place_id, name, address, point, claims, checked_at, notes=()):
    unique = list({claim.claim_id: claim for claim in claims}.values())
    return PlaceEvidence(
        place_id=place_id, name=name, address=address, point=point, claims=unique,
        access_notes=ACCESS_UNKNOWN, checked_at=checked_at, uncertainty=" ".join([*notes, FEATURES_NOTE]),
    ).model_dump(mode="json")


# --- Helpers ---


def _wiki(params):
    body = fetch_json("wikipedia", "GET", WIKI_API,
                      params={"action": "query", "format": "json", "formatversion": 2, **params})
    if "error" in body:
        raise UpstreamError("wikipedia", body["error"].get("info", "API error"), retryable=False)
    return body.get("query", {})


def _section_about(extract, focus):
    """(title, text) of the first section whose title mentions a focus word, including its subsections."""
    words = [w for w in re.findall(r"[a-z]+", focus.lower()) if len(w) > 2]
    headings = list(_HEADING.finditer(extract))
    for i, heading in enumerate(headings):
        if any(word in heading.group(2).lower() for word in words):
            level = len(heading.group(1))
            end = next((h.start() for h in headings[i + 1:] if len(h.group(1)) <= level), len(extract))
            return heading.group(2), _HEADING.sub("\n", extract[heading.end():end])
    return None, None


def _sentences(text):
    """Split text into sentences of at least 25 characters, keeping 'George B. McClellan' and 'c. 1900' whole."""
    sentences = []
    for paragraph in text.split("\n"):
        pieces = []
        for piece in _SENTENCE_BREAK.split(paragraph.strip()):
            if pieces and _ABBREVIATION_END.search(pieces[-1]):
                pieces[-1] += " " + piece
            else:
                pieces.append(piece)
        sentences += [p for p in pieces if len(p) >= 25]
    return sentences


def _name_words(name):
    name = re.sub(r"\(.*?\)", " ", name.lower())  # drop qualifiers like "(Manhattan)"
    name = name.replace("public school", "ps").replace("saint", "st")
    return [w for w in re.findall(r"[a-z0-9]+", name) if w not in NAME_NOISE]


def _name_key(name):
    """Comparable form of a place name: 'The El Dorado' and 'Eldorado Apartments' both become 'eldorado'."""
    return "".join(_name_words(name))


def _same_name(a, b):
    """Loose match for one place's names across sources ('Endicott Hotel' and 'Hotel Endicott')."""
    words_a, words_b = _name_words(a), _name_words(b)
    if not words_a or not words_b:
        return False
    if set(words_a) == set(words_b):
        return True
    short, long_ = sorted(("".join(words_a), "".join(words_b)), key=len)
    return short == long_ or (len(short) >= 6 and short in long_)


def _designation(row):
    return {
        "body": "NYC Landmarks Preservation Commission",
        "type": row.get("landmarkty"),
        "designated_on": _us_date(row.get("desdate")),
        "lp_number": row.get("lpc_lpnumb"),
        "report_url": row.get("url_report"),
    }


def _landmark_sources(row):
    sources = [{"title": "NYC LPC: Individual Landmark Sites (NYC Open Data)", "url": LPC_SITES_PAGE}]
    if row.get("url_report"):
        sources.append({"title": f"LPC designation report: {row.get('lpc_name')}", "url": row["url_report"]})
    return sources


def _us_date(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except (TypeError, ValueError):
        return value


def _blank(value):
    """The LPC building database writes '0' for an empty field."""
    return None if value in (None, "", "0") else value


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
