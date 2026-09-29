"""Find candidate places near a point and gather sourced claims about one of them.

Returns candidate lists and PlaceEvidence-shaped records (docs/CONTRACTS.md).

Sources, all keyless:
- Wikipedia: articles with coordinates (geosearch, or text search limited to a radius), and
  revision-pinned article text, so each claim links to the exact version it was quoted from.
- NYC Landmarks Preservation Commission (LPC) open data: individual landmark sites (buis-pvji)
  and the landmark and historic-district building database (gpmc-yuvp), which records architect,
  style, and construction dates.

Claims are quoted or transcribed from the source with a URL; this module does not verify them.
Nothing here shows that a physical detail is visible today, so physical_features stays empty
until fieldwork or a current source supports one.
"""

import math
import re
from collections import defaultdict
from datetime import datetime

from integrations.common import UpstreamError, distance_m, failure, fetch_json, in_nyc, now_iso, success

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
ACCESS_UNKNOWN = {"status": "unknown", "note": "These sources do not establish current public access or opening hours."}

AREA_DESCRIPTION = re.compile(r"^(neighbou?rhood|borough|county|country|census)\b", re.IGNORECASE)
_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$", re.MULTILINE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")
_ABBREVIATION_END = re.compile(r"\b(?:[A-Z]|St|Ave|Jr|Sr|Mr|Mrs|Dr|Co|Inc|No|Mt|Ft|ca|c)\.$")


def find_places(lat, lon, radius_m=800, query=None, limit=15):
    """Real places near a point that could anchor an adventure stop, nearest first.

    Candidates come from Wikipedia articles with coordinates and from designated NYC landmarks.
    `query` narrows the Wikipedia results to articles mentioning it (e.g. "architecture", "jazz").
    Designated landmarks keep up to half the slots even when nearer, less documented places would
    fill the list. Distances are straight-line; use get_walking_times for travel time. A candidate is
    a lead for research_place, not evidence that it suits an activity or is open.
    """
    try:
        lat, lon, radius_m, limit = float(lat), float(lon), int(radius_m), int(limit)
    except (TypeError, ValueError):
        return failure("INVALID_ARGUMENT", "lat, lon, radius_m, and limit must be numbers.",
                       "Use coordinates returned by geocode_place.")
    if not in_nyc(lat, lon):
        return failure("OUTSIDE_COVERAGE", f"({lat}, {lon}) is outside New York City.",
                       "Search around a point within the five boroughs.")
    radius_m = max(100, min(radius_m, MAX_RADIUS_M))
    limit = max(1, min(limit, MAX_CANDIDATES))
    query = " ".join(str(query or "").split())[:80] or None

    errors = []
    try:
        articles = _article_candidates(lat, lon, radius_m, query)
    except UpstreamError as e:
        articles = []
        errors.append(str(e))
    try:
        landmarks, unlocated = _landmark_candidates(lat, lon, radius_m)
    except UpstreamError as e:
        landmarks, unlocated = [], []
        errors.append(str(e))
    if len(errors) == 2:
        return failure("UPSTREAM_UNAVAILABLE", "Place sources are unavailable: " + "; ".join(errors),
                       "Try again shortly.", retryable=True)

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
        return failure("NO_MATCH", f"No candidate places within {radius_m} m{matching}.",
                       "Widen radius_m (up to 2000) or drop the query.")
    return success({
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "query": query,
        "candidates": candidates,
        "retrieved_at": now_iso(),
    }, warnings)


def research_place(place_id, focus=None):
    """Sourced claims about one place from find_places, each with a citable source URL.

    place_id: "wiki:<pageid>" or "lpc:<LP number>". focus: an optional topic such as
    "architecture" or "history"; for Wikipedia places it adds claims from the matching section.
    """
    kind, _, key = str(place_id or "").partition(":")
    focus = " ".join(str(focus or "").split())[:60] or None
    try:
        if kind == "wiki" and key.isdigit():
            return _research_article(int(key), focus)
        if kind == "lpc" and re.fullmatch(r"LP-\d+[A-Z]?", key):
            return _research_landmark(key)
    except UpstreamError as e:
        return failure("UPSTREAM_UNAVAILABLE", str(e), "Try again shortly, or research another candidate.",
                       retryable=e.retryable)
    return failure("INVALID_ARGUMENT", f"Unknown place_id {place_id!r}.",
                   "Use a place_id returned by find_places, such as 'wiki:9238071' or 'lpc:LP-01521'.")


