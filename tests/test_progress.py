"""Live turn progress (GET /progress) and the page's board (GET /adventure), with the model scripted."""

import functools
import sqlite3
import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import app as app_module
import state
import tools
import turn_progress
from fakes import FakeMessage, script, tool_call
from fixtures import load_scenario
from schemas import AdventureState, Character, PhotoAsset, tool_ok
from state import SessionRecord
from test_state import fake_firestore_store, make_store

KINDS = ["memory", "sqlite", "firestore"]
LATE = state.IN_FLIGHT_TIMEOUT + timedelta(seconds=1)


class WatchedStore:
    """A store that counts session loads and logs progress writes and releases in order."""

    def __init__(self, inner, write_delay=0.0):
        self.inner, self.write_delay, self.loads, self.events = inner, write_delay, 0, []

    def load(self, session_id):
        self.loads += 1
        return self.inner.load(session_id)

    def set_progress(self, *args):
        self.events.append("progress")
        time.sleep(self.write_delay)
        return self.inner.set_progress(*args)

    def release(self, *args):
        self.events.append("release")
        return self.inner.release(*args)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture
def store(monkeypatch):
    watched = WatchedStore(state.MemoryStore())
    monkeypatch.setattr(app_module, "store", watched)
    monkeypatch.setattr(app_module, "ProgressReporter", functools.partial(turn_progress.ProgressReporter, interval=0.01))
    return watched


@pytest.fixture
def client(store):
    return TestClient(app_module.app)


def poll(client, session_id, message_id):
    return client.get("/progress", params={"session_id": session_id, "client_message_id": message_id}).json()


