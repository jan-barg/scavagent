"""The camera tools as the model reaches them: registered, bound to the session, one capture per message.

The field-verified position below is TEST data for a fake DOT feed; it is not fieldwork evidence.
"""

import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import state
import tools
from fakes import FakeMessage, script, tool_call
from integrations import cameras

CAMERA = "test-camera"
STILL_URL = f"{cameras.CATALOGUE_URL}/{CAMERA}/image"
JPEG = b"\xff\xd8\xffTEST_STILL_NOT_A_PHOTO\xff\xd9"
POINT = {"lat": 40.785, "lng": -73.97}


class Response:
    def __init__(self, body, content_type):
        self.body, self.status_code, self.headers = body, 200, {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield self.body


@pytest.fixture
def dot(monkeypatch, tmp_path):
    """One field-verified position (test data) and a fake DOT feed; returns the URLs fetched."""
    monkeypatch.delenv("SCAVAGENT_DEV_CAMERA_FIXTURES", raising=False)
    catalogue = tmp_path / "camera_catalogue.json"
    catalogue.write_text(json.dumps({"checkpoints": [{
        "checkpoint_id": "test_cp", "camera_id": CAMERA, "stand_location": POINT,
        "address": "TEST", "landmark": "TEST", "side_of_street": "TEST",
        "positioning_instructions": "TEST instructions.", "verification_status": "field_verified",
        "last_field_verified_at": "2026-09-28T12:00:00+00:00", "enabled": True,
    }]}))
    monkeypatch.setattr(cameras, "CATALOGUE_PATH", catalogue)
    fetched = []

    def get(url, **kwargs):
        fetched.append(url)
        if url == cameras.CATALOGUE_URL:
            return Response(json.dumps([{"id": CAMERA, "isOnline": "true"}]).encode(), "application/json")
        assert url == STILL_URL, f"unexpected request {url}"
        return Response(JPEG, "image/jpeg")

    monkeypatch.setattr(cameras.requests, "get", get)
    return fetched


class InstanceDied(BaseException):
    """The Cloud Run instance stops mid-turn: nothing in the app gets to handle it."""


def capture(call_id):
    return FakeMessage(content=None, tool_calls=[tool_call(call_id, "capture_camera_checkpoint", '{"checkpoint_id": "test_cp"}')])


def test_a_capture_is_saved_to_the_session_and_not_retaken_when_its_message_runs_again(dot, monkeypatch):
    store = state.MemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    client = TestClient(app_module.app)
    ready = {"message": "I'm in position", "session_id": "cam", "client_message_id": "m-1"}

    script(monkeypatch, capture("c1"), InstanceDied())
    with pytest.raises(InstanceDied):
        client.post("/chat", json=ready)
    [photo_id] = store.load("cam").photos
    assert client.get(f"/media/{photo_id}").content == JPEG  # Saved before the turn died

    later = state.utc_now() + state.IN_FLIGHT_TIMEOUT  # The dead turn's claim has expired
    monkeypatch.setattr(state, "utc_now", lambda: later)
    script(monkeypatch, capture("c2"), FakeMessage(content="Got you.", tool_calls=None))
    [call] = client.post("/chat", json=ready).json()["tool_calls"]
    assert call["result"]["data"]["already_captured"] is True and call["result"]["data"]["photo"]["asset_id"] == photo_id
    assert call["result"]["data"]["verification_status"] == "field_verified"
    assert not any("not tested in person" in w for w in call["result"]["warnings"])
    assert dot.count(STILL_URL) == 1 and len(store.load("cam").photos) == 1

    # A new message asking again is a real retake.
    script(monkeypatch, capture("c3"), FakeMessage(content="Retook it.", tool_calls=None))
    client.post("/chat", json={**ready, "message": "try again", "client_message_id": "m-2"})
    assert dot.count(STILL_URL) == 2 and len(store.load("cam").photos) == 2


def test_a_replayed_capture_still_says_the_position_was_only_checked_on_the_image(dot, monkeypatch):
    data = json.loads(cameras.CATALOGUE_PATH.read_text())
    data["checkpoints"][0].update(verification_status="image_verified", last_field_verified_at=None,
                                  last_image_verified_at="2026-09-29T19:00:00+00:00")
    cameras.CATALOGUE_PATH.write_text(json.dumps(data))
    store = state.MemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    client = TestClient(app_module.app)
    ready = {"message": "ready", "session_id": "img", "client_message_id": "m-1"}

    script(monkeypatch, capture("c1"), InstanceDied())
    with pytest.raises(InstanceDied):
        client.post("/chat", json=ready)
    later = state.utc_now() + state.IN_FLIGHT_TIMEOUT
    monkeypatch.setattr(state, "utc_now", lambda: later)
    script(monkeypatch, capture("c2"), FakeMessage(content="Here it is.", tool_calls=None))
    [call] = client.post("/chat", json=ready).json()["tool_calls"]

    assert call["result"]["data"]["already_captured"] is True
    assert call["result"]["data"]["verification_status"] == "image_verified"
    assert any("not tested in person" in w for w in call["result"]["warnings"])


def test_the_model_cannot_reach_server_only_camera_arguments(dot):
    ctx = state.ToolContext(record=state.SessionRecord.new("s"), store=state.MemoryStore())

    with_fixtures = tools.run_tool("find_camera_checkpoints", {"point": POINT, "allow_synthetic": True}, ctx)
    own_storage = tools.run_tool("capture_camera_checkpoint", {"checkpoint_id": "test_cp", "save_asset": "x"}, ctx)
    other_session = tools.run_tool("capture_camera_checkpoint", {"checkpoint_id": "test_cp", "session_id": "s2"}, ctx)
    assert with_fixtures["error"]["code"] == own_storage["error"]["code"] == other_session["error"]["code"] == "INVALID_ARGUMENT"
    assert dot == [] and ctx.record.photos == {}  # Nothing was fetched or saved

    found = tools.run_tool("find_camera_checkpoints", {"point": POINT}, ctx)
    assert [c["checkpoint"]["checkpoint_id"] for c in found["data"]["checkpoints"]] == ["test_cp"]


def test_every_tool_the_model_is_offered_can_run():
    assert {t["function"]["name"] for t in tools.TOOLS} == set(tools.TOOL_MAP)
