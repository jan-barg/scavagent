"""The /chat trace contract, with the model replaced by scripted replies."""

import json

import litellm
import pytest
from fastapi.testclient import TestClient

import app as app_module
import state
import tools
from fakes import FakeMessage, reply, script, tool_call


@pytest.fixture
def client(monkeypatch):
    fake_weather = lambda location: tools.tool_ok({"location": location, "temp_f": 70})
    monkeypatch.setitem(tools.TOOL_MAP, "get_weather", fake_weather)
    monkeypatch.setattr(app_module, "store", state.MemoryStore())
    return TestClient(app_module.app)


def test_trace_keeps_result_objects_and_model_gets_json(client, monkeypatch):
    seen = script(
        monkeypatch,
        FakeMessage(content=None, tool_calls=[tool_call("c1", "get_weather", '{"location": "New York"}')]),
        FakeMessage(content="Nice out.", tool_calls=None),
    )
    body = client.post("/chat", json={"message": "walk?"}).json()

    assert set(body) == {"response", "session_id", "tool_calls"}
    assert body["response"] == "Nice out."
    [call] = body["tool_calls"]
    assert call["name"] == "get_weather" and call["args"] == {"location": "New York"}
    assert call["result"]["ok"] is True and call["result"]["data"]["temp_f"] == 70
    tool_message = seen[1][-1]
    assert tool_message["role"] == "tool" and json.loads(tool_message["content"]) == call["result"]


def test_later_model_failure_keeps_earlier_tool_trace(client, monkeypatch):
    script(
        monkeypatch,
        FakeMessage(content=None, tool_calls=[tool_call("c1", "get_weather", '{"location": "NYC"}')]),
        RuntimeError("quota exceeded for key AIza-not-a-real-key"),
    )
    body = client.post("/chat", json={"message": "walk?"}).json()

    assert body["response"].startswith("Model call failed (RuntimeError)")
    assert [c["name"] for c in body["tool_calls"]] == ["get_weather"]
    # Provider error text can carry request details; it stays out of the reply and the saved chat.
    history = client.get("/history", params={"session_id": body["session_id"]}).text
    assert "AIza" not in body["response"] and "AIza" not in history


def test_a_turn_starts_no_model_call_or_tool_after_its_deadline(client, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])
    timeouts, ran = [], []

    def slow_model(**kwargs):  # Each call takes 100 s and asks for two tools
        timeouts.append(kwargs["timeout"])
        clock[0] += 100
        n = len(timeouts)
        return reply(FakeMessage(content=None, tool_calls=[
            tool_call(f"a{n}", "get_weather", '{"location": "NYC"}'), tool_call(f"b{n}", "get_weather", '{"location": "NYC"}')]))

    def slow_tool(location):  # Each tool takes 30 s
        ran.append(clock[0])
        clock[0] += 30
        return tools.tool_ok({"location": location})

    monkeypatch.setattr(app_module.litellm, "completion", slow_model)
    monkeypatch.setitem(tools.TOOL_MAP, "get_weather", slow_tool)
    body = client.post("/chat", json={"message": "walk?"}).json()

    # Calls at 0 s and 160 s; the second returns at 260 s, past the 240 s deadline, so its tools never run.
    assert timeouts == [240, 80] and ran == [100, 130]
    assert [c["result"]["ok"] for c in body["tool_calls"]] == [True, True, False, False]
    assert "too long" in body["response"]


def test_model_context_never_starts_with_an_orphaned_tool_result():
    # One earlier turn used many tools, so the 40-message window would begin inside it.
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function"}]}
    messages = [{"role": "user", "content": "u0"}, call, *[{"role": "tool", "tool_call_id": "c", "content": "{}"}] * 43]
    messages += [{"role": "assistant", "content": "a0"}, {"role": "user", "content": "u1"}]

    context = app_module.recent(messages)
    assert context[0] == {"role": "user", "content": "u0"}  # Tool results keep the call they answer
    assert context[-1] == {"role": "user", "content": "u1"}


def rate_limited():
    return litellm.RateLimitError(message="429 Resource exhausted", llm_provider="vertex_ai", model="test")


