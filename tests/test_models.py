"""Model configuration and what each model is sent back: Claude, open models on Vertex, and Gemini."""

import litellm
import pytest
from fastapi.testclient import TestClient

import app as app_module
import state
import tools
from fakes import FakeMessage, reply, script, tool_call

THINKING = [{"type": "thinking", "thinking": "", "signature": "sig-1"}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(tools.TOOL_MAP, "get_weather", lambda location: tools.tool_ok({"temp_f": 70}))
    monkeypatch.setattr(app_module, "store", state.MemoryStore())
    return TestClient(app_module.app)


def test_claude_gets_medium_effort_a_bounded_output_and_prompt_caching(monkeypatch):
    monkeypatch.setattr(app_module, "REASONING_EFFORT", None)
    for model in ("vertex_ai/claude-opus-5-5", "vertex_ai/claude-sonnet-5-5", "claude-opus-5-5"):
        assert app_module.model_options(model) == {
            "reasoning_effort": "medium", "max_tokens": 32_000, "cache_control": {"type": "ephemeral"}}
    # Other models keep their provider defaults: LiteLLM rejects a parameter a model does not take.
    for model in ("vertex_ai/gemini-3.5-flash-lite", "vertex_ai/moonshotai/kimi-k2-thinking-maas",
                  "vertex_ai/qwen/qwen3-235b-a22b-instruct-2507-maas", "vertex_ai/openai/gpt-oss-120b-maas"):
        assert app_module.model_options(model) == {}


def test_a_configured_effort_applies_to_any_model(monkeypatch):
    monkeypatch.setattr(app_module, "REASONING_EFFORT", "low")
    assert app_module.model_options("vertex_ai/claude-opus-5-5")["reasoning_effort"] == "low"
    assert app_module.model_options("vertex_ai/gemini-3.5-flash") == {"reasoning_effort": "low"}


def test_every_model_call_carries_the_model_options(client, monkeypatch):
    monkeypatch.setattr(app_module, "MODEL_OPTIONS", {"reasoning_effort": "medium", "max_tokens": 32_000})
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return reply(FakeMessage(content=None, tool_calls=[tool_call("c1", "get_weather", '{"location": "NYC"}')]))
        return reply(FakeMessage(content="Sunny.", tool_calls=None))

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    client.post("/chat", json={"message": "weather?"})

    assert [(c["reasoning_effort"], c["max_tokens"]) for c in calls] == [("medium", 32_000)] * 2
    assert "tool_choice" not in calls[0]  # Claude 5.5 rejects forced tool use; the default is auto


def test_reasoning_is_replayed_within_its_turn_and_never_stored(client, monkeypatch):
    seen = script(
        monkeypatch,
        FakeMessage(content=None, thinking_blocks=THINKING, reasoning_content="checking weather",
                    tool_calls=[tool_call("c1", "get_weather", '{"location": "NYC"}')]),
        FakeMessage(content="Sunny.", thinking_blocks=THINKING, tool_calls=None),
        FakeMessage(content="Still sunny.", tool_calls=None),
    )
    session = client.post("/chat", json={"message": "weather?"}).json()["session_id"]
    client.post("/chat", json={"message": "and now?", "session_id": session})

    # The tool round continues with its own thinking block, as Claude requires.
    assert seen[1][-2]["thinking_blocks"] == THINKING
    # The next turn has a new server context, so no earlier reasoning goes back to the model or into storage.
    assert not any(k in m for m in seen[2] for k in app_module.REASONING_FIELDS)
    assert not any(k in m for m in app_module.store.load(session).messages for k in app_module.REASONING_FIELDS)


def test_reasoning_already_stored_by_an_older_session_is_not_sent(client, monkeypatch):
    record = state.SessionRecord.new("old")
    record.messages = [{"role": "user", "content": "hi"},
                       {"role": "assistant", "content": "Hello.", "thinking_blocks": THINKING, "reasoning_content": "x"}]
    app_module.store.save(record)
    seen = script(monkeypatch, FakeMessage(content="Hi again.", tool_calls=None))
    client.post("/chat", json={"message": "hello?", "session_id": "old"})

    assert seen[0][2] == {"role": "assistant", "content": "Hello."}


def test_a_turn_cut_short_is_stored_with_the_reply_the_user_saw(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_TOOL_ROUNDS", 1)
    seen = script(
        monkeypatch,
        FakeMessage(content=None, tool_calls=[tool_call("c1", "get_weather", '{"location": "NYC"}')]),
        FakeMessage(content="Here you go.", tool_calls=None),
    )
    first = client.post("/chat", json={"message": "weather?"}).json()
    client.post("/chat", json={"message": "well?", "session_id": first["session_id"]})

    assert "tool-call limit" in first["response"]
    # The next turn sees the tool result, then what the user was told, then their new message.
    assert [m["role"] for m in seen[1][1:]] == ["user", "assistant", "tool", "assistant", "user"]
    assert seen[1][-2] == {"role": "assistant", "content": first["response"]}


def test_an_empty_reply_is_not_stored_but_the_fallback_the_user_saw_is(client, monkeypatch):
    script(monkeypatch, FakeMessage(content=None, tool_calls=None), FakeMessage(content="", tool_calls=None))
    body = client.post("/chat", json={"message": "hi"}).json()

    assert app_module.store.load(body["session_id"]).messages == [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": body["response"]}]


def test_a_failed_model_call_is_stored_with_its_error_reply(client, monkeypatch):
    script(monkeypatch, RuntimeError("boom"))
    body = client.post("/chat", json={"message": "hi"}).json()

    assert app_module.store.load(body["session_id"]).messages[-1] == {"role": "assistant", "content": body["response"]}


def test_an_overloaded_model_is_retried(client, monkeypatch):
    monkeypatch.setattr(app_module, "sleep", lambda seconds: None)
    overloaded = litellm.InternalServerError(message="529 overloaded_error", llm_provider="vertex_ai", model="claude")
    script(monkeypatch, overloaded, FakeMessage(content="Back.", tool_calls=None))
    assert client.post("/chat", json={"message": "hi"}).json()["response"] == "Back."
