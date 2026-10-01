"""Model configuration and what each model is sent back: Claude, open models on Vertex, and Gemini."""

import json

import httpx
import litellm
import pytest
from fastapi.testclient import TestClient
from litellm.llms.vertex_ai.vertex_llm_base import VertexBase

import app as app_module
import state
import tools
from fakes import FakeMessage, reply, script, tool_call

THINKING = [{"type": "thinking", "thinking": "", "signature": "sig-1"}]


@pytest.fixture(autouse=True)
def no_model_skipped(monkeypatch):
    monkeypatch.setattr(app_module, "_skip_until", {})  # A model that failed in one test stays usable in the next


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(tools.TOOL_MAP, "fake_lookup", lambda location: tools.tool_ok({"temp_f": 70}))
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


def test_the_server_context_rides_with_the_newest_message_and_is_never_stored(client, monkeypatch):
    monkeypatch.setattr(app_module, "MODEL", "vertex_ai/gemini-3.5-flash-lite")
    seen = script(monkeypatch, FakeMessage(content="Hi.", tool_calls=None), FakeMessage(content="Again.", tool_calls=None))
    session = client.post("/chat", json={"message": "hi"}).json()["session_id"]
    client.post("/chat", json={"message": "and now?", "session_id": session})

    assert seen[0][0] == {"role": "system", "content": app_module.SYSTEM_PROMPT}  # The same every turn
    context, text = seen[1][-1]["content"]
    assert context["text"].startswith("App context (from the server, not the user)") and text["text"] == "and now?"
    assert seen[1][1] == {"role": "user", "content": "hi"}  # Earlier messages go back without their context
    assert "App context" not in json.dumps(app_module.store.load(session).messages)


def test_everything_before_the_newest_message_is_unchanged_since_the_last_turn(client, monkeypatch):
    # What lets the provider serve the conversation so far from its cache.
    seen = script(
        monkeypatch,
        FakeMessage(content=None, thinking_blocks=THINKING, tool_calls=[tool_call("c1", "fake_lookup", '{"location": "NYC"}')]),
        FakeMessage(content="Sunny.", tool_calls=None),
        FakeMessage(content="Still sunny.", tool_calls=None),
        FakeMessage(content="Yes.", tool_calls=None),
    )
    session = client.post("/chat", json={"message": "weather?"}).json()["session_id"]
    client.post("/chat", json={"message": "now?", "session_id": session})
    client.post("/chat", json={"message": "sure?", "session_id": session})

    second, third = seen[2], seen[3]
    assert third[:len(second) - 1] == second[:-1]


def test_every_model_call_carries_the_model_options(client, monkeypatch):
    monkeypatch.setattr(app_module, "MODEL_OPTIONS", {"reasoning_effort": "medium", "max_tokens": 32_000})
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return reply(FakeMessage(content=None, tool_calls=[tool_call("c1", "fake_lookup", '{"location": "NYC"}')]))
        return reply(FakeMessage(content="Sunny.", tool_calls=None))

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    client.post("/chat", json={"message": "weather?"})

    assert [(c["reasoning_effort"], c["max_tokens"]) for c in calls] == [("medium", 32_000)] * 2
    assert "tool_choice" not in calls[0]  # Claude 5.5 rejects forced tool use; the default is auto


def test_reasoning_is_replayed_within_its_turn_and_never_stored(client, monkeypatch):
    seen = script(
        monkeypatch,
        FakeMessage(content=None, thinking_blocks=THINKING, reasoning_content="checking weather",
                    tool_calls=[tool_call("c1", "fake_lookup", '{"location": "NYC"}')]),
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
        FakeMessage(content=None, tool_calls=[tool_call("c1", "fake_lookup", '{"location": "NYC"}')]),
        FakeMessage(content=None, tool_calls=None), FakeMessage(content=None, tool_calls=None),  # Last call, tools off
        FakeMessage(content="Here you go.", tool_calls=None),
    )
    first = client.post("/chat", json={"message": "weather?"}).json()
    client.post("/chat", json={"message": "well?", "session_id": first["session_id"]})

    assert "tool-call limit" in first["response"]
    # The next turn sees the tool result, then what the user was told, then their new message.
    assert [m["role"] for m in seen[3][1:]] == ["user", "assistant", "tool", "assistant", "user"]
    assert seen[3][-2] == {"role": "assistant", "content": first["response"]}


