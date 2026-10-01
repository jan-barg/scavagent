"""Mocked camera boundary checks; no network or actual photos/positions.

The field_verified records below are TEST inputs for exercising eligibility and
capture. They are not fieldwork evidence and are never written to the catalogue.
"""

import json
import re
from datetime import datetime, timezone

import pytest
import requests

from integrations import cameras as cam
from schemas import CameraCheckpoint, LatLng, PhotoAsset, ToolResult

STAMP = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
POINT = {"lat": 40.785, "lng": -73.97}
# Signature-only mocked response, not a photograph; never saved to the repository.
JPEG = b"\xff\xd8\xffMOCK_HTTP_IMAGE_BYTES\xff\xd9"


def checkpoint(checkpoint_id="test_cp", camera_id="test-camera", lat=40.785, lng=-73.97, **changes):
    values = dict(
        checkpoint_id=checkpoint_id, camera_id=camera_id,
        stand_location={"lat": lat, "lng": lng}, address="TEST location",
        landmark="TEST landmark", side_of_street="TEST side",
        positioning_instructions="TEST instructions, not for navigation.",
        verification_status="field_verified", last_field_verified_at=STAMP,
        enabled=True,
    )
    values.update(changes)
    return CameraCheckpoint(**values)


def row(camera_id="test-camera", online="true", **changes):
    return {"id": camera_id, "isOnline": online, "latitude": 0, "longitude": 0, "imageUrl": "https://untrusted.invalid/private", **changes}


class Response:
    def __init__(self, body=JPEG, content_type="image/jpeg", status=200, headers=None, chunks=None):
        self.body, self.status_code = body, status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.chunks = chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        yield from (self.chunks if self.chunks is not None else [self.body])


def catalogue(rows=None):
    return Response(json.dumps([row()] if rows is None else rows).encode(), "application/json; charset=utf-8")


def http(monkeypatch, *responses):
    remaining = list(responses)
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert remaining, "Unexpected HTTP request"
        response = remaining.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(cam.requests, "get", get)
    return calls


def result(value, code=None):
    ToolResult.model_validate(value)
    json.dumps(value, allow_nan=False)
    if code:
        assert value["ok"] is False
        assert value["error"]["code"] == code
        assert value["error"]["next_step"]
    else:
        assert value["ok"] is True
    return value


@pytest.fixture(autouse=True)
def no_accidental_network(monkeypatch):
    monkeypatch.delenv("SCAVAGENT_DEV_CAMERA_FIXTURES", raising=False)
    def denied(*args, **kwargs):
        raise AssertionError("Tests must mock every HTTP request")
    monkeypatch.setattr(cam.requests, "get", denied)


def test_catalogue_street_ordinals_are_spelled_right():
    # The page's sign and the directions show these fields; two spots once said "23st Street".
    def suffix(n):
        return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    wrong = [(record.checkpoint_id, found.group(0))
             for record in cam.load_checkpoints()
             for text in (record.address, record.landmark, record.side_of_street, record.positioning_instructions)
             for found in re.finditer(r"\b(\d+)(st|nd|rd|th)\b", text or "", re.IGNORECASE)
             if found.group(2).lower() != suffix(int(found.group(1)))]
    assert wrong == []


def test_committed_checkpoints_are_verified_and_backed_by_a_field_log():
    data = json.loads(cam.CATALOGUE_PATH.read_text())
    for candidate in data["candidates"]:  # candidates stay incomplete; calibrated positions are separate records
        assert candidate["verification_status"] == "unverified" and candidate["enabled"] is False
        assert candidate["stand_location"] is None
    records = cam.load_checkpoints()
    log = {entry["checkpoint_id"]: entry for entry in data.get("field_log", [])}
    mounts = {c["camera_id"]: c["camera_mount_location"] for c in data["candidates"]}
    excluded = {e["workbench_spot_id"] for e in data.get("excluded_workbench_spots", [])}
    for record in records:
        assert record.verification_status in ("field_verified", "image_verified") and record.enabled
        assert (record.last_image_verified_at if record.verification_status == "image_verified" else record.last_field_verified_at)
        entry = log[record.checkpoint_id]  # every entry names its evidence and who checked it
        assert entry["evidence_stills"] and all(len(e["sha256"]) == 64 for e in entry["evidence_stills"])
        assert entry["camera_id"] == record.camera_id
        assert {"image": "image_verified", "field": "field_verified"}[entry["method"]] == record.verification_status
        reviewer = entry["verified_by"]
        assert reviewer and "TEST" not in reviewer and not reviewer.lower().startswith(("claude", "workbench user none"))
        if record.verification_status == "image_verified":
            assert entry["workbench_spot_id"] not in excluded
        assert not record.checkpoint_id.startswith(("candidate_", "fixture_", "test_", "rehearsal"))
        mount = LatLng(**(entry.get("camera_mount") or mounts[record.camera_id]))
        assert 1 <= cam._distance(record.stand_location, mount) <= 250, record.checkpoint_id  # not the mount, not far off
    assert len({r.checkpoint_id for r in records}) == len(records)