@pytest.fixture
def fake_clock(monkeypatch):
    """Turn time that only moves when the test (or a wait) moves it; returns the waits taken."""
    clock, waits = [0.0], []
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module, "sleep", wait)
    return clock, waits


def test_a_rate_limited_model_call_is_retried(client, monkeypatch, fake_clock):
    script(monkeypatch, rate_limited(), rate_limited(), FakeMessage(content="Here now.", tool_calls=None))
    assert client.post("/chat", json={"message": "hi"}).json()["response"] == "Here now."
    assert fake_clock[1] == [2, 4]


def test_a_rate_limit_that_outlasts_the_turn_is_reported_without_waiting_past_the_deadline(client, monkeypatch, fake_clock):
    clock, waits = fake_clock

    def refused_after_100s(**kwargs):
        clock[0] += 100
        raise rate_limited()

    monkeypatch.setattr(app_module.litellm, "completion", refused_after_100s)
    body = client.post("/chat", json={"message": "hi"}).json()

    # Refused at 100 s and 202 s; the third try ends at 306 s, past 240 s, so no 8 s wait follows.
    assert waits == [2, 4] and body["response"].startswith("Model call failed (RateLimitError)")


def test_an_empty_model_reply_is_asked_for_once_more(client, monkeypatch):
    seen = script(monkeypatch, FakeMessage(content=None, tool_calls=None), FakeMessage(content="Here.", tool_calls=None))
    assert client.post("/chat", json={"message": "hi"}).json()["response"] == "Here."
    assert len(seen) == 2 and seen[0] == seen[1]  # The empty reply was not added to the conversation

    script(monkeypatch, FakeMessage(content="", tool_calls=None), FakeMessage(content=None, tool_calls=None))
    assert "didn't get an answer" in client.post("/chat", json={"message": "hi"}).json()["response"]


def test_a_turn_that_runs_out_of_tool_rounds_still_answers(client, monkeypatch):
    # A live run spent its last round saving a plan, and the user got the limit message instead of the briefing.
    calls = []

    def busy_model(**kwargs):
        calls.append(kwargs.get("tool_choice"))
        if kwargs.get("tool_choice") == "none":
            return reply(FakeMessage(content="Your briefing: the case of the missing tape.", tool_calls=None))
        return reply(FakeMessage(content=None, tool_calls=[tool_call(f"c{len(calls)}", "get_weather", '{"location": "NYC"}')]))

    monkeypatch.setattr(app_module.litellm, "completion", busy_model)
    body = client.post("/chat", json={"message": "plan something"}).json()

    assert body["response"] == "Your briefing: the case of the missing tape."
    assert calls == [None] * app_module.MAX_TOOL_ROUNDS + ["none"]  # One last call, with tools switched off
    assert len(body["tool_calls"]) == app_module.MAX_TOOL_ROUNDS


def test_bad_tool_calls_become_actionable_failures(client, monkeypatch):
    script(
        monkeypatch,
        FakeMessage(
            content=None,
            tool_calls=[
                tool_call("c1", "get_weather", "{not json"),
                tool_call("c2", "teleport", "{}"),
                tool_call("c3", "get_weather", '{"city": "NYC"}'),
            ],
        ),
        FakeMessage(content="Sorry.", tool_calls=None),
    )
    calls = client.post("/chat", json={"message": "x"}).json()["tool_calls"]

    assert [c["result"]["error"]["code"] for c in calls] == ["INVALID_ARGUMENT", "UNKNOWN_TOOL", "INVALID_ARGUMENT"]
    assert calls[0]["args"] == {"_unparsed": "{not json"}
    assert all(c["result"]["error"]["next_step"] for c in calls)


def test_sessions_are_separate(client, monkeypatch):
    seen = script(
        monkeypatch,
        FakeMessage(content="one", tool_calls=None),
        FakeMessage(content="two", tool_calls=None),
    )
    a = client.post("/chat", json={"message": "I am A"}).json()["session_id"]
    b = client.post("/chat", json={"message": "I am B"}).json()["session_id"]

    assert a != b
    assert "I am A" not in json.dumps(seen[1])
