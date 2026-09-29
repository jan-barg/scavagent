"""Session persistence and adventure state operations."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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

    model_calls = []

    def completion(**kwargs):
        model_calls.append(1)
        if not retries:  # The browser gave up waiting and sent the same message again while the model was thinking
            retries.append(client.post("/chat", json=body).status_code)
        return reply(FakeMessage(content="Done once.", tool_calls=None))

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    first = client.post("/chat", json=body).json()
    later = client.post("/chat", json=body).json()

    assert retries == [409] and len(model_calls) == 1  # The mid-turn resend did not start a second turn
    assert first["response"] == "Done once." and later == first


def test_an_error_after_claiming_frees_the_claim(monkeypatch):
    store = state.MemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    real_load, claimed = store.load, []

    def load(session_id):  # Storage reads fail from the moment the message is claimed
        if claimed:
            raise OSError("storage unavailable")
        return real_load(session_id)

    monkeypatch.setattr(store, "claim", lambda *args: claimed.append(args) or state.MemoryStore.claim(store, *args))
    monkeypatch.setattr(store, "load", load)
    client = TestClient(app_module.app)
    body = {"message": "ready", "session_id": "flaky", "client_message_id": "m-1"}
    with pytest.raises(OSError):
        client.post("/chat", json=body)

    monkeypatch.setattr(store, "load", real_load)  # Storage recovers
    script(monkeypatch, FakeMessage(content="Here now.", tool_calls=None))
    assert client.post("/chat", json=body).json()["response"] == "Here now."  # Not a 409 for five minutes


class InstanceDied(BaseException):
    """The Cloud Run instance stops mid-turn: nothing in the app gets to handle it."""


def test_photo_from_a_turn_that_died_is_kept_and_its_message_runs_again_later(monkeypatch):
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
    later = state.utc_now() + state.IN_FLIGHT_TIMEOUT
    monkeypatch.setattr(state, "utc_now", lambda: later)
    script(monkeypatch, FakeMessage(content="Got the shot.", tool_calls=None))
    assert client.post("/chat", json=body).json()["response"] == "Got the shot."
    assert client.post("/chat", json=body).json()["response"] == "Got the shot."  # Replayed, not rerun

    after = store.load("died")
    assert [m["content"] for m in after.messages if m["role"] == "user"] == ["ready at the corner"]
    assert len(after.photos) == 1  # The frame from the turn that died


def test_a_turn_that_lost_its_save_says_so_and_the_message_can_be_sent_again(monkeypatch):
    store = state.MemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    client = TestClient(app_module.app)
    mine = {"message": "ready", "session_id": "race", "client_message_id": "m-1"}
    other = {"message": "also this", "session_id": "race", "client_message_id": "m-2"}
    answers, raced = iter(["Answer to m-2.", "Answer to m-1.", "Answer to m-1, again."]), []

    def completion(**kwargs):
        if not raced:  # A second tab sends another message, which finishes while this turn is running
            raced.append(True)
            client.post("/chat", json=other)
        return reply(FakeMessage(content=next(answers), tool_calls=None))

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    lost = client.post("/chat", json=mine).json()["response"]
    resent = client.post("/chat", json=mine)

    assert "wasn't saved" in lost  # The user is told, rather than shown a reply that was never stored
    assert resent.status_code == 200 and resent.json()["response"] == "Answer to m-1, again."
    texts = [m["text"] for m in store.load("race").transcript]
    assert texts == ["also this", "Answer to m-2.", "ready", "Answer to m-1, again."]


def test_a_long_conversation_stays_bounded_in_storage(monkeypatch):
    store = state.MemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    record = SessionRecord.new("long")
    for i in range(120):  # Every earlier turn used a tool, so a cut can land inside a tool exchange
        call = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function"}]}
        record.messages += [{"role": "user", "content": f"u{i}"}, call,
                            {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"}, {"role": "assistant", "content": f"a{i}"}]
    record.transcript = [{"role": "user", "text": f"t{i}", "at": NOW.isoformat()} for i in range(250)]
    for i in range(state.MAX_CACHED_REPLIES):
        record.remember_reply(f"old-{i}", {"response": f"old {i}", "session_id": "long", "tool_calls": []})
    store.save(record)
    script(monkeypatch, FakeMessage(content="Still here.", tool_calls=None))

    TestClient(app_module.app).post("/chat", json={"message": "hi", "session_id": "long", "client_message_id": "new"})

    saved = store.load("long")
    assert len(saved.messages) <= state.MAX_STORED_MESSAGES
    assert saved.messages[0]["role"] == "user" and saved.messages[-1]["content"] == "Still here."
    assert len(saved.transcript) <= state.MAX_TRANSCRIPT_ENTRIES and saved.transcript[-1]["text"] == "Still here."
    assert len(saved.replies) <= state.MAX_CACHED_REPLIES and "new" in saved.replies and "old-0" not in saved.replies


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


class ContendedFirestore:
    """Just enough of google.cloud.firestore for FirestoreStore's sessions and claims. When `rival` is set,
    another writer commits during our first attempt, so our commit is rejected and the transaction reruns."""

    def __init__(self):
        self.docs, self.rival = {}, None

    def document(self, doc_id):
        data = lambda: self.docs.get(doc_id)
        snapshot = lambda transaction=None: SimpleNamespace(exists=data() is not None, get=lambda key: data()[key])
        return SimpleNamespace(id=doc_id, get=snapshot, delete=lambda: self.docs.pop(doc_id, None))

    def transactional(self, fn):
        def run(transaction):
            for attempt in (1, 2):
                transaction.writes = []
                result = fn(transaction)
                if attempt == 1 and self.rival:
                    self.docs.update([self.rival])  # The rival wins; our first attempt is discarded
                    continue
                for doc, data in transaction.writes:
                    self.docs.pop(doc.id) if data is None else self.docs.update({doc.id: data})
                return result
        return run


class FakeTransaction:
    def __init__(self):
        self.writes = []

    def set(self, doc, data):
        self.writes.append((doc, data))

    def delete(self, doc):
        self.writes.append((doc, None))


def fake_firestore_store(fake=None):
    store = object.__new__(state.FirestoreStore)
    store._firestore = store._sessions = store._claims = fake or ContendedFirestore()
    store._db = SimpleNamespace(transaction=FakeTransaction)
    return store


def make_store(kind, tmp_path):
    return {"memory": state.MemoryStore, "sqlite": lambda: state.SqliteStore(tmp_path / "db.sqlite"),
            "firestore": fake_firestore_store}[kind]()


@pytest.mark.parametrize("kind", ["memory", "sqlite", "firestore"])
def test_concurrent_turns_cannot_overwrite_each_other(kind, tmp_path):
    store = make_store(kind, tmp_path)
    store.save(SessionRecord.new("s"))
    first, second = store.load("s"), store.load("s")
    first.messages.append({"role": "user", "content": "first"})
    store.save(first)
    second.messages.append({"role": "user", "content": "second"})
    with pytest.raises(state.VersionConflict):
        store.save(second)
    assert store.load("s").messages == [{"role": "user", "content": "first"}]


@pytest.mark.parametrize("kind", ["memory", "sqlite", "firestore"])
def test_one_turn_at_a_time_can_claim_a_message(kind, tmp_path):
    store = make_store(kind, tmp_path)
    first = store.claim("s", "m-1", NOW)
    assert first and store.claim("s", "m-1", NOW + timedelta(minutes=4)) is None
    assert store.claim("s", "m-2", NOW)  # Other messages are independent

    takeover = store.claim("s", "m-1", NOW + state.IN_FLIGHT_TIMEOUT)  # An abandoned claim can be taken over
    store.release("s", "m-1", first)  # The overrunning first turn finishing late frees nothing
    assert takeover and store.claim("s", "m-1", NOW + state.IN_FLIGHT_TIMEOUT) is None
    store.release("s", "m-1", takeover)
    assert store.claim("s", "m-1", NOW + state.IN_FLIGHT_TIMEOUT)


def test_firestore_save_rerun_after_a_lost_race_does_not_overwrite_the_winner():
    fake = ContendedFirestore()
    store = fake_firestore_store(fake)

    store.save(SessionRecord.new("s"))
    mine, rival = store.load("s"), store.load("s")
    rival.messages.append({"role": "user", "content": "rival"})
    fake.rival = ("s", {"version": 2, "data": rival.model_copy(update={"storage_version": 2}).model_dump_json()})
    mine.messages.append({"role": "user", "content": "mine"})

    with pytest.raises(state.VersionConflict):
        store.save(mine)
    assert store.load("s").messages == [{"role": "user", "content": "rival"}]
    assert mine.storage_version == 1


def test_a_photo_whose_save_failed_is_detached_and_the_turn_can_still_save(monkeypatch, tmp_path):
    store = state.SqliteStore(tmp_path / "db.sqlite")
    ctx = ToolContext(record=SessionRecord.new("s1"), store=store, now=lambda: NOW)
    real_dump = state._dump

    def disk_full(record):
        raise OSError("disk full")

    monkeypatch.setattr(state, "_dump", disk_full)
    with pytest.raises(OSError):
        ctx.save_asset(b"x", "image/jpeg", "cam-1", "fixture_cam_cp_1", None, NOW)
    assert ctx.record.photos == {} and ctx.record.adventure.photo_asset_ids == []

    monkeypatch.setattr(state, "_dump", real_dump)
    store.save(ctx.record)  # The failed attempt did not leave a version that makes this look stale
    assert store.load("s1").photos == {}


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

    # A finished stop can't also be recorded as blocked, and a call without a stop says what's missing.
    blocked = tools.update_adventure_state(active, "block_checkpoint", checkpoint_id="stop_1")
    assert "different outcome" in blocked["error"]["message"] and active.record.adventure.blocked_ids == []
    assert "checkpoint_id" in tools.update_adventure_state(active, "complete_checkpoint")["error"]["message"]


def test_only_beats_from_the_plan_can_be_revealed(active):
    invented = tools.update_adventure_state(active, "reveal_beat", beat_id="beat_the_model_made_up")
    assert invented["error"]["code"] == "INVALID_ARGUMENT"
    assert tools.update_adventure_state(active, "reveal_beat", beat_id="beat_1")["ok"]
    assert active.record.adventure.revealed_beat_ids == ["beat_1"]


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


def test_only_the_newest_evaluated_drafts_are_kept():
    record, plan = SessionRecord.new("s"), load_scenario("start_only").plan
    for i in range(state.MAX_DRAFTS + 2):
        record.remember_draft(f"d{i}", state.EvaluatedDraft(kind="new", plan=plan))
    record.remember_draft("d2", state.EvaluatedDraft(kind="new", plan=plan))  # Evaluated again: now the newest
    assert list(record.drafts) == ["d3", "d4", "d2"]

    huge = plan.model_copy(update={"story": plan.story.model_copy(update={"solution": "x" * state.MAX_DRAFT_BYTES})})
    assert record.remember_draft("d5", state.EvaluatedDraft(kind="new", plan=huge)) is False
    assert list(record.drafts) == ["d3", "d4", "d2"]


def bulky_session(session_id, turns=30, blob="x" * 30_000):
    """A long adventure: each earlier turn left a planning-sized tool result in all three stored copies."""
    record = SessionRecord.new(session_id)
    tools.load_dev_adventure(ToolContext(record=record, store=state.MemoryStore()), "constrained_route")
    for i in range(turns):
        call = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function"}]}
        record.messages += [{"role": "user", "content": f"u{i}"}, call,
                            {"role": "tool", "tool_call_id": f"c{i}", "content": blob}, {"role": "assistant", "content": f"a{i}"}]
        trace = [{"name": "research_place", "args": {}, "result": {"ok": True, "data": {"text": blob}}}]
        record.transcript += [{"role": "user", "text": f"u{i}", "at": NOW.isoformat()},
                              {"role": "assistant", "text": f"a{i}", "tool_calls": trace, "at": NOW.isoformat()}]
        record.remember_reply(f"m{i}", {"response": f"a{i}", "session_id": session_id, "tool_calls": trace})
    assert len(record.model_dump_json()) > 1_048_576  # Firestore would refuse to store this
    return record


def test_a_long_adventure_still_fits_in_one_firestore_document(monkeypatch):
    store = state.MemoryStore()  # No size limit of its own, so the test sees exactly what would be written
    monkeypatch.setattr(app_module, "store", store)
    store.save(bulky_session("long"))
    script(monkeypatch, FakeMessage(content="Still with you.", tool_calls=None))

    TestClient(app_module.app).post("/chat", json={"message": "next", "session_id": "long", "client_message_id": "new"})

    saved = store.load("long")
    assert len(saved.model_dump_json()) <= state.MAX_RECORD_BYTES
    assert saved.messages[0]["role"] == "user" and saved.messages[-1]["content"] == "Still with you."
    assert saved.transcript[-1]["text"] == "Still with you." and "new" in saved.replies
    assert saved.adventure.active_plan_id in saved.plans and saved.adventure.status == "active"  # Progress kept


def test_a_mid_turn_photo_save_also_stays_inside_the_budget():
    record = bulky_session("photo")
    ctx = ToolContext(record=record, store=state.MemoryStore(), now=lambda: NOW)
    photo = ctx.save_asset(b"jpeg", "image/jpeg", "cam-1", "fixture_cam_cp_1", None, NOW)
    saved = ctx.store.load("photo")
    assert len(saved.model_dump_json()) <= state.MAX_RECORD_BYTES and photo.asset_id in saved.photos


def test_a_saved_plan_cannot_be_overwritten(ctx):
    plan = load_scenario("start_only").plan
    state.save_plan(ctx, plan)
    changed = plan.model_copy(update={"estimated_total_minutes": plan.estimated_total_minutes + 60})
    assert "immutable" in state.save_plan(ctx, changed)["error"]["message"]
    assert ctx.record.plans[plan.plan_id] == plan


def test_revision_keeps_history_and_required_stops(active):
    fixture = load_scenario("revision_after_skip")
    tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1")
    tools.update_adventure_state(active, "reveal_beat", beat_id="beat_1")
    tools.update_adventure_state(active, "skip_checkpoint", checkpoint_id="stop_2")
    revised = passing(fixture.revised_plan)

    assert state.save_plan(active, fixture.revised_plan)["error"]["code"] == "PLAN_INFEASIBLE"

    unlinked = revised.model_copy(update={"plan_id": "bad_0", "supersedes_plan_id": None})
    assert "supersedes_plan_id" in state.save_plan(active, unlinked)["error"]["message"]

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


def test_stops_are_completed_in_plan_order_and_finishing_needs_them_all_resolved(active):
    ahead = tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_2")
    assert "not the current checkpoint (stop_1)" in ahead["error"]["message"] and active.record.adventure.completed_ids == []

    tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1")
    early = tools.update_adventure_state(active, "finish_adventure")
    assert "abandon_adventure" in early["error"]["next_step"] and active.record.adventure.status == "active"

    # Skipping ahead is still allowed (for example "skip the next optional stop"), and resolves the route.
    assert tools.update_adventure_state(active, "skip_checkpoint", checkpoint_id="stop_3")["ok"]
    tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_2")
    assert tools.update_adventure_state(active, "finish_adventure")["data"]["status"] == "completed"


def test_abandoning_midway_leaves_no_stop_to_continue(active):
    tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id="stop_1")
    assert tools.update_adventure_state(active, "abandon_adventure")["data"]["status"] == "abandoned"
    assert active.record.adventure.current_checkpoint_id is None
    assert "no adventure in progress" in tools.update_adventure_state(
        active, "complete_checkpoint", checkpoint_id="stop_2")["error"]["message"]


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

    # An invented visibility value or photo id is refused, so nothing unloadable is stored.
    invented_value = tools.update_adventure_state(active, "set_photo_visibility", asset_id=photo.asset_id,
                                                  visibility="probably")
    invented_photo = tools.update_adventure_state(active, "set_photo_visibility", asset_id="not-a-photo",
                                                  visibility="user_confirmed_visible")
    assert invented_value["error"]["code"] == invented_photo["error"]["code"] == "INVALID_ARGUMENT"
    assert active.record.photos[photo.asset_id].visibility == "unconfirmed"

    tools.update_adventure_state(active, "set_photo_visibility", asset_id=photo.asset_id,
                                 visibility="user_confirmed_visible")
    assert active.record.photos[photo.asset_id].visibility == "user_confirmed_visible"
    assert TestClient(app_module.app).get("/media/not-a-real-id").status_code == 404


def test_new_adventure_after_finishing_keeps_photos_but_resets_progress(active):
    photo = active.save_asset(b"x", "image/jpeg", "cam-1", "fixture_cam_cp_1", None, NOW)
    for stop in ("stop_1", "stop_2", "stop_3"):
        tools.update_adventure_state(active, "complete_checkpoint", checkpoint_id=stop)
    assert tools.update_adventure_state(active, "finish_adventure")["ok"]

    state.save_plan(active, load_scenario("start_only").plan)
    after = active.record.adventure
    assert after.status == "proposed" and after.completed_ids == []
    assert after.photo_asset_ids == [photo.asset_id]


def test_a_capture_is_remembered_for_its_message_until_that_message_has_a_reply(active):
    active.message_id = "m-1"
    photo = active.save_asset(b"x", "image/jpeg", "cam-1", "fixture_cam_cp_1", None, NOW)

    assert active.saved_capture("fixture_cam_cp_1") == photo
    stored = active.store.load("s1")  # Already durable, so a rerun after the turn dies still finds it
    assert ToolContext(record=stored, store=active.store, message_id="m-1").saved_capture("fixture_cam_cp_1") == photo
    assert ToolContext(record=stored, store=active.store, message_id="m-2").saved_capture("fixture_cam_cp_1") is None
    assert active.saved_capture("another_camera_checkpoint") is None

    active.record.remember_reply("m-1", {"response": "Got you.", "session_id": "s1", "tool_calls": []})
    assert active.saved_capture("fixture_cam_cp_1") is None  # A resend now replays the reply instead


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
