"""The /chat trace contract, with the model replaced by scripted replies."""

import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import state
import tools
from fakes import FakeMessage, script, tool_call


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