def test_string_offline_unknown_and_online_normalization(monkeypatch):
    values = ["false", False, "unknown", None, 1, "true", True]
    http(monkeypatch, catalogue([row(f"camera-{i}", value) for i, value in enumerate(values)]))
    rows, stamp = cam.DOTCameraClient().catalogue()
    assert [c["is_online"] for c in rows.values()] == [False] * 5 + [True, True]
    assert stamp.tzinfo is not None


def test_nearest_standing_position_wins_not_hardware(monkeypatch):
    calls = http(monkeypatch, catalogue([row("far"), row("near")]), Response())
    records = [checkpoint("far", "far", lat=40.79), checkpoint("near", "near", lat=40.7851)]
    reply = result(cam.find_camera_checkpoints(point=POINT, checkpoints=records, limit=1))
    match = reply["data"]["checkpoints"][0]
    assert match["checkpoint"]["checkpoint_id"] == "near"
    assert 10 < match["distance_m"] < 12
    assert 20 < match["estimated_detour_m"] < 24
    assert match["checkpoint"]["positioning_instructions"].startswith("TEST")
    assert match["availability"] == "still_retrieved"
    assert calls[-1][0] == cam.CATALOGUE_URL + "/near/image"
    assert all(kwargs["allow_redirects"] is False and kwargs["timeout"] == cam.TIMEOUT for _, kwargs in calls)


def test_max_distance_filters_independently_of_default_detour(monkeypatch):
    # Both views fit the default 1600 m detour; only the near one is within 100 m.
    records = [checkpoint("near", "near", lat=40.7855), checkpoint("far", "far", lat=40.787)]
    calls = http(monkeypatch, catalogue([row("near"), row("far")]), Response(), Response())
    reply = result(cam.find_camera_checkpoints(point=POINT, checkpoints=records, max_distance_m=100))
    assert [match["checkpoint"]["checkpoint_id"] for match in reply["data"]["checkpoints"]] == ["near"]
    assert [url for url, _ in calls] == [cam.CATALOGUE_URL, cam.CATALOGUE_URL + "/near/image"]


def test_corridor_distance_and_round_trip_detour(monkeypatch):
    route = [{"lat": 40.784, "lng": -73.97}, {"lat": 40.786, "lng": -73.97}]
    records = [checkpoint(lng=-73.969)]
    result(cam.find_camera_checkpoints(corridor=route, checkpoints=records, max_detour_m=100), "NO_MATCH")
    http(monkeypatch, catalogue(), Response())
    match = result(cam.find_camera_checkpoints(corridor=route, checkpoints=records, max_detour_m=180))["data"]["checkpoints"][0]
    assert 80 < match["distance_m"] < 90
    assert 160 < match["estimated_detour_m"] < 180


@pytest.mark.parametrize("latitude", [40.783, 40.787], ids=["before-start", "after-end"])
def test_corridor_distance_uses_segment_end_for_positions_beyond_it(monkeypatch, latitude):
    route = [{"lat": 40.784, "lng": -73.97}, {"lat": 40.786, "lng": -73.97}]
    http(monkeypatch, catalogue(), Response())
    reply = result(cam.find_camera_checkpoints(corridor=route, checkpoints=[checkpoint(lat=latitude)]))
    match = reply["data"]["checkpoints"][0]
    # Each view is 0.001 degree (~111 m) beyond an endpoint, but lies on the
    # segment's infinite extension. An unclamped projection would report zero.
    assert 110 < match["distance_m"] < 112
    assert 221 < match["estimated_detour_m"] < 223


