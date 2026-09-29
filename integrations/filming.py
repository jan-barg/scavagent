"""Film permits near a street or ZIP code, with the data's coverage dates (find_filming_records).

Source: NYC Film Permits on NYC Open Data (tg4x-b46p), keyless. A record is a permit to hold
parking for a shoot on listed street segments. It does not prove filming happened, where cameras
stood, or that anything is on set now. When checked on 2026-09-28 the newest permit started on
2026-06-29, so the data gives historical filming context and cannot show a current set.
"""

import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from integrations.common import UpstreamError, fetch_json, upstream_failure, utc_now
from integrations.geocoding import normalize_street
from schemas import NYC_TIMEZONE, Freshness, tool_error, tool_ok

API = "https://data.cityofnewyork.us/resource/tg4x-b46p.json"
PAGE = "https://data.cityofnewyork.us/d/tg4x-b46p"
COVERAGE_SECONDS = 3600
DEFAULT_YEARS_BACK = 1
MAX_RECORDS = 10
CAVEAT = ("A permit holds parking for a shoot on these street segments. It does not show that filming happened, "
          "where the camera was, or that anything is on set now.")

_coverage = {"expires": 0.0, "value": None}


def find_filming_records(street=None, zip_code=None, date_from=None, date_to=None, category=None, limit=5):
    """Film permits that held parking on a street (or in a ZIP code) within a date range, newest first.

    street: a street name ("Columbus Avenue", "W 81st St"). zip_code: five digits. Dates are YYYY-MM-DD,
    default the year before the data's last record. category: e.g. "Television", "Film", "Commercial".
    """
    street_name = normalize_street(str(street)) if street else None
    zip_code = str(zip_code).strip() if zip_code else None
    if street and not street_name:
        return tool_error("INVALID_ARGUMENT", f"{street!r} does not look like a street name.", retryable=False,
                          next_step="Pass one street, e.g. 'Columbus Avenue' or 'West 81st Street'.")
    if zip_code and not re.fullmatch(r"\d{5}", zip_code):
        return tool_error("INVALID_ARGUMENT", "zip_code must be five digits.", retryable=False, next_step="Pass e.g. '10024'.")
    if not street_name and not zip_code:
        return tool_error("INVALID_ARGUMENT", "Give a street or a zip_code.", retryable=False,
                          next_step="Pass the street of the stop, e.g. 'Columbus Avenue'.")
    try:
        coverage = _coverage_dates()
    except UpstreamError as e:
        return upstream_failure(e, "Skip filming context for now.")
    try:
        end = date.fromisoformat(date_to) if date_to else coverage["latest_start"].date()
        start = date.fromisoformat(date_from) if date_from else end - timedelta(days=365 * DEFAULT_YEARS_BACK)
    except ValueError:
        return tool_error("INVALID_ARGUMENT", "Dates must look like 2026-06-01.", retryable=False,
                          next_step="Pass date_from and date_to as YYYY-MM-DD, or omit them.")
    last = coverage["latest_start"].date()
    if start > last:
        return tool_error("STALE_DATA", f"Permit records end with permits starting {last.isoformat()}; they cannot show "
                                        f"filming from {start.isoformat()} on.", retryable=False,
                          next_step="Offer filming history from earlier dates instead, and never promise a current set.")

    where = [f"startdatetime between '{start.isoformat()}T00:00:00' and '{end.isoformat()}T23:59:59'"]
    if zip_code:
        where.append(f"zipcode_s like '%{zip_code}%'")
    if street_name:
        words = _permit_words(street_name)
        where += [f"upper(parkingheld) like '%{word}%'" for word in words]
    if category:
        where.append(f"upper(category) = '{re.sub(r'[^A-Za-z &]', '', str(category)).upper()}'")
    try:
        rows = fetch_json("nyc-open-data", "GET", API, params={
            "$select": "eventid,eventtype,startdatetime,enddatetime,parkingheld,borough,category,subcategoryname,zipcode_s",
            "$where": " and ".join(where), "$order": "startdatetime DESC", "$limit": 200})
    except UpstreamError as e:
        return upstream_failure(e, "Skip filming context for now.")

    pattern = _permit_pattern(street_name) if street_name else None
    records = [_record(row) for row in rows if not pattern or pattern.search(row.get("parkingheld", ""))]
    limit = max(1, min(int(limit or 5), MAX_RECORDS))
    warnings = [CAVEAT]
    if end > last:
        warnings.append(f"The records stop with permits starting {last.isoformat()}; nothing after that is known.")
    return tool_ok({
        "records": records[:limit],
        "total_found": len(records),
        "coverage": {"latest_permit_start": last.isoformat(), "latest_entry": coverage["latest_entry"].isoformat(),
                     "source": PAGE},
    }, warnings=warnings, freshness=Freshness(kind="historical", as_of=coverage["latest_entry"], retrieved_at=utc_now()))


def _coverage_dates():
    if _coverage["expires"] < time.monotonic():
        row = fetch_json("nyc-open-data", "GET", API, params={
            "$select": "max(startdatetime) AS latest_start, max(enteredon) AS latest_entry"})[0]
        local = ZoneInfo(NYC_TIMEZONE)
        value = {key: datetime.fromisoformat(row[key]).replace(tzinfo=local) for key in ("latest_start", "latest_entry")}
        _coverage.update(expires=time.monotonic() + COVERAGE_SECONDS, value=value)
    return _coverage["value"]


def _permit_words(street_name):
    """The dataset writes 'WEST   81 STREET': upper case, ordinals as plain numbers, spacing that varies."""
    return [re.sub(r"(\d+)(st|nd|rd|th)$", r"\1", word).upper() for word in street_name.split()]


def _permit_pattern(street_name):
    words = _permit_words(street_name)
    return re.compile(r"\b" + r"\s+".join(map(re.escape, words)) + r"\b", re.IGNORECASE)


def _record(row):
    local = ZoneInfo(NYC_TIMEZONE)

    def moment(value):
        return datetime.fromisoformat(value).replace(tzinfo=local).isoformat() if value else None

    return {
        "event_id": row.get("eventid"),
        "type": row.get("eventtype"),
        "category": row.get("category"),
        "subcategory": row.get("subcategoryname"),
        "start": moment(row.get("startdatetime")),
        "end": moment(row.get("enddatetime")),
        "parking_held": re.sub(r"\s+", " ", row.get("parkingheld", "")).strip(),
        "zip_codes": [z.strip() for z in (row.get("zipcode_s") or "").split(",") if z.strip()],
        "borough": row.get("borough"),
    }


def reset_for_tests():
    _coverage.update(expires=0.0, value=None)
