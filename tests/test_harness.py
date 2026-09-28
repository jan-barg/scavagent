"""The /chat trace contract, with the model replaced by scripted replies."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import tools


class FakeMessage(SimpleNamespace):
    def model_dump(self):
        calls = None
        if self.tool_calls:
            calls = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in self.tool_calls
            ]
        return {"role": "assistant", "content": self.content, "tool_calls": calls}


def tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def script(monkeypatch, *steps):
    """Each step is a FakeMessage to return, or an exception to raise."""
    remaining = list(steps)
    seen = []

    def completion(**kwargs):
        seen.append(json.loads(json.dumps(kwargs["messages"])))  # Must be JSON-serializable
        step = remaining.pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(choices=[SimpleNamespace(message=step)])

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    return seen


@pytest.fixture
def client(monkeypatch):
    fake_weather = lambda location: tools.tool_ok({"location": location, "temp_f": 70})
    monkeypatch.setitem(tools.TOOL_MAP, "get_weather", fake_weather)
    app_module.sessions.clear()
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
        RuntimeError("quota exceeded"),
    )
    body = client.post("/chat", json={"message": "walk?"}).json()

    assert body["response"].startswith("Model call failed: RuntimeError")
    assert [c["name"] for c in body["tool_calls"]] == ["get_weather"]


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