def test_degenerate_corridor_is_bounded_point_distance(monkeypatch):
    http(monkeypatch, catalogue(), Response())
    reply = result(cam.find_camera_checkpoints(corridor=[POINT, POINT], checkpoints=[checkpoint()]))
    assert reply["data"]["checkpoints"][0]["distance_m"] == 0


@pytest.mark.parametrize("kwargs", [
    {}, {"point": POINT, "corridor": [POINT, POINT]}, {"point": {"lat": 91, "lng": 0}},
    {"point": POINT, "limit": 0}, {"point": POINT, "limit": True}, {"point": POINT, "limit": 6},
    {"point": POINT, "max_distance_m": float("nan")}, {"point": POINT, "max_detour_m": -1},
    {"point": POINT, "max_distance_m": "800"}, {"corridor": [POINT]},
    {"corridor": [POINT] * 51}, {"corridor": [POINT, {"lat": 41.8, "lng": -73.97}]},
])
def test_bad_arguments_never_fetch(kwargs):
    result(cam.find_camera_checkpoints(checkpoints=[checkpoint()], **kwargs), "INVALID_ARGUMENT")


@pytest.mark.parametrize("changes", [{"enabled": False}, {"verification_status": "unverified"}, {"verification_status": "synthetic_fixture"}])
def test_ineligible_positions_never_fetch(changes):
    record = checkpoint(**changes)
    result(cam.find_camera_checkpoints(point=POINT, checkpoints=[record]), "NO_MATCH")
    result(cam.capture_camera_checkpoint(record.checkpoint_id, lambda **kwargs: None, checkpoints=[record]), "MISSING_EVIDENCE")


def test_dev_flag_is_explicit_and_synthetic_data_stays_labeled(monkeypatch):
    fixture = checkpoint(verification_status="synthetic_fixture")
    monkeypatch.setenv("SCAVAGENT_DEV_CAMERA_FIXTURES", "true")
    result(cam.find_camera_checkpoints(point=POINT, checkpoints=[fixture]), "NO_MATCH")
    monkeypatch.setenv("SCAVAGENT_DEV_CAMERA_FIXTURES", "1")
    reply = result(cam.find_camera_checkpoints(point=POINT, checkpoints=[fixture]))
    assert reply["freshness"]["kind"] == "synthetic_fixture"
    assert reply["data"]["checkpoints"][0]["availability"] == "synthetic_fixture_not_checked"
    assert any("DEVELOPMENT FIXTURE" in w for w in reply["warnings"])
    result(cam.capture_camera_checkpoint(fixture.checkpoint_id, lambda **kwargs: None, checkpoints=[fixture]), "MISSING_EVIDENCE")


def test_unknown_offline_and_broken_feeds_skip_to_working_feed(monkeypatch):
    records = [checkpoint("a", "missing"), checkpoint("b", "offline"), checkpoint("c", "broken"), checkpoint("d", "works")]
    calls = http(monkeypatch, catalogue([row("offline", "false"), row("broken"), row("works")]), Response(status=503), Response())
    reply = result(cam.find_camera_checkpoints(point=POINT, checkpoints=records))
    assert [m["checkpoint"]["checkpoint_id"] for m in reply["data"]["checkpoints"]] == ["d"]
    assert len(calls) == 3
    assert any("3 unavailable" in w for w in reply["warnings"])


def test_probe_count_is_bounded_even_if_all_feeds_fail(monkeypatch):
    records = [checkpoint(str(i), f"camera-{i}") for i in range(10)]
    calls = http(monkeypatch, catalogue([row(f"camera-{i}") for i in range(10)]), *[Response(status=503) for _ in range(5)])
    result(cam.find_camera_checkpoints(point=POINT, checkpoints=records), "UPSTREAM_UNAVAILABLE")
    assert len(calls) == 6


@pytest.mark.parametrize("response", [
    requests.Timeout("secret must never appear"), Response(status=302), Response(status=500),
    Response(b"not json", "application/json"), Response(b"{}", "application/json"),
    Response(b"[]", "application/json"), Response(b"{}", "text/html"),
    catalogue([row(), row()]),
    Response(b"", "application/json", headers={"Content-Length": "9000000"}),
])
def test_catalogue_failures_are_actionable_and_do_not_leak(monkeypatch, response):
    http(monkeypatch, response)
    reply = result(cam.find_camera_checkpoints(point=POINT, checkpoints=[checkpoint()]), "UPSTREAM_UNAVAILABLE")
    assert "secret" not in json.dumps(reply)