def test_an_empty_reply_is_not_stored_but_the_fallback_the_user_saw_is(client, monkeypatch):
    script(monkeypatch, FakeMessage(content=None, tool_calls=None), FakeMessage(content="", tool_calls=None))
    body = client.post("/chat", json={"message": "hi"}).json()

    assert app_module.store.load(body["session_id"]).messages == [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": body["response"]}]


def test_a_failed_model_call_is_stored_with_its_error_reply(client, monkeypatch):
    script(monkeypatch, RuntimeError("boom"))
    body = client.post("/chat", json={"message": "hi"}).json()

    assert app_module.store.load(body["session_id"]).messages[-1] == {"role": "assistant", "content": body["response"]}


def test_a_save_during_the_turn_does_not_change_what_the_model_already_saw(client, monkeypatch):
    # A photo save trims the record, which can compact stored tool arguments in place. The next model call in
    # the same turn must still get the conversation its thinking blocks were made with.
    record = state.SessionRecord.new("s")
    record.messages = [
        {"role": "user", "content": "plan"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "e1", "type": "function", "function": {"name": "evaluate_adventure_plan", "arguments": "x" * 5000}}]},
        {"role": "tool", "tool_call_id": "e1", "content": "{}"}, {"role": "assistant", "content": "Planned."}]
    app_module.store.save(record)
    monkeypatch.setattr(state, "COMPACT_BYTES", 100)
    monkeypatch.setitem(tools.TOOL_MAP, "get_adventure_state", lambda ctx: ctx.record._compact_tool_payloads() and tools.tool_ok({}))
    seen = script(
        monkeypatch,
        FakeMessage(content=None, thinking_blocks=THINKING, tool_calls=[tool_call("c1", "get_adventure_state", "{}")]),
        FakeMessage(content="Onward.", tool_calls=None),
    )
    client.post("/chat", json={"message": "next?", "session_id": "s"})

    assert seen[1][:len(seen[0])] == seen[0]


# --- Claude through LiteLLM's real Vertex transformation; only the HTTP transport is faked ---


