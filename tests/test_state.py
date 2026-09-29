"""Session persistence and adventure state operations."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
import state
import tools
from fakes import FakeMessage, reply, script, tool_call
from fixtures import load_scenario
from schemas import ValidationReport
from state import SessionRecord, ToolContext

NOW = datetime(2026, 9, 28, 19, 30, tzinfo=timezone.utc)


def passing(plan):
    return plan.model_copy(update={"validation": ValidationReport(ok=True, evaluated_at=NOW)})


@pytest.fixture
def ctx():
    return ToolContext(record=SessionRecord.new("s1"), store=state.MemoryStore(), now=lambda: NOW)


@pytest.fixture
def active(ctx):
    """The constrained-route fixture, running, with nothing done yet."""
    tools.load_dev_adventure(ctx, "constrained_route")
    return ctx


# --- Persistence through the HTTP boundary ---


def test_conversation_and_progress_survive_a_restart(tmp_path, monkeypatch):
    db = tmp_path / "scavagent.db"
    monkeypatch.setattr(app_module, "store", state.SqliteStore(db))
    monkeypatch.setitem(tools.TOOL_MAP, "load_dev_adventure", tools.load_dev_adventure)
    script(
        monkeypatch,
        FakeMessage(content=None, tool_calls=[tool_call("c1", "load_dev_adventure", '{"scenario": "constrained_route"}')]),
        FakeMessage(content=None, tool_calls=[tool_call("c2", "update_adventure_state",
                                                        '{"operation": "complete_checkpoint", "checkpoint_id": "stop_1"}')]),
        FakeMessage(content="Loaded a test adventure; stop 1 done.", tool_calls=None),
    )
    session_id = TestClient(app_module.app).post("/chat", json={"message": "dev hunt, I'm at stop 1"}).json()["session_id"]

    # A new process: a fresh store object on the same database.
    monkeypatch.setattr(app_module, "store", state.SqliteStore(db))
    seen = script(monkeypatch, FakeMessage(content="Welcome back.", tool_calls=None))
    client = TestClient(app_module.app)
    client.post("/chat", json={"message": "I'm back", "session_id": session_id})

    record = app_module.store.load(session_id)
    assert record.adventure.completed_ids == ["stop_1"]
    assert record.adventure.current_checkpoint_id == "stop_2"
    assert "dev hunt, I'm at stop 1" in json.dumps(seen[0])  # Earlier turn is in the model's context
    history = client.get("/history", params={"session_id": session_id}).json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert [c["name"] for c in history[1]["tool_calls"]] == ["load_dev_adventure", "update_adventure_state"]


def test_retried_message_returns_the_first_reply_without_rerunning(monkeypatch):
    monkeypatch.setattr(app_module, "store", state.MemoryStore())
    seen = script(monkeypatch, FakeMessage(content="Done once.", tool_calls=None))
    client = TestClient(app_module.app)
    body = {"message": "ready", "session_id": "retry-test", "client_message_id": "m-1"}

    first = client.post("/chat", json=body).json()
    second = client.post("/chat", json=body).json()

    assert first == second and len(seen) == 1
    assert len(app_module.store.load("retry-test").transcript) == 2


def test_retry_while_the_first_turn_is_still_running_does_not_run_it_again(monkeypatch):
    monkeypatch.setattr(app_module, "store", state.MemoryStore())
    client = TestClient(app_module.app)
    body = {"message": "ready", "session_id": "in-flight", "client_message_id": "m-1"}
    retries = []

    def completion(**kwargs):
        # The browser gave up waiting and sent the same message again while the model was thinking.
        retries.append(client.post("/chat", json=body).status_code)
        return reply(FakeMessage(content="Done once.", tool_calls=None))

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    first = client.post("/chat", json=body).json()
    later = client.post("/chat", json=body).json()

    assert retries == [409]  # The model ran once; the mid-turn retry did not start a second turn
    assert first["response"] == "Done once." and later == first


class InstanceDied(BaseException):
    """The Cloud Run instance stops mid-turn: nothing in the app gets to handle it."""


def test_photo_from_a_turn_that_died_is_kept_and_the_message_can_run_again(monkeypatch):
    store = state.MemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    capture = lambda ctx: tools.tool_ok(
        {"asset_id": ctx.save_asset(b"jpeg", "image/jpeg", "cam-1", "fixture_cam_cp_1", None, NOW).asset_id})
    monkeypatch.setitem(tools.TOOL_MAP, "fake_capture", capture)
    monkeypatch.setattr(tools, "SESSION_TOOLS", tools.SESSION_TOOLS | {"fake_capture"})
    script(monkeypatch, FakeMessage(content=None, tool_calls=[tool_call("c1", "fake_capture", "{}")]), InstanceDied())
    client = TestClient(app_module.app)
    body = {"message": "ready at the corner", "session_id": "died", "client_message_id": "m-1"}

    with pytest.raises(InstanceDied):
        client.post("/chat", json=body)

    saved = store.load("died")
    assert list(saved.photos) == saved.adventure.photo_asset_ids and len(saved.photos) == 1
    assert saved.messages == [] and saved.transcript == []  # No half turn was stored

    # Until the claim expires the message is treated as still running; after that it runs again, once.
    assert client.post("/chat", json=body).status_code == 409
    saved.in_flight["m-1"] -= state.IN_FLIGHT_TIMEOUT
    store.save(saved)
    script(monkeypatch, FakeMessage(content="Got the shot.", tool_calls=None))
    assert client.post("/chat", json=body).json()["response"] == "Got the shot."

    after = store.load("died")
    assert [m["content"] for m in after.messages if m["role"] == "user"] == ["ready at the corner"]
    assert len(after.photos) == 1 and after.in_flight == {}


def test_unknown_history_and_malformed_session_ids_are_rejected(monkeypatch):
    monkeypatch.setattr(app_module, "store", state.MemoryStore())
    client = TestClient(app_module.app)
    assert client.get("/history", params={"session_id": "never-seen"}).status_code == 404
    assert client.post("/chat", json={"message": "hi", "session_id": "../etc/passwd"}).status_code == 400


def test_client_location_is_saved_and_older_fixes_do_not_replace_newer(monkeypatch):
    monkeypatch.setattr(app_module, "store", state.MemoryStore())
    script(monkeypatch, *[FakeMessage(content="ok", tool_calls=None)] * 2)
    client = TestClient(app_module.app)
    fix = lambda minute: {"point": {"lat": 40.78, "lng": -73.97}, "source": "browser",
                          "observed_at": f"2026-09-28T15:{minute:02d}:00-04:00", "accuracy_m": 20}

    client.post("/chat", json={"message": "a", "session_id": "loc", "location": fix(10)})
    client.post("/chat", json={"message": "b", "session_id": "loc", "location": fix(5)})

    latest = app_module.store.load("loc").adventure.latest_location
    assert latest.observed_at.minute == 10


@pytest.mark.parametrize("store_factory", [state.MemoryStore, lambda: None])
def test_concurrent_turns_cannot_overwrite_each_other(store_factory, tmp_path):
    store = store_factory() or state.SqliteStore(tmp_path / "db.sqlite")
    store.save(SessionRecord.new("s"))
    first, second = store.load("s"), store.load("s")
    first.messages.append({"role": "user", "content": "first"})
    store.save(first)
    second.messages.append({"role": "user", "content": "second"})
    with pytest.raises(state.VersionConflict):
        store.save(second)
    assert store.load("s").messages == [{"role": "user", "content": "first"}]


# --- State operations ---


def test_session_tools_cannot_be_pointed_at_another_session(active):
    result = tools.run_tool("get_adventure_state", {"session_id": "someone-else"}, active)
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_completion_advances_and_repeats_are_harmless(active):
    first = tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1", note="the brownstone")
    version = active.record.adventure.version
    repeat = tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1")

    assert first["data"]["current_checkpoint_id"] == "stop_2"
    assert repeat["data"]["already_recorded"] is True
    assert active.record.adventure.version == version
    assert active.record.adventure.user_reports[0].text == "the brownstone"


def test_required_stop_needs_an_explicit_waiver_to_skip(active):
    refused = tools.update_adventure_state(active, "skip_checkpoint", checkpoint_id="stop_1")
    assert refused["error"]["code"] == "INVALID_ARGUMENT" and "confirm" in refused["error"]["next_step"]

    waived = tools.update_adventure_state(active, "skip_checkpoint", checkpoint_id="stop_1", user_waived_required=True)
    assert waived["ok"] and active.record.adventure.skipped_ids == ["stop_1"]


def test_stale_expected_version_is_rejected(active):
    stale = active.record.adventure.version
    tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1")
    result = tools.update_adventure_state(active, "reveal_beat", beat_id="beat_1", expected_version=stale)
    assert result["error"]["code"] == "STATE_VERSION_CONFLICT"
    assert active.record.adventure.revealed_beat_ids == []


def test_unevaluated_plans_cannot_become_active(ctx):
    plan = load_scenario("start_only").plan
    assert state.save_plan(ctx, plan, activate=True)["error"]["code"] == "PLAN_INFEASIBLE"
    assert state.save_plan(ctx, plan)["data"]["status"] == "proposed"
    assert tools.update_adventure_state(ctx, "start_adventure")["error"]["code"] == "PLAN_INFEASIBLE"


def test_revision_keeps_history_and_required_stops(active):
    fixture = load_scenario("revision_after_skip")
    tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1")
    tools.update_adventure_state(active, "reveal_beat", beat_id="beat_1")
    tools.update_adventure_state(active, "skip_checkpoint", checkpoint_id="stop_2")
    revised = passing(fixture.revised_plan)

    assert state.save_plan(active, fixture.revised_plan)["error"]["code"] == "PLAN_INFEASIBLE"

    rewritten = revised.model_copy(update={"plan_id": "bad_1", "checkpoints": revised.checkpoints[1:]})
    assert "Completed checkpoint stop_1" in state.save_plan(active, rewritten)["error"]["message"]

    story = revised.story.model_copy(update={"beats": revised.story.beats[1:]})
    no_beat = revised.model_copy(update={"plan_id": "bad_2", "story": story})
    assert "Revealed story beats" in state.save_plan(active, no_beat)["error"]["message"]

    saved = state.save_plan(active, revised)
    after = active.record.adventure
    assert saved["ok"] and after.active_plan_id == "fixture_plan_constrained_v2"
    assert after.completed_ids == ["stop_1"] and after.skipped_ids == ["stop_2"]
    assert after.revealed_beat_ids == ["beat_1"] and after.current_checkpoint_id == "stop_4"


def test_revision_cannot_silently_drop_an_unfinished_required_stop(active):
    revised = passing(load_scenario("revision_after_skip").revised_plan)
    without_required = revised.model_copy(update={"checkpoints": revised.checkpoints[1:]})

    result = state.save_plan(active, without_required)
    assert "Required stop stop_1" in result["error"]["message"]
    assert state.save_plan(active, without_required, waived_required_ids=["stop_1"])["ok"]


def test_finishing_reveals_the_solution_only_at_the_end(active):
    assert active.record.adventure.status == "active"
    assert state.state_summary(active.record)["solution_if_finished"] is None
    for stop in ("stop_1", "stop_2", "stop_3"):
        tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id=stop)
    assert state.state_summary(active.record)["solution_if_finished"]
    assert tools.update_adventure_state(active, "finish_adventure")["data"]["status"] == "completed"


# --- Photos ---


def test_saved_photo_is_served_later_and_tracked_in_progress(active, monkeypatch):
    monkeypatch.setattr(app_module, "store", active.store)
    photo = active.save_asset(
        b"\xff\xd8jpeg-bytes", "image/jpeg", camera_id="cam-1", checkpoint_id="fixture_cam_cp_1",
        source_url="https://webcams.nyctmc.org/api/cameras/cam-1/image", retrieved_at=NOW,
    )

    served = TestClient(app_module.app).get(photo.media_url)
    assert served.content == b"\xff\xd8jpeg-bytes" and served.headers["content-type"] == "image/jpeg"
    assert active.record.adventure.photo_asset_ids == [photo.asset_id]
    assert photo.visibility == "unconfirmed" and photo.frame_time is None

    tools.update_adventure_state(active, "set_photo_visibility", asset_id=photo.asset_id,
                                 visibility="user_confirmed_visible")
    assert active.record.photos[photo.asset_id].visibility == "user_confirmed_visible"
    assert TestClient(app_module.app).get("/media/not-a-real-id").status_code == 404


def test_new_adventure_after_finishing_keeps_photos_but_resets_progress(active):
    photo = active.save_asset(b"x", "image/jpeg", "cam-1", "fixture_cam_cp_1", None, NOW)
    tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1")
    tools.update_adventure_state(active, "finish_adventure")

    state.save_plan(active, load_scenario("start_only").plan)
    after = active.record.adventure
    assert after.status == "proposed" and after.completed_ids == []
    assert after.photo_asset_ids == [photo.asset_id]


def test_the_model_can_match_a_photo_to_its_stop(active):
    # PhotoAsset.checkpoint_id is the camera checkpoint; the plan's stop points at it.
    active.save_asset(b"x", "image/jpeg", "fixture-camera-not-dot", "fixture_cam_cp_1", None, NOW)
    summary = state.state_summary(active.record)
    [photo] = summary["photos"]
    [stop] = [c for c in summary["checkpoints"] if c["camera_checkpoint_id"] == photo["checkpoint_id"]]
    assert stop["checkpoint_id"] == "stop_3"


def test_old_location_age_is_reported_to_the_model():
    record = SessionRecord.new("s")
    observed = NOW - timedelta(minutes=12)
    state.update_location(record, state.LocationContext(
        point={"lat": 40.78, "lng": -73.97}, source="browser", observed_at=observed, accuracy_m=30))
    assert "12 min old, accuracy 30 m" in app_module.app_context(record, NOW)