@pytest.mark.parametrize("response", [
    Response(status=302, headers={"Location": "https://evil.invalid"}), Response(status=404),
    Response(b"<svg></svg>", "image/svg+xml"), Response(b"<html>offline</html>", "image/jpeg"),
    Response(JPEG[:-2]), Response(b""), Response(headers={"Content-Length": "99999999"}),
    requests.ConnectionError("sensitive internal diagnostic"),
])
def test_image_failure_does_not_call_storage(monkeypatch, response):
    http(monkeypatch, catalogue(), response)
    def never(**kwargs):
        pytest.fail("Storage must not be called for failed captures")
    reply = result(cam.capture_camera_checkpoint("test_cp", never, checkpoints=[checkpoint()]), "UPSTREAM_UNAVAILABLE")
    assert "sensitive" not in json.dumps(reply)


def test_unknown_or_malicious_ids_never_form_a_still_url(monkeypatch):
    calls = http(monkeypatch, catalogue([row("../../private"), row()]))
    result(cam.capture_camera_checkpoint("test_cp", lambda **kw: None, checkpoints=[checkpoint(camera_id="../../private")]), "UPSTREAM_UNAVAILABLE")
    assert [url for url, _ in calls] == [cam.CATALOGUE_URL]


@pytest.mark.parametrize("extra_bytes", [0, 1], ids=["at-limit", "over-limit"])
def test_image_stream_size_limit_without_content_length(monkeypatch, extra_bytes):
    monkeypatch.setattr(cam, "MAX_IMAGE_BYTES", len(JPEG))
    # Both bodies pass the JPEG signature check. Removing the size guard must
    # therefore reach storage, rather than being masked by malformed image data.
    response = Response(chunks=[JPEG[:3], b"x" * extra_bytes, JPEG[3:]])
    assert "Content-Length" not in response.headers
    http(monkeypatch, catalogue(), response)
    saved = []
    def save(**kwargs):
        saved.append(kwargs)
        return asset_for(kwargs)
    reply = cam.capture_camera_checkpoint("test_cp", save, checkpoints=[checkpoint()])
    if extra_bytes:
        result(reply, "UPSTREAM_UNAVAILABLE")
        assert "size limit" in reply["error"]["message"]
        assert saved == []
    else:
        result(reply)
        assert len(saved) == 1 and saved[0]["data"] == JPEG
    assert response.closed


def test_slow_stream_is_rejected(monkeypatch):
    tick = iter([0, 16])
    monkeypatch.setattr(cam.time, "monotonic", lambda: next(tick))
    http(monkeypatch, catalogue())
    with pytest.raises(cam.CameraUnavailable, match="time limit"):
        cam.DOTCameraClient().catalogue()


def asset_for(kwargs, **updates):
    value = dict(asset_id="saved-test", media_url="/media/saved-test", byte_size=len(kwargs["data"]),
                 provenance="live_capture", **{k:v for k,v in kwargs.items() if k != "data"})
    value.update(updates)
    return PhotoAsset(**value)


def test_capture_passes_exact_bytes_and_metadata_once(monkeypatch):
    http(monkeypatch, catalogue(), Response(headers={"Date": "Mon, 28 Sep 2026 12:00:00 GMT", "Last-Modified": "Mon, 28 Sep 2026 11:59:00 GMT"}))
    saved = []
    def save(**kwargs):
        saved.append(kwargs)
        return asset_for(kwargs)
    reply = result(cam.capture_camera_checkpoint("test_cp", save, checkpoints=[checkpoint()]))
    assert len(saved) == 1 and saved[0]["data"] == JPEG
    assert saved[0]["source_url"] == cam.CATALOGUE_URL + "/test-camera/image"
    assert saved[0]["checkpoint_id"] == "test_cp" and saved[0]["retrieved_at"].tzinfo is not None
    photo = PhotoAsset.model_validate(reply["data"]["photo"])
    assert photo.media_url == "/media/saved-test" and photo.frame_time is None
    assert photo.visibility == "unconfirmed" and photo.retrieved_at == saved[0]["retrieved_at"]
    assert "MOCK_HTTP_IMAGE_BYTES" not in json.dumps(reply)