def anthropic_reply(*content, stop_reason="end_turn"):
    return {"id": "msg", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": list(content),
            "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}}


@pytest.fixture
def vertex_claude(monkeypatch):
    """Configure Claude Opus 5.5 on Vertex; returns (queue of (status, body) replies, list of request bodies sent).

    No fallback model, so these tests see Claude's own retries and errors.
    """
    monkeypatch.setattr(app_module, "FALLBACK_MODEL", None)
    monkeypatch.setattr(app_module, "REASONING_EFFORT", None)
    monkeypatch.setattr(app_module, "MODEL", "vertex_ai/claude-opus-5-5")
    monkeypatch.setattr(app_module, "MODEL_OPTIONS", app_module.model_options("vertex_ai/claude-opus-5-5"))
    monkeypatch.setattr(app_module, "sleep", lambda seconds: None)
    monkeypatch.setattr(VertexBase, "_ensure_access_token", lambda self, **kw: ("test-token", "test-project"))
    replies, sent = [], []
    real_send = httpx.Client.send

    def send(self, request, **kwargs):
        if request.url.host == "testserver":  # The test's own client
            return real_send(self, request, **kwargs)
        sent.append(json.loads(request.content))
        status, body = replies.pop(0)
        return httpx.Response(status, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return replies, sent


def test_claude_requests_adaptive_thinking_and_replays_it_only_within_the_turn(client, vertex_claude):
    replies, sent = vertex_claude
    thinking = {"type": "thinking", "thinking": "Weather first.", "signature": "SIG-TURN-1"}
    replies += [
        (200, anthropic_reply(thinking, {"type": "tool_use", "id": "toolu_1", "name": "fake_lookup",
                                         "input": {"location": "NYC"}}, stop_reason="tool_use")),
        (200, anthropic_reply({"type": "thinking", "thinking": "", "signature": "SIG-TURN-1B"},
                              {"type": "text", "text": "Sunny."})),
        (200, anthropic_reply({"type": "text", "text": "Still sunny."})),
    ]
    session = client.post("/chat", json={"message": "weather?"}).json()["session_id"]
    assert client.post("/chat", json={"message": "now?", "session_id": session}).json()["response"] == "Still sunny."

    first = sent[0]
    assert first["thinking"]["type"] == "adaptive" and first["output_config"] == {"effort": "medium"}
    assert first["max_tokens"] == 32_000 and first["cache_control"] == {"type": "ephemeral"}
    assert "tool_choice" not in first
    # The instructions are cached for an hour; this turn's server context comes with the user's message.
    [instructions] = first["system"]
    assert instructions["text"] == app_module.SYSTEM_PROMPT
    assert instructions["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert [b["text"][:11] for b in first["messages"][0]["content"]] == ["App context", "weather?"]
    # The tool round goes back with its thinking block ahead of the tool call, as Claude requires.
    assert [b["type"] for b in sent[1]["messages"][1]["content"]] == ["thinking", "tool_use"]
    assert sent[1]["messages"][1]["content"][0]["signature"] == "SIG-TURN-1"
    # Neither the next turn nor storage carries any of it.
    assert "SIG-TURN" not in json.dumps(sent[2]["messages"])
    assert "SIG-TURN" not in app_module.store.load(session).model_dump_json()


def test_an_overloaded_claude_is_asked_again(client, vertex_claude):
    replies, sent = vertex_claude
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    replies += [(529, overloaded), (500, {"type": "error", "error": {"type": "api_error", "message": "Internal"}}),
                (200, anthropic_reply({"type": "text", "text": "Back."}))]
    assert client.post("/chat", json={"message": "hi"}).json()["response"] == "Back."
    assert len(sent) == 3


def test_claude_without_quota_reports_a_rate_limit_after_retrying(client, vertex_claude, monkeypatch):
    replies, sent = vertex_claude
    monkeypatch.setattr(app_module, "RETRY_DELAYS", (1,))
    quota = {"error": {"code": 429, "message": "Quota exceeded for base model: anthropic-claude-opus",
                       "status": "RESOURCE_EXHAUSTED"}}
    replies += [(429, quota), (429, quota)]
    body = client.post("/chat", json={"message": "hi"}).json()
    assert body["response"].startswith("Model call failed (RateLimitError)") and len(sent) == 2


def test_a_rejected_claude_request_is_not_retried(client, vertex_claude):
    replies, sent = vertex_claude
    replies += [(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                  "message": "Invalid `signature` in `thinking` block."}})] * 3
    body = client.post("/chat", json={"message": "hi"}).json()
    assert body["response"].startswith("Model call failed") and len(sent) == 1


def test_malformed_tool_arguments_do_not_break_the_rest_of_the_conversation(client, monkeypatch):
    # Kimi once dropped a closing brace; Vertex then refused every later request that carried it.
    seen = script(
        monkeypatch,
        FakeMessage(content=None, tool_calls=[tool_call("c1", "fake_lookup", '{"location": "NYC"'),
                                              tool_call("c2", "fake_lookup", '["NYC"]')]),
        FakeMessage(content="Let me retry.", tool_calls=None),
        FakeMessage(content="Fine now.", tool_calls=None),
    )
    first = client.post("/chat", json={"message": "weather?"}).json()
    client.post("/chat", json={"message": "and?", "session_id": first["session_id"]})

    assert [c["args"] for c in first["tool_calls"]] == [{"_unparsed": '{"location": "NYC"'}, {"_unparsed": '["NYC"]'}]
    for context in seen[1:]:
        sent = [call["function"]["arguments"] for m in context for call in m.get("tool_calls") or []]
        assert sent and all(isinstance(json.loads(a), dict) for a in sent)


def test_claude_answers_after_the_last_tool_round_with_tools_off(client, vertex_claude, monkeypatch):
    replies, sent = vertex_claude
    monkeypatch.setattr(app_module, "MAX_TOOL_ROUNDS", 1)
    replies += [
        (200, anthropic_reply({"type": "thinking", "thinking": "", "signature": "SIG"},
                              {"type": "tool_use", "id": "toolu_1", "name": "fake_lookup", "input": {"location": "NYC"}},
                              stop_reason="tool_use")),
        (200, anthropic_reply({"type": "text", "text": "Your briefing."})),
    ]
    assert client.post("/chat", json={"message": "plan"}).json()["response"] == "Your briefing."
    assert "tool_choice" not in sent[0] and sent[1]["tool_choice"] == {"type": "none"}  # Claude 5.5 accepts none


def test_claude_caches_the_history_before_each_of_the_last_two_user_messages(client, vertex_claude):
    replies, sent = vertex_claude
    replies += [(200, anthropic_reply({"type": "text", "text": f"Reply {n}."})) for n in range(3)]
    session = client.post("/chat", json={"message": "one"}).json()["session_id"]
    for text in ("two", "three"):
        client.post("/chat", json={"message": text, "session_id": session})

    hour = {"type": "ephemeral", "ttl": "1h"}
    marked = [(m["role"], b.get("text")) for m in sent[2]["messages"] if isinstance(m["content"], list)
              for b in m["content"] if b.get("cache_control") == hour]
    # Reply 0 closed the history the previous turn cached; Reply 1 closes what this turn adds for the next one.
    assert marked == [("assistant", "Reply 0."), ("assistant", "Reply 1.")]
    assert sent[2]["cache_control"] == {"type": "ephemeral"}  # Plus the automatic one for this turn's tool rounds
    assert "cache_control" not in json.dumps(app_module.store.load(session).messages)


# --- Falling back when Claude can't answer ---

CLAUDE, GEMINI = "anthropic/claude-sonnet-5-5", "vertex_ai/gemini-3.5-flash-lite"


def no_credit():
    return litellm.BadRequestError(message='AnthropicException - {"type":"error","error":{"type":"invalid_request_error",'
                                   '"message":"Your credit balance is too low to access the Anthropic API."}}',
                                   model="claude-sonnet-5-5", llm_provider="anthropic")


@pytest.fixture
def claude_with_fallback(client, monkeypatch):
    """Claude as the model with Gemini as the fallback; returns (Claude's queued outcomes, models called, kwargs)."""
    monkeypatch.setattr(app_module, "MODEL", CLAUDE)
    monkeypatch.setattr(app_module, "MODEL_OPTIONS", app_module.model_options(CLAUDE))
    monkeypatch.setattr(app_module, "FALLBACK_MODEL", GEMINI)
    monkeypatch.setattr(app_module, "sleep", lambda seconds: None)
    outcomes, calls = [], []

    def completion(**kwargs):
        calls.append({**kwargs, "messages": json.loads(json.dumps(kwargs["messages"]))})  # As sent at the time
        if kwargs["model"] == GEMINI:
            return reply(FakeMessage(content="Gemini here.", tool_calls=None))
        step = outcomes.pop(0)
        if isinstance(step, BaseException):
            raise step
        return reply(step)

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    return outcomes, calls


@pytest.mark.parametrize("error", [
    no_credit(),
    litellm.AuthenticationError(message="invalid x-api-key", model="claude", llm_provider="anthropic"),
    litellm.APIConnectionError(message="Connection refused", model="claude", llm_provider="anthropic"),
])
def test_claude_without_credit_or_a_working_key_or_connection_hands_the_turn_to_gemini(client, claude_with_fallback, error):
    outcomes, calls = claude_with_fallback
    outcomes += [error]
    body = client.post("/chat", json={"message": "hi"}).json()

    assert body["response"] == "Gemini here."
    assert [c["model"] for c in calls] == [CLAUDE, GEMINI]  # No retry for these
    assert calls[1]["messages"][0] == {"role": "system", "content": app_module.SYSTEM_PROMPT}  # Gemini's own format
    assert "reasoning_effort" not in calls[1] and "cache_control" not in calls[1]


def test_an_overloaded_claude_gets_one_quick_retry_before_gemini_answers(client, claude_with_fallback, monkeypatch):
    outcomes, calls = claude_with_fallback
    waits = []
    monkeypatch.setattr(app_module, "sleep", waits.append)
    overloaded = litellm.InternalServerError(message="529 overloaded_error", llm_provider="anthropic", model="claude")
    outcomes += [overloaded, overloaded]
    assert client.post("/chat", json={"message": "hi"}).json()["response"] == "Gemini here."
    assert [c["model"] for c in calls] == [CLAUDE, CLAUDE, GEMINI] and waits == [2]


def test_gemini_takes_the_next_turns_until_claude_has_had_a_rest(client, claude_with_fallback, monkeypatch):
    outcomes, calls = claude_with_fallback
    clock = [1000.0]
    monkeypatch.setattr(app_module, "monotonic", lambda: clock[0])
    outcomes += [no_credit(), FakeMessage(content="Claude is back.", tool_calls=None)]
    session = client.post("/chat", json={"message": "one"}).json()["session_id"]
    clock[0] += 60
    assert client.post("/chat", json={"message": "two", "session_id": session}).json()["response"] == "Gemini here."
    clock[0] += app_module.FALLBACK_COOLDOWN
    assert client.post("/chat", json={"message": "three", "session_id": session}).json()["response"] == "Claude is back."
    assert [c["model"] for c in calls] == [CLAUDE, GEMINI, GEMINI, CLAUDE]


def test_a_mid_turn_failure_restarts_the_turn_on_gemini_and_keeps_the_trace(client, claude_with_fallback, monkeypatch):
    outcomes, calls = claude_with_fallback
    monkeypatch.setitem(tools.TOOL_MAP, "fake_lookup", lambda location: tools.tool_ok({"temp_f": 70}))
    outcomes += [FakeMessage(content=None, thinking_blocks=THINKING, tool_calls=[tool_call("c1", "fake_lookup", '{"location": "NYC"}')]),
                 litellm.APIConnectionError(message="Connection reset", model="claude", llm_provider="anthropic")]
    body = client.post("/chat", json={"message": "weather?"}).json()

    assert body["response"] == "Gemini here." and [c["name"] for c in body["tool_calls"]] == ["fake_lookup"]
    gemini_view = calls[-1]["messages"]
    assert gemini_view[-1]["role"] == "user" and "SIG" not in json.dumps(gemini_view)  # From the user's message, no Claude reasoning


def test_other_claude_errors_are_reported_not_hidden_by_the_fallback(client, claude_with_fallback):
    outcomes, calls = claude_with_fallback
    outcomes += [litellm.BadRequestError(message="messages.1.content.0: Invalid `signature` in `thinking` block",
                                         model="claude", llm_provider="anthropic")]
    body = client.post("/chat", json={"message": "hi"}).json()
    assert body["response"].startswith("Model call failed (BadRequestError)") and [c["model"] for c in calls] == [CLAUDE]


def test_a_failing_fallback_is_reported(client, claude_with_fallback, monkeypatch):
    outcomes, calls = claude_with_fallback
    monkeypatch.setattr(app_module, "MODEL", GEMINI)  # Gemini is the model and the fallback: nothing to fall back to
    monkeypatch.setattr(app_module.litellm, "completion", lambda **kw: calls.append(kw) or (_ for _ in ()).throw(
        litellm.APIConnectionError(message="down", model="gemini", llm_provider="vertex_ai")))
    body = client.post("/chat", json={"message": "hi"}).json()
    assert body["response"].startswith("Model call failed (APIConnectionError)") and len(calls) == 1


def test_anthropics_real_credit_error_counts_as_claude_being_unusable(client, vertex_claude):
    replies, sent = vertex_claude
    replies += [(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                  "message": "Your credit balance is too low to access the Anthropic API. "
                                                             "Please go to Plans & Billing to upgrade or purchase credits."}})]
    with pytest.raises(Exception) as raised:
        app_module.complete([{"role": "user", "content": "hi"}], app_module.monotonic() + 60, model=app_module.MODEL)
    assert app_module.unusable(raised.value)
