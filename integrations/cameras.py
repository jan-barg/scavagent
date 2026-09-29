"""NYC DOT stills and Scavagent's pedestrian checkpoint selector.

Integration: extend TOOLS with CAMERA_TOOLS. Bind the capture handler per request
with functools.partial(capture_camera_checkpoint, save_asset=session_save_asset).
Never let the model supply save_asset, checkpoint records, URLs, or a session ID.
Storage owns persistence, media authorization, and replay protection; call capture
only after the user types readiness. The finder probes but does not save images.

Only enabled, field-verified records are usable in production. Set the server-side
SCAVAGENT_DEV_CAMERA_FIXTURES=1 flag to include fixtures in FINDER results. Fixtures
are explicitly labeled and cannot be captured. No production standing positions
exist yet: data/camera_catalogue.json separates pending candidates from validated
CameraCheckpoint records because stand_location cannot currently be null.

Distances are geometric estimates using pedestrian positions, never camera mounts.
Corridor detour is an out-and-back estimate from the nearest polyline point, not a
street route; the planner must check crossings, access, walking time and framing.
See data/camera_catalogue.json for the dated live API observation.
"""

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

import requests
from pydantic import ValidationError

from schemas import CameraCheckpoint, Freshness, LatLng, PhotoAsset, tool_error, tool_ok

CATALOGUE_URL = "https://webcams.nyctmc.org/api/cameras"
CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "data" / "camera_catalogue.json"
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "cameras.json"
MAX_CATALOGUE_BYTES = 4_000_000
MAX_IMAGE_BYTES = 8_000_000
MAX_CATALOGUE_RECORDS = 5000
MAX_LOCAL_CHECKPOINTS = 500
MAX_PROBES = 5
TIMEOUT = (3, 8)  # connect/read seconds; streaming also has a wall-clock deadline
FETCH_DEADLINE = 15
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
SaveAsset = Callable[..., PhotoAsset]


class CameraUnavailable(Exception):
    """A bounded, safe explanation, never upstream text or a credential-bearing URL."""


def _now():
    return datetime.now(timezone.utc)