# --- Candidates ---


def _article_candidates(lat, lon, radius_m, query):
    if query:
        found = _wiki({"list": "search", "srsearch": f"nearcoord:{radius_m}m,{lat},{lon} {query}",
                       "srlimit": 30, "srprop": ""}).get("search", [])
    else:
        found = _wiki({"list": "geosearch", "gscoord": f"{lat}|{lon}", "gsradius": radius_m,
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
        d = distance_m(lat, lon, coords["lat"], coords["lon"])
        if d > radius_m:
            continue
        candidates.append({
            "place_id": f"wiki:{page['pageid']}",
            "name": page["title"],
            "description": page.get("description"),
            "lat": coords["lat"],
            "lon": coords["lon"],
            "distance_m": round(d),
            "address": None,
            "designation": None,
            "related_ids": [],
            "sources": [{"title": f"Wikipedia: {page['title']}", "url": page.get("fullurl")}],
        })
    return candidates


def _landmark_candidates(lat, lon, radius_m):
    """Designated individual landmarks within the radius, plus names skipped for having no usable point."""
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * math.cos(math.radians(lat)))
    rows = fetch_json("nyc-open-data", "GET", LPC_SITES_API, params={
        "$select": "lpc_name,lpc_lpnumb,address,desdate,landmarkty,url_report,latitude,longitude",
        "$where": (f"latitude between {lat - dlat} and {lat + dlat} "
                   f"and longitude between {lon - dlon} and {lon + dlon}"),
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
        row_lat, row_lon = float(row["latitude"]), float(row["longitude"])
        d = distance_m(lat, lon, row_lat, row_lon)
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
            "lat": row_lat,
            "lon": row_lon,
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
                        if distance_m(a["lat"], a["lon"], landmark["lat"], landmark["lon"]) <= SAME_PLACE_M
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
        return failure("NO_MATCH", f"Wikipedia has no page with id {pageid}.",
                       "Use a place_id from a fresh find_places call.")

    title = page["title"]
    revision = page["revisions"][0]
    permalink = f"https://en.wikipedia.org/w/index.php?oldid={revision['revid']}"
    extract = page.get("extract") or ""
    first_heading = _HEADING.search(extract)
    intro = extract[:first_heading.start()] if first_heading else extract

    claims, warnings = [], []

    def cite(sentences, section=None):
        for sentence in sentences:
            claims.append({
                "id": f"claim_{len(claims) + 1}",
                "text": sentence,
                "source_title": f"Wikipedia: {title}" + (f" (section: {section})" if section else ""),
                "source_url": permalink + (f"#{section.replace(' ', '_')}" if section else ""),
                "source_revised_at": revision["timestamp"],
                "source_kind": "encyclopedia",
            })

    cite(_sentences(intro)[:MAX_INTRO_CLAIMS])
    if focus:
        section, text = _section_about(extract, focus)
        if section:
            cite(_sentences(text)[:MAX_FOCUS_CLAIMS], section)
        else:
            warnings.append(f"The article has no section about {focus!r}; returned its introduction only.")

    coords = (page.get("coordinates") or [None])[0]
    lat, lon = (coords["lat"], coords["lon"]) if coords else (None, None)
    address = None
    if coords:
        try:
            building = _matching_building(lat, lon, title)
        except UpstreamError as e:
            building = None
            warnings.append(f"LPC building records were unavailable: {e}")
        if building:
            claims.append(_building_claim(building, len(claims) + 1))
            address = building.get("des_addres")
    if not claims:
        warnings.append("The article text yielded no quotable sentences.")

    return success(_evidence(f"wiki:{pageid}", title, page.get("description"), address, lat, lon, claims),
                   warnings)


def _research_landmark(lp):
    rows = fetch_json("nyc-open-data", "GET", LPC_SITES_API, params={
        "$select": "lpc_name,lpc_lpnumb,address,desdate,landmarkty,url_report,latitude,longitude,bbl",
        "$where": f"lpc_lpnumb = '{lp}'",
        "$limit": 20,
    })
    if not rows:
        return failure("NO_MATCH", f"No LPC landmark {lp}.", "Use a place_id from a fresh find_places call.")
    site = rows[0]
    designation = _designation(site)

    uncertainty = []
    lat, lon = _number(site.get("latitude")), _number(site.get("longitude"))
    buildings = []
    if site.get("bbl"):
        lot_landmarks = fetch_json("nyc-open-data", "GET", LPC_SITES_API, params={
            "$select": "lpc_lpnumb", "$where": f"bbl = '{site['bbl']}'", "$limit": 50})
        if len({r.get("lpc_lpnumb") for r in lot_landmarks}) > 1:
            lat = lon = None
            uncertainty.append("The recorded point is the center of a tax lot shared with other landmarks, so "
                               "it does not locate this structure; geocode its address instead.")
        buildings = fetch_json("nyc-open-data", "GET", LPC_BUILDINGS_API, params={
            "$select": "des_addres,build_nme,arch_build,style_prim,date_combo,hist_dist,bbl",
            "$where": f"bbl = '{site['bbl']}'", "$limit": 3})

    claims = [{
        "id": "claim_1",
        "text": (f"{site.get('lpc_name')} ({site.get('address')}) was designated a New York City "
                 f"{(designation['type'] or 'landmark').lower()} on {designation['designated_on']}."),
        "source_title": "NYC LPC: Individual Landmark Sites (NYC Open Data)",
        "source_url": LPC_SITES_PAGE,
        "source_revised_at": None,
        "source_kind": "official_record",
    }]
    for building in buildings:
        claims.append(_building_claim(building, len(claims) + 1))

    evidence = _evidence(f"lpc:{lp}", site.get("lpc_name"), "Designated New York City landmark",
                         site.get("address"), lat, lon, claims, uncertainty)
    evidence["designation"] = designation
    return success(evidence)


def _matching_building(lat, lon, name):
    """The LPC building record near this point whose name or address matches `name`, if exactly one does."""
    rows = fetch_json("nyc-open-data", "GET", LPC_BUILDINGS_API, params={
        "$select": "des_addres,build_nme,arch_build,style_prim,date_combo,hist_dist,bbl",
        "$where": f"within_circle(the_geom, {lat}, {lon}, {BUILDING_SEARCH_M})",
        "$limit": 100,
    })
    key = _name_key(name)
    matches = {}
    for row in rows:
        named = _blank(row.get("build_nme"))
        if (named and _same_name(named, name)) or (len(key) >= 6 and key in _name_key(row.get("des_addres") or "")):
            matches.setdefault(row.get("bbl"), row)
    return next(iter(matches.values())) if len(matches) == 1 else None


def _building_claim(row, number):
    name, address = _blank(row.get("build_nme")), row.get("des_addres")
    details = [f"architect/builder {_blank(row.get('arch_build')) or 'not recorded'}",
               f"primary style {_blank(row.get('style_prim')) or 'not recorded'}",
               f"date {_blank(row.get('date_combo')) or 'not recorded'}"]
    if _blank(row.get("hist_dist")):
        details.append(f"historic district {row['hist_dist']}")
    return {
        "id": f"claim_{number}",
        "text": f"The LPC building database lists {address}" + (f" ({name})" if name else "") + ": " + "; ".join(details) + ".",
        "source_title": "NYC LPC: Individual Landmark and Historic District Building Database (NYC Open Data)",
        "source_url": LPC_BUILDINGS_PAGE,
        "source_revised_at": None,
        "source_kind": "official_record",
    }


def _evidence(place_id, name, description, address, lat, lon, claims, uncertainty=()):
    return {
        "place_id": place_id,
        "name": name,
        "description": description,
        "address": address,
        "lat": lat,
        "lon": lon,
        "claims": claims,
        "physical_features": [],
        "access": dict(ACCESS_UNKNOWN),
        "checked_at": now_iso(),
        "uncertainty": [*uncertainty, CLAIMS_NOTE, FEATURES_NOTE],
    }


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
