"""Shared plumbing for the adapters: bounded HTTP calls and the tool-result envelope.

The envelope follows the proposal in docs/CONTRACTS.md:
    {"ok": bool, "data": ..., "error": {code, message, retryable, next_step} or None, "warnings": [...]}
When the shared schemas land, switch these helpers to the shared ones rather than keeping two conventions.
"""

import math
from datetime import datetime, timezone

import requests

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


def success(data, warnings=None):
    return {"ok": True, "data": data, "error": None, "warnings": warnings or []}


def failure(code, message, next_step, retryable=False):
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "retryable": retryable, "next_step": next_step},
        "warnings": [],
    }


def now_iso():
    """Retrieval time in UTC. Convert to America/New_York only where a time is shown to the user."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def in_nyc(lat, lon):
    south, west, north, east = NYC_BOUNDS
    return south <= lat <= north and west <= lon <= east


def distance_m(lat1, lon1, lat2, lon2):
    """Straight-line (haversine) distance in meters. For filtering and ranking, never travel time."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))