@pytest.mark.parametrize("changes", [
    {"camera_id": "another-camera"}, {"checkpoint_id": "another-checkpoint"},
    {"byte_size": 1}, {"content_type": "image/png"}, {"retrieved_at": STAMP},
    {"frame_time": STAMP}, {"visibility": "user_confirmed_visible"}, {"provenance": "synthetic_fixture"},
    {"media_url": "https://evil.invalid/photo.jpg"}, {"media_url": "/media/%252e%252e/secrets"},
    {"media_url": "https://evil.invalid/media/x"},
])
def test_storage_cannot_substitute_capture_metadata(monkeypatch, changes):
    http(monkeypatch, catalogue(), Response())
    result(cam.capture_camera_checkpoint("test_cp", lambda **kw: asset_for(kw, **changes), checkpoints=[checkpoint()]), "INTERNAL_ERROR")


def test_storage_failure_is_not_retried_and_is_sanitized(monkeypatch):
    http(monkeypatch, catalogue(), Response())
    saved = []
    def fail(**kwargs):
        saved.append(kwargs)
        raise RuntimeError("secret-storage-token")
    reply = result(cam.capture_camera_checkpoint("test_cp", fail, checkpoints=[checkpoint()]), "INTERNAL_ERROR")
    assert len(saved) == 1 and "secret-storage-token" not in json.dumps(reply)
    result(cam.capture_camera_checkpoint("test_cp", checkpoints=[checkpoint()]), "INTERNAL_ERROR")
    result(cam.capture_camera_checkpoint("unknown", checkpoints=[]), "NO_MATCH")
    result(cam.capture_camera_checkpoint(None), "INVALID_ARGUMENT")


def test_invalid_local_records_are_not_silently_treated_as_empty():
    result(cam.find_camera_checkpoints(point=POINT, checkpoints=[{"bad": True}]), "INTERNAL_ERROR")
    result(cam.capture_camera_checkpoint("test_cp", checkpoints=[{"bad": True}]), "INTERNAL_ERROR")
    result(cam.find_camera_checkpoints(point=POINT, checkpoints=[checkpoint(), checkpoint()]), "INTERNAL_ERROR")


def test_exported_tools_exclude_server_only_arguments():
    for tool in cam.CAMERA_TOOLS:
        props = tool["function"]["parameters"]["properties"]
        assert not {"save_asset", "session_id", "client", "checkpoints", "allow_synthetic", "url"}.intersection(props)
        assert tool["function"]["parameters"]["additionalProperties"] is False


def test_image_verified_positions_are_found_captured_and_flagged(monkeypatch):
    record = checkpoint(verification_status="image_verified", last_field_verified_at=None, last_image_verified_at=STAMP)
    http(monkeypatch, catalogue(), Response(), catalogue(), Response())
    found = result(cam.find_camera_checkpoints(point=POINT, checkpoints=[record]))
    assert found["data"]["checkpoints"][0]["checkpoint"]["verification_status"] == "image_verified"
    assert any("not tested in person" in w for w in found["warnings"])
    field = result(cam.find_camera_checkpoints(point=POINT, checkpoints=[checkpoint()], client=FakeClient()))
    assert not any("not tested in person" in w for w in field["warnings"])
    reply = result(cam.capture_camera_checkpoint("test_cp", lambda **kw: asset_for(kw), checkpoints=[record]))
    assert reply["data"]["photo"]["checkpoint_id"] == "test_cp" and reply["data"]["verification_status"] == "image_verified"
    assert any("not tested in person" in w for w in reply["warnings"])
    field = result(cam.capture_camera_checkpoint("test_cp", lambda **kw: asset_for(kw), checkpoints=[checkpoint()], client=FakeClient()))
    assert not any("not tested in person" in w for w in field["warnings"])


class FakeClient:
    def catalogue(self):
        return {"test-camera": {"is_online": True}}, STAMP

    def still(self, camera_id, catalogue):
        return JPEG, "image/jpeg", f"{cam.CATALOGUE_URL}/{camera_id}/image", STAMP