def wait_for(check, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (value := check()) is not None and value is not False:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


# --- Stores ---


@pytest.mark.parametrize("kind", KINDS)
def test_progress_lives_on_the_claim_and_goes_with_it(kind, tmp_path):
    store, now = make_store(kind, tmp_path), state.utc_now()
    token = store.claim("s", "m", now)
    assert store.progress("s", "m", now) == {"phase": None, "steps": [], "started_at": now}

    assert store.set_progress("s", "m", token, {"phase": "tools", "steps": [{"i": 0, "status": "running"}]})
    live = store.progress("s", "m", now)
    assert live["phase"] == "tools" and live["steps"] == [{"i": 0, "status": "running"}]

    store.release("s", "m", token)
    assert store.progress("s", "m", now) is None
    assert not store.set_progress("s", "m", token, {"phase": "tools", "steps": []})


@pytest.mark.parametrize("kind", KINDS)
def test_an_expired_claim_shows_no_progress_and_its_old_token_cannot_write(kind, tmp_path):
    store, now = make_store(kind, tmp_path), state.utc_now()
    old = store.claim("s", "m", now)
    store.set_progress("s", "m", old, {"phase": "tools", "steps": [{"i": 0, "status": "running"}]})

    assert store.progress("s", "m", now + LATE) is None  # A crashed turn's claim is not live
    new = store.claim("s", "m", now + LATE)
    assert new and not store.set_progress("s", "m", old, {"phase": "tools", "steps": [{"i": 9}]})
    assert store.progress("s", "m", now + LATE)["steps"] == []


def test_firestore_writes_progress_as_an_update_that_keeps_the_claim():
    store, now = fake_firestore_store(), state.utc_now()
    firestore = store._claims
    token = store.claim("s", "m", now)
    doc_id = store._claim_doc("s", "m").id
    claimed = dict(firestore.docs[doc_id])
    firestore.applied.clear()

    assert store.set_progress("s", "m", token, {"phase": "tools", "steps": []})

    assert firestore.applied == [("update", doc_id)]  # Never a set, which would replace started_at
    assert {k: firestore.docs[doc_id][k] for k in claimed} == claimed
    assert store.claim("s", "m", now + timedelta(seconds=10)) is None  # Still live
    assert store.claim("s", "m", now + LATE) is not None  # And still expires


def test_sqlite_adds_the_progress_column_to_an_older_database(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE claims (session_id TEXT, message_id TEXT, started_at TEXT, token TEXT, "
               "PRIMARY KEY (session_id, message_id))")
    db.commit()
    db.close()

    store, now = state.SqliteStore(path), state.utc_now()
    token = store.claim("s", "m", now)
    assert store.set_progress("s", "m", token, {"phase": "thinking", "steps": []})
    assert store.progress("s", "m", now)["phase"] == "thinking"


def test_progress_written_by_one_server_is_read_by_another(tmp_path):
    first, second, now = state.SqliteStore(tmp_path / "db"), state.SqliteStore(tmp_path / "db"), state.utc_now()
    token = first.claim("s", "m", now)
    first.set_progress("s", "m", token, {"phase": "tools", "steps": [{"i": 0, "status": "ok"}]})
    assert second.progress("s", "m", now)["steps"] == [{"i": 0, "status": "ok"}]


# --- The reporter ---


class RecordingStore:
    def __init__(self):
        self.writes = []

    def set_progress(self, session_id, message_id, token, progress):
        self.writes.append(progress)  # Kept as given, so a write that shares the reporter's dicts would change
        return True


def test_subjects_come_only_from_whitelisted_arguments():
    subject = turn_progress.subject
    assert subject("geocode_place", {"text": "  72nd and\nWest End "}) == "72nd and West End"
    assert subject("find_places", {"query": 'Strokes "music venue"', "lat": 40.7}) == 'Strokes "music venue"'
    assert subject("get_transit_arrivals", {"line": "1", "station": "72 St"}) == "1 at 72 St"
    assert subject("research_place", {"place_id": "wiki:123"}) is None
    assert subject("research_place", {"place_id": "wiki:123"}, tool_ok({"evidence": {"name": "Beacon Theatre"}})) \
        == "Beacon Theatre"
    assert subject("evaluate_adventure_plan", {"draft": {"story": {"solution": "SECRET"}}}) is None
    assert subject("update_adventure_state", {"operation": "complete_checkpoint", "note": "SECRET"}) is None
    assert subject("geocode_place", {"text": "x" * 200}) == "x" * turn_progress.SUBJECT_CHARS


def test_a_plan_check_that_does_not_pass_is_a_failed_step():
    outcome = turn_progress.outcome
    assert outcome("evaluate_adventure_plan", tool_ok({"passes": False})) == "failed"
    assert outcome("evaluate_adventure_plan", tool_ok({"passes": True})) == "ok"
    assert outcome("geocode_place", {"ok": False, "error": {"code": "NO_MATCH"}}) == "failed"
    assert outcome("geocode_place", tool_ok({})) == "ok"


def test_the_writer_writes_a_snapshot_not_the_live_steps():
    store = RecordingStore()
    reporter = turn_progress.ProgressReporter(store, "s", "m", "t")
    reporter.started(0, "geocode_place", {"text": "here"})
    reporter.flush()
    reporter.finished(0, tool_ok({}))
    assert store.writes[0]["steps"][0]["status"] == "running"  # What was written stays as written
    reporter.flush()
    assert store.writes[1]["steps"][0]["status"] == "ok"
    reporter.flush()
    assert len(store.writes) == 2  # Nothing changed, nothing written


def test_hooks_from_many_threads_lose_no_steps(monkeypatch):
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda hook_args: errors.append(hook_args.exc_value))
    store = RecordingStore()
    reporter = turn_progress.ProgressReporter(store, "s", "m", "t", interval=0.0001).start()

    def worker(k):
        for j in range(100):
            i = k * 100 + j
            reporter.started(i, "find_places", {"query": f"q{i}"}, True)
            reporter.finished(i, tool_ok({}))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    reporter.stop()

    steps = reporter.snapshot()["steps"]
    assert [s["i"] for s in steps] == list(range(800)) and {s["status"] for s in steps} == {"ok"}
    assert errors == []  # The writer never saw the steps change under it
    reporter.flush()
    assert store.writes[-1]["steps"] == steps


# --- GET /progress during a real turn ---


def planning_turn(monkeypatch):
    """A turn with a round in order (a lookup and the plan check) and then three lookups at once, whose
    research_place calls each wait for their own gate, so the test decides the order they finish in."""
    gates = {f"wiki:{n}": threading.Event() for n in (1, 2, 3)}
    names = {"wiki:1": "Beacon Theatre", "wiki:2": "Pythian Temple", "wiki:3": "The Dakota"}

    def run_tool(name, args, ctx=None):
        if name == "research_place":
            assert gates[args["place_id"]].wait(5)
            return tool_ok({"evidence": {"name": names[args["place_id"]]}})
        if name == "evaluate_adventure_plan":
            return tool_ok({"passes": False})
        return tool_ok({"point": {"lat": 40.78, "lng": -73.98}})

    monkeypatch.setattr(app_module, "run_tool", run_tool)
    script(monkeypatch,
           FakeMessage(content=None, tool_calls=[
               tool_call("c1", "geocode_place", '{"text": "72nd and West End"}'),
               tool_call("c2", "evaluate_adventure_plan", '{"draft": {"story": {"solution": "SECRET-SOLUTION"}}}')]),
           FakeMessage(content=None, tool_calls=[
               tool_call(f"r{n}", "research_place", f'{{"place_id": "wiki:{n}"}}') for n in (1, 2, 3)]),
           FakeMessage(content="Here's your case.", tool_calls=None))
    return gates


def test_progress_shows_each_step_as_it_runs_in_trace_order(client, store, monkeypatch):
    gates = planning_turn(monkeypatch)
    replies = []
    turn = threading.Thread(target=lambda: replies.append(client.post(
        "/chat", json={"message": "plan", "session_id": "p", "client_message_id": "m1"}).json()))
    turn.start()

    body = wait_for(lambda: (b := poll(client, "p", "m1"))["state"] == "running" and len(b["steps"]) == 5 and b)
    steps = body["steps"]
    assert [s["i"] for s in steps] == [0, 1, 2, 3, 4]
    assert [s["name"] for s in steps] == ["geocode_place", "evaluate_adventure_plan"] + ["research_place"] * 3
    assert [s["round"] for s in steps] == [1, 1, 2, 2, 2]
    assert [s["parallel"] for s in steps] == [False, False, True, True, True]
    assert [s["status"] for s in steps] == ["ok", "failed", "running", "running", "running"]
    assert steps[0]["subject"] == "72nd and West End" and steps[1]["subject"] is None
    assert body["phase"] == "tools" and body["started_at"]
    assert "SECRET" not in str(body)

    # The last lookup finishes first; its step resolves on its own, named from its result.
    gates["wiki:3"].set()
    steps = wait_for(lambda: (s := poll(client, "p", "m1")["steps"])[4]["status"] == "ok" and s)
    assert [s["status"] for s in steps[2:]] == ["running", "running", "ok"] and steps[4]["subject"] == "The Dakota"

    # A reloaded page (a new client) polling the same ids sees the same steps.
    assert TestClient(app_module.app).get("/progress", params={"session_id": "p", "client_message_id": "m1"}) \
        .json()["steps"] == steps

    gates["wiki:1"].set()
    gates["wiki:2"].set()
    turn.join(5)
    [reply] = replies
    assert [c["name"] for c in reply["tool_calls"]] == [s["name"] for s in steps]  # Step i is tool_calls[i]
    assert poll(client, "p", "m1") == {"session_id": "p", "client_message_id": "m1", "state": "done",
                                       "started_at": None, "phase": None, "steps": []}


def test_polling_a_running_turn_reads_only_its_claim(client, store, monkeypatch):
    gates = planning_turn(monkeypatch)
    turn = threading.Thread(target=lambda: client.post(
        "/chat", json={"message": "plan", "session_id": "p", "client_message_id": "m1"}))
    turn.start()
    wait_for(lambda: len(poll(client, "p", "m1")["steps"]) == 5)

    loads = store.loads
    for _ in range(5):
        assert poll(client, "p", "m1")["state"] == "running"
    assert store.loads == loads  # The session record, up to 800 KB, is never read while the turn runs

    for gate in gates.values():
        gate.set()
    turn.join(5)
    loads = store.loads
    assert poll(client, "p", "m1")["state"] == "done"
    assert store.loads == loads + 1  # Only without a live claim, to tell done from unknown


def test_an_expired_claim_reads_as_unknown_or_done_never_running(client, store):
    past = state.utc_now() - LATE
    token = store.claim("s1", "m1", past)
    store.set_progress("s1", "m1", token, {"phase": "tools", "steps": [{"i": 0, "status": "running"}]})

    assert poll(client, "s1", "m1") | {} == {"session_id": "s1", "client_message_id": "m1", "state": "unknown",
                                            "started_at": None, "phase": None, "steps": []}
    record = SessionRecord.new("s1")
    record.remember_reply("m1", {"response": "Saved", "session_id": "s1", "tool_calls": []})
    store.save(record)
    assert poll(client, "s1", "m1")["state"] == "done"


def test_a_message_not_yet_claimed_is_unknown_and_bad_ids_are_refused(client):
    assert poll(client, "fresh", "never-sent")["state"] == "unknown"
    assert client.get("/progress", params={"session_id": "../x", "client_message_id": "m"}).status_code == 400
    assert client.get("/progress", params={"session_id": "s", "client_message_id": ""}).status_code == 400


class Exploding:
    """A reporter whose every hook raises."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def __getattr__(self, name):
        raise RuntimeError("progress broke")


@pytest.mark.parametrize("reporter", [Exploding, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no thread"))])
def test_a_broken_progress_reporter_never_fails_the_turn(client, monkeypatch, reporter):
    monkeypatch.setattr(app_module, "ProgressReporter", reporter)
    monkeypatch.setitem(tools.TOOL_MAP, "get_weather", lambda location: tool_ok({"temp_f": 70}))
    script(monkeypatch,
           FakeMessage(content=None, tool_calls=[tool_call("c1", "get_weather", '{"location": "NYC"}'),
                                                 tool_call("c2", "get_weather", '{"location": "LA"}')]),
           FakeMessage(content="Nice out.", tool_calls=None))
    body = client.post("/chat", json={"message": "walk?", "session_id": "b", "client_message_id": "m"}).json()
    assert body["response"] == "Nice out." and len(body["tool_calls"]) == 2


def test_no_progress_write_lands_after_the_claim_is_released(client, store, monkeypatch):
    store.write_delay = 0.03
    monkeypatch.setattr(app_module, "ProgressReporter", functools.partial(turn_progress.ProgressReporter, interval=0.02))
    monkeypatch.setitem(tools.TOOL_MAP, "get_weather", lambda location: tool_ok({"temp_f": 70}))
    script(monkeypatch, FakeMessage(content=None, tool_calls=[tool_call("c1", "get_weather", '{"location": "NYC"}')]),
           FakeMessage(content="Nice out.", tool_calls=None))
    client.post("/chat", json={"message": "walk?", "session_id": "r", "client_message_id": "m"})
    time.sleep(0.1)
    assert store.events[-1] == "release"


def test_no_progress_write_lands_after_release_when_the_turn_raises(store, monkeypatch):
    store.write_delay = 0.03
    monkeypatch.setattr(app_module, "ProgressReporter", functools.partial(turn_progress.ProgressReporter, interval=0.02))

    def failing_turn(record, request, progress=None):
        app_module.report(progress, "started", 0, "geocode_place", {"text": "here"})
        time.sleep(0.05)
        app_module.report(progress, "finished", 0, tool_ok({}))  # A change just before the crash
        raise RuntimeError("turn crashed")

    monkeypatch.setattr(app_module, "run_turn", failing_turn)
    response = TestClient(app_module.app, raise_server_exceptions=False).post(
        "/chat", json={"message": "walk?", "session_id": "e", "client_message_id": "m"})
    time.sleep(0.1)
    assert response.status_code == 500
    assert store.events[-1] == "release" and "progress" in store.events


def test_chat_is_unchanged_with_and_without_a_client_message_id(client, store, monkeypatch):
    monkeypatch.setitem(tools.TOOL_MAP, "get_weather", lambda location: tool_ok({"temp_f": 70}))
    for extra in ({}, {"client_message_id": "m-shape"}):
        script(monkeypatch, FakeMessage(content=None, tool_calls=[tool_call("c1", "get_weather", '{"location": "NYC"}')]),
               FakeMessage(content="Hi.", tool_calls=None))
        body = client.post("/chat", json={"message": "hi", "session_id": "shape", **extra}).json()
        assert set(body) == {"response", "session_id", "tool_calls"}
        assert body["response"] == "Hi." and body["session_id"] == "shape"
        assert [set(c) for c in body["tool_calls"]] == [{"name", "args", "result"}]
        if not extra:
            assert "progress" not in store.events  # Without a claim there is nothing to write progress to

    store.claim("shape", "busy", state.utc_now())
    assert client.post("/chat", json={"message": "x", "session_id": "shape", "client_message_id": "busy"}) \
        .status_code == 409


# --- GET /adventure ---

SECRETS = ["SOLUTION-TEXT", "HINT-TEXT", "ANSWER-RULE", "PUZZLE-ANSWER", "PROMPT-TEXT", "Viktor Hale"]


def board_session(store, status, current, done=(), revealed=()):
    fixture = load_scenario("constrained_route")
    plan = fixture.plan
    story = plan.story.model_copy(update={
        "briefing": "You're a tape tracker. Mara Quill radios you at each stop.",
        "cast": [Character(name="Mara Quill", role="handler", contact="radio"),
                 Character(name="Viktor Hale", role="rival", contact="phone", introduced_in="stop_2")],
        "beats": [b.model_copy(update={"clue": f"CLUE-{b.beat_id}"}) for b in plan.story.beats],
        "solution": "SOLUTION-TEXT",
    })
    secret = {"hints": ["HINT-TEXT"], "answer_rule": "ANSWER-RULE", "solution": "PUZZLE-ANSWER", "prompt": "PROMPT-TEXT"}
    checkpoints = [c.model_copy(update={"activity": c.activity.model_copy(update=secret)}) for c in plan.checkpoints]
    plan = plan.model_copy(update={"story": story, "checkpoints": checkpoints})
    record = SessionRecord.new("board")
    record.plans[plan.plan_id] = plan
    record.adventure = AdventureState(status=status, active_plan_id=plan.plan_id, current_checkpoint_id=current,
                                      completed_ids=list(done), revealed_beat_ids=list(revealed))
    record.photos["a1"] = PhotoAsset(asset_id="a1", media_url="/media/a1", camera_id="cam",
                                     checkpoint_id="fixture_cam_cp_1", byte_size=10, retrieved_at=state.utc_now(),
                                     provenance="live_capture")
    store.save(record)


def test_the_board_names_only_reached_stops_and_revealed_clues(client, store):
    board_session(store, "active", "stop_2", done=["stop_1"], revealed=["beat_1"])
    response = client.get("/adventure", params={"session_id": "board"})
    body = response.json()

    assert body["status"] == "active" and body["solution"] is None
    assert body["briefing"].startswith("You're a tape tracker")
    assert body["handler"] == {"name": "Mara Quill", "contact": "radio"}
    assert [(s["n"], s["status"], s["camera"]) for s in body["stops"]] == [(1, "done", False), (2, "current", False),
                                                                          (3, "locked", True)]
    assert body["stops"][1]["name"] == "Fixture Facade (synthetic)"
    assert body["stops"][2]["name"] is None and body["stops"][2]["address"] is None
    assert body["clues"] == [{"text": "CLUE-beat_1", "stop": 1}]
    assert body["photos"] == [{"media_url": "/media/a1", "visibility": "unconfirmed", "stop": 3}]
    assert body["destination"] == "West 72nd Street and Broadway" and body["estimated_minutes"] == 40
    for secret in SECRETS + ["CLUE-beat_2", "CLUE-beat_3", "Fixture camera corner"]:
        assert secret not in response.text


def test_a_proposed_plan_names_only_its_first_stop(client, store):
    board_session(store, "proposed", "stop_1")
    body = client.get("/adventure", params={"session_id": "board"}).json()
    assert [(s["status"], bool(s["name"])) for s in body["stops"]] == [("current", True), ("locked", False),
                                                                      ("locked", False)]
    assert body["solution"] is None and body["clues"] == []


@pytest.mark.parametrize("status, current, done", [("completed", None, ["stop_1", "stop_2", "stop_3"]),
                                                   ("abandoned", "stop_2", ["stop_1"])])
def test_an_adventure_that_is_over_shows_its_solution(client, store, status, current, done):
    board_session(store, status, current, done=done, revealed=["beat_1"])
    body = client.get("/adventure", params={"session_id": "board"}).json()
    assert body["status"] == status and body["solution"] == "SOLUTION-TEXT"
    if status == "abandoned":  # Stops never reached stay locked
        assert [s["status"] for s in body["stops"]] == ["done", "current", "locked"]
        assert body["stops"][2]["name"] is None
    for secret in SECRETS[1:]:
        assert secret not in str(body)


def test_the_board_is_idle_without_a_plan_and_404_without_a_session(client, store):
    store.save(SessionRecord.new("empty"))
    assert client.get("/adventure", params={"session_id": "empty"}).json() == {"status": "idle"}
    assert client.get("/adventure", params={"session_id": "nobody"}).status_code == 404
    assert client.get("/adventure", params={"session_id": "../x"}).status_code == 400
