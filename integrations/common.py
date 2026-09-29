"""Shared plumbing for the external-data adapters: bounded HTTP calls, NYC bounds, and points.

Adapters return the shared result convention from schemas.py (tool_ok / tool_error with Freshness).
"""

import math
from datetime import datetime, timezone

import requests

from schemas import Freshness, tool_error

# Public APIs (Nominatim, Overpass, Wikipedia) require an identifying User-Agent.
USER_AGENT = "Scavagent/0.1 (Columbia class project; +https://github.com/jan-barg/scavagent)"
DEFAULT_TIMEOUT_S = 15

# A coarse box around the five boroughs; it also clips a little of New Jersey and Nassau County.
# It rejects points that are clearly outside the city. It does not define the adventure area.
NYC_BOUNDS = (40.49, -74.27, 40.92, -73.68)  # south, west, north, east

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


class UpstreamError(Exception):
    """A provider call failed. `status` and `body` are set when the provider answered with an error."""

    def __init__(self, provider, detail, retryable=True, status=None, body=None):
        super().__init__(f"{provider}: {detail}")
        self.provider = provider
        self.retryable = retryable
        self.status = status
        self.body = body


class NoRoute(Exception):
    """The router answered, but no route of the requested kind connects the points."""


def fetch_json(provider, method, url, timeout=DEFAULT_TIMEOUT_S, **kwargs):
    """Make one bounded HTTP request and return the decoded JSON.

    Every failure becomes UpstreamError, so callers can fall back to another provider or
    report UPSTREAM_UNAVAILABLE instead of crashing the tool loop.
    """
    try:
        r = _session.request(method, url, timeout=timeout, **kwargs)
    except requests.RequestException as e:
        raise UpstreamError(provider, f"request failed ({type(e).__name__})") from e

    try:
        body = r.json()
    except ValueError:
        body = None

    if r.status_code >= 400:
        # 429 and 5xx may work later; any other 4xx means this exact request will keep failing.
        retryable = r.status_code == 429 or r.status_code >= 500
        raise UpstreamError(provider, f"HTTP {r.status_code}", retryable, r.status_code, body)
    if body is None:
        raise UpstreamError(provider, "response was not JSON")
    return body


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def reference(as_of=None) -> Freshness:
    """Freshness for reference data (maps, encyclopedias, city records) fetched just now."""
    return Freshness(kind="static_reference", as_of=as_of, retrieved_at=utc_now())


def upstream_failure(error: UpstreamError, next_step: str) -> dict:
    return tool_error("UPSTREAM_UNAVAILABLE", str(error), retryable=error.retryable, next_step=next_step)


def in_nyc(lat, lng):
    south, west, north, east = NYC_BOUNDS
    return south <= lat <= north and west <= lng <= east


def read_point(value):
    """(lat, lng) from {"lat": .., "lng": ..}, or None. "lon" and "longitude" are accepted too,
    because models often use them."""
    if not isinstance(value, dict):
        return None
    try:
        lat = float(value["lat"] if "lat" in value else value["latitude"])
        lng = float(next(value[key] for key in ("lng", "lon", "longitude") if key in value))
    except (KeyError, StopIteration, TypeError, ValueError):
        return None
    return (lat, lng) if math.isfinite(lat) and math.isfinite(lng) else None


def distance_m(lat1, lng1, lat2, lng2):
    """Straight-line (haversine) distance in meters. For filtering and ranking, never travel time."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))