class DOTCameraClient:
    """Fetch only the fixed catalogue and known IDs on its fixed HTTPS host."""

    def _get(self, url: str, max_bytes: int, content_types: set[str]):
        started = time.monotonic()
        try:
            with requests.get(
                url, timeout=TIMEOUT, allow_redirects=False, stream=True,
                headers={"Accept": ", ".join(sorted(content_types))},
            ) as response:
                if response.status_code != 200:
                    raise CameraUnavailable("DOT did not return HTTP 200; redirects are not followed.")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in content_types:
                    raise CameraUnavailable("DOT returned an unsupported content type.")
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > max_bytes):
                    raise CameraUnavailable("DOT response exceeded the size limit or had an invalid length.")
                data = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() - started > FETCH_DEADLINE:
                        raise CameraUnavailable("DOT response exceeded the time limit.")
                    if len(data) + len(chunk) > max_bytes:
                        raise CameraUnavailable("DOT response exceeded the size limit.")
                    data.extend(chunk)
                if not data:
                    raise CameraUnavailable("DOT returned an empty response.")
                return bytes(data), content_type, _now()
        except requests.RequestException:
            raise CameraUnavailable("DOT could not be reached within the request limits.") from None

    def catalogue(self):
        data, _, retrieved_at = self._get(CATALOGUE_URL, MAX_CATALOGUE_BYTES, {"application/json"})
        try:
            rows = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise CameraUnavailable("DOT returned invalid catalogue JSON.") from None
        if not isinstance(rows, list) or not rows or len(rows) > MAX_CATALOGUE_RECORDS:
            raise CameraUnavailable("DOT catalogue was empty, oversized, or had an unexpected shape.")
        cameras = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            camera_id = row.get("id")
            if not isinstance(camera_id, str) or not ID_PATTERN.fullmatch(camera_id):
                continue
            if camera_id in cameras:
                raise CameraUnavailable("DOT catalogue contained duplicate camera IDs.")
            online = row.get("isOnline")
            # bool('false') is True. Only an explicit boolean/string true qualifies.
            cameras[camera_id] = {
                "id": camera_id,
                "is_online": online is True or (isinstance(online, str) and online.lower() == "true"),
            }
            # Deliberately ignore imageUrl and mounting latitude/longitude.
        if not cameras:
            raise CameraUnavailable("DOT catalogue contained no usable camera IDs.")
        return cameras, retrieved_at

    def still(self, camera_id: str, catalogue: dict):
        if not isinstance(camera_id, str) or not ID_PATTERN.fullmatch(camera_id) or camera_id not in catalogue:
            raise CameraUnavailable("Camera is absent from the current DOT catalogue.")
        if not catalogue[camera_id]["is_online"]:
            raise CameraUnavailable("DOT reports this camera offline or its status is unknown.")
        source_url = f"{CATALOGUE_URL}/{camera_id}/image"
        data, content_type, retrieved_at = self._get(
            source_url, MAX_IMAGE_BYTES, {"image/jpeg", "image/png", "image/webp"},
        )
        signatures = {
            "image/jpeg": len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"),
            "image/png": data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xaeB`\x82"),
            "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and int.from_bytes(data[4:8], "little") + 8 == len(data),
        }
        if not signatures[content_type]:
            raise CameraUnavailable("DOT did not return recognizable image bytes matching its content type.")
        # HTTP Date/Last-Modified describe a response/resource, not established frame time.
        return data, content_type, source_url, retrieved_at


def _dev_enabled(value):
    return value is True if value is not None else os.environ.get("SCAVAGENT_DEV_CAMERA_FIXTURES") == "1"


def _load_file(path):
    if path.stat().st_size > 1_000_000:
        raise ValueError("Checkpoint file is too large")
    payload = json.loads(path.read_text())
    rows = payload["checkpoints"]
    if not isinstance(rows, list) or len(rows) > MAX_LOCAL_CHECKPOINTS:
        raise ValueError("Invalid checkpoint list")
    return rows


def _checkpoints(provided, allow_synthetic):
    rows = provided
    if rows is None:
        rows = _load_file(CATALOGUE_PATH)
        if allow_synthetic:
            rows += _load_file(FIXTURE_PATH)
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_LOCAL_CHECKPOINTS:
        raise ValueError("Invalid checkpoint list")
    records = [CameraCheckpoint.model_validate(row) for row in rows]
    if len({c.checkpoint_id for c in records}) != len(records):
        raise ValueError("Duplicate checkpoint IDs")
    # Bound free text in model context, including future fieldwork records.
    for record in records:
        if len(record.model_dump_json()) > 6000:
            raise ValueError("Oversized checkpoint record")
    return records


def load_checkpoints(allow_synthetic=False):
    """Every validated catalogue record, plus the labeled fixtures when allow_synthetic is True.

    Public read access for the planner and scripts. Disabled and unverified records are included;
    callers decide eligibility. A missing or invalid catalogue raises (OSError, ValueError, TypeError,
    KeyError) instead of reading as empty.
    """
    return _checkpoints(None, allow_synthetic is True)


def _eligible(checkpoint, allow_synthetic):
    return checkpoint.enabled and (
        checkpoint.verification_status == "field_verified"
        or (allow_synthetic and checkpoint.verification_status == "synthetic_fixture")
    )


def _distance(a: LatLng, b: LatLng):
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b.lng - a.lng) / 2) ** 2
    return 6_371_000 * 2 * math.asin(min(1, math.sqrt(h)))


def _corridor_distance(point, corridor):
    # Local equirectangular projection at the participant position; appropriate for
    # bounded NYC corridors. Degenerate segments are ordinary point distances.
    scale = 6_371_000 * math.pi / 180
    def xy(p):
        return ((p.lng - point.lng) * scale * math.cos(math.radians(point.lat)), (p.lat - point.lat) * scale)
    best = math.inf
    for a, b in zip(corridor, corridor[1:]):
        ax, ay = xy(a)
        bx, by = xy(b)
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = max(0, min(1, -(ax * dx + ay * dy) / denom)) if denom else 0
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def _failure(code, message, next_step, retryable=False):
    return tool_error(code, message, retryable=retryable, next_step=next_step)


def find_camera_checkpoints(
    point=None, corridor=None, max_distance_m=800, max_detour_m=1600, limit=3,
    *, checkpoints=None, client=None, allow_synthetic=None,
):
    """Rank enabled pedestrian views near a point OR a polyline; return at most five.

    `checkpoints`, `client`, and `allow_synthetic` are server/test injection only.
    At most five real images are probed, even when every feed is unavailable.
    """
    try:
        if (point is None) == (corridor is None):
            raise ValueError("Supply one geometry")
        if type(limit) is not int or not 1 <= limit <= MAX_PROBES:
            raise ValueError("Bad limit")
        for value, maximum in ((max_distance_m, 5000), (max_detour_m, 10000)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError("Bad distance")
        origin = LatLng.model_validate(point) if point is not None else None
        route = None
        if corridor is not None:
            if not isinstance(corridor, list) or not 2 <= len(corridor) <= 50:
                raise ValueError("Bad corridor")
            route = [LatLng.model_validate(p) for p in corridor]
            if sum(_distance(a, b) for a, b in zip(route, route[1:])) > 50_000:
                raise ValueError("Corridor is not local")
    except (ValidationError, ValueError, TypeError):
        return _failure("INVALID_ARGUMENT", "Supply a point or a local corridor (2–50 coordinates, at most 50 km), distances within 0–5000/10000 m, and a limit of 1–5.", "Correct the geometry or distance limits and try again.")
    dev = _dev_enabled(allow_synthetic)
    try:
        records = _checkpoints(checkpoints, dev)
    except (OSError, ValueError, TypeError, KeyError):
        return _failure("INTERNAL_ERROR", "The local camera checkpoint catalogue is invalid or unavailable.", "Continue with a non-camera activity; ask the maintainer to validate the catalogue.")
    ranked = []
    for checkpoint in records:
        if not _eligible(checkpoint, dev):
            continue
        distance = _distance(origin, checkpoint.stand_location) if origin else _corridor_distance(checkpoint.stand_location, route)
        detour = 2 * distance
        if distance <= max_distance_m and detour <= max_detour_m:
            ranked.append((distance, checkpoint.checkpoint_id, checkpoint))
    ranked.sort(key=lambda item: (item[0], item[1]))
    if not ranked:
        return _failure("NO_MATCH", "No enabled, verified pedestrian camera positions fit these limits.", "Offer a non-camera activity. Unverified candidates need field calibration before use.")
    selected = ranked[:MAX_PROBES]
    client = client or DOTCameraClient()
    cameras, catalogue_at = {}, None
    if any(c.verification_status != "synthetic_fixture" for _, _, c in selected):
        try:
            cameras, catalogue_at = client.catalogue()
        except CameraUnavailable as error:
            return _failure("UPSTREAM_UNAVAILABLE", str(error), "Offer a non-camera activity, or retry the camera search once later.", True)
    matches, unavailable = [], 0
    for distance, _, checkpoint in selected:
        fixture = checkpoint.verification_status == "synthetic_fixture"
        checked_at = None
        if not fixture:
            try:
                _, _, _, checked_at = client.still(checkpoint.camera_id, cameras)
            except CameraUnavailable:
                unavailable += 1
                continue
        matches.append({
            "checkpoint": checkpoint.model_dump(mode="json"),
            "distance_m": round(distance, 1),
            "estimated_detour_m": round(2 * distance, 1),
            "distance_basis": "pedestrian stand position; geometric out-and-back, not street routing",
            "availability": "synthetic_fixture_not_checked" if fixture else "still_retrieved",
            "availability_checked_at": checked_at.isoformat() if checked_at else None,
            "fallback": "Skip the photo and continue with a non-camera observation or chat puzzle.",
        })
        if len(matches) == limit:
            break
    if not matches:
        return _failure("UPSTREAM_UNAVAILABLE", "None of the candidate feeds checked is currently usable.", "Offer a non-camera activity; retry later if the user still wants a photo.", True)
    warnings = ["Distances do not verify a walkable route. Check crossings, access, activity time, and contingency before planning.", "A retrieved still does not prove the calibrated view still matches or that the participant is visible."]
    synthetic = any(m["checkpoint"]["verification_status"] == "synthetic_fixture" for m in matches)
    if synthetic:
        warnings.append("DEVELOPMENT FIXTURE: synthetic positions are not real discoveries or verified views; capture is disabled for them.")
    if unavailable:
        warnings.append(f"Skipped {unavailable} unavailable candidate feed(s).")
    if len(ranked) > MAX_PROBES:
        warnings.append("Search is bounded to the five nearest eligible positions; farther feeds were not checked.")
    return tool_ok(
        {"checkpoints": matches, "catalogue_retrieved_at": catalogue_at.isoformat() if catalogue_at else None},
        warnings=warnings,
        freshness=Freshness(kind="synthetic_fixture" if synthetic else "live", retrieved_at=_now()),
    )


def _media_path(value):
    if not isinstance(value, str) or "\\" in value:
        return False
    url = urlsplit(value)
    path = unquote(unquote(url.path))
    return not url.scheme and not url.netloc and path.startswith("/media/") and "\\" not in path and all(p not in (".", "..") for p in path.split("/"))


def capture_camera_checkpoint(checkpoint_id, save_asset: SaveAsset | None = None, *, checkpoints=None, client=None):
    """Retrieve on readiness; save bytes exactly once through the session-bound callable.

    save_asset(data, content_type, camera_id, checkpoint_id, source_url, retrieved_at)
    is called with keyword arguments and must return the persisted PhotoAsset.
    The caller owns request-level idempotency and attaching the asset to state.
    """
    if not isinstance(checkpoint_id, str) or not 1 <= len(checkpoint_id) <= 128:
        return _failure("INVALID_ARGUMENT", "A checkpoint ID is required.", "Use a checkpoint ID returned by find_camera_checkpoints.")
    try:
        records = _checkpoints(checkpoints, False)
    except (OSError, ValueError, TypeError, KeyError):
        return _failure("INTERNAL_ERROR", "The local camera checkpoint catalogue is invalid or unavailable.", "Continue without a photo and ask the maintainer to check the catalogue.")
    checkpoint = next((c for c in records if c.checkpoint_id == checkpoint_id), None)
    if checkpoint is None:
        return _failure("NO_MATCH", "The checkpoint is not in the usable local catalogue.", "Find an enabled, verified checkpoint or continue without a photo.")
    if not _eligible(checkpoint, False):
        return _failure("MISSING_EVIDENCE", "This position is disabled, unverified, or a synthetic fixture; no photo was fetched.", "Continue with a non-camera activity; field calibration is required.")
    if not callable(save_asset):
        return _failure("INTERNAL_ERROR", "Session-bound photo storage has not been configured.", "Continue without capturing; bind save_asset in the server's session tool registry.")
    client = client or DOTCameraClient()
    try:
        cameras, _ = client.catalogue()
        data, content_type, source_url, retrieved_at = client.still(checkpoint.camera_id, cameras)
    except CameraUnavailable as error:
        return _failure("UPSTREAM_UNAVAILABLE", str(error), "Offer a skip or non-camera activity; retry only if the user asks.", True)
    try:
        asset = PhotoAsset.model_validate(save_asset(
            data=data, content_type=content_type, camera_id=checkpoint.camera_id,
            checkpoint_id=checkpoint.checkpoint_id, source_url=source_url, retrieved_at=retrieved_at,
        ))
        if not (
            asset.camera_id == checkpoint.camera_id and asset.checkpoint_id == checkpoint.checkpoint_id
            and asset.byte_size == len(data) and asset.content_type == content_type
            and str(asset.source_url) == source_url and asset.retrieved_at == retrieved_at
            and asset.frame_time is None and asset.visibility == "unconfirmed"
            and asset.provenance == "live_capture" and _media_path(asset.media_url)
        ):
            raise ValueError("Storage metadata does not match capture")
    except Exception:
        # Never leak storage errors, paths, session IDs or credentials. Do not retry
        # a possibly successful write here: the session store owns deduplication.
        return _failure("INTERNAL_ERROR", "Photo storage did not return a valid saved-asset record.", "Keep existing photos; check session storage before retrying this capture.")
    return tool_ok(
        {"photo": asset.model_dump(mode="json"), "positioning_instructions": checkpoint.positioning_instructions},
        warnings=["Frame time is unknown; retrieval time is not exposure time. Ask the user whether they are visible. Reuse this saved media URL in the finale."],
        freshness=Freshness(kind="live", retrieved_at=retrieved_at),
    )


_POINT_SCHEMA = {
    "type": "object", "properties": {"lat": {"type": "number", "minimum": -90, "maximum": 90}, "lng": {"type": "number", "minimum": -180, "maximum": 180}},
    "required": ["lat", "lng"], "additionalProperties": False,
}
CAMERA_TOOLS = [
    {"type": "function", "function": {
        "name": "find_camera_checkpoints",
        "description": "Find enabled, field-verified pedestrian camera positions near a point OR a route corridor. Filters by estimated detour and current still availability; returns standing instructions and fallback. Distances are geometric, not confirmed walking routes. No photo is saved.",
        "parameters": {"type": "object", "properties": {
            "point": _POINT_SCHEMA,
            "corridor": {"type": "array", "items": _POINT_SCHEMA, "minItems": 2, "maxItems": 50, "description": "Ordered local route polyline, at most 50 km. Omit point when supplied."},
            "max_distance_m": {"type": "number", "minimum": 0, "maximum": 5000, "default": 800},
            "max_detour_m": {"type": "number", "minimum": 0, "maximum": 10000, "default": 1600},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "capture_camera_checkpoint",
        "description": "After the user types readiness, fetch a known enabled, field-verified checkpoint's DOT still and save it to the current session. Does not operate a shutter or establish visibility. On failure offer a skip. Never invent a checkpoint ID.",
        "parameters": {"type": "object", "properties": {"checkpoint_id": {"type": "string", "minLength": 1, "maxLength": 128}}, "required": ["checkpoint_id"], "additionalProperties": False},
    }},
]
CAMERA_TOOL_MAP = {"find_camera_checkpoints": find_camera_checkpoints, "capture_camera_checkpoint": capture_camera_checkpoint}
