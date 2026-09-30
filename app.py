import copy
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from zoneinfo import ZoneInfo

import litellm
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

import state
from schemas import NYC_TIMEZONE, ChatRequest, ChatResponse, tool_error
from state import SessionRecord, ToolContext, VersionConflict
from agent import SYSTEM_PROMPT
from tools import SESSION_TOOLS, TOOL_MAP, TOOLS, run_tool

# --- Config ---

# The agent's instructions live in agent.py (Kyle's workstream); see docs/SHARED_FILE_EDITS.md.
# Planning a researched adventure takes more tool rounds than the starter's 8; TURN_SECONDS still bounds the turn.
MAX_TOOL_ROUNDS = 16
# A turn starts no model call or tool after this, and each model call gets only the time left. Tools bound
# their own time. Kept well under state.IN_FLIGHT_TIMEOUT, so an expired claim's turn is no longer working.
TURN_SECONDS = 240
# Waits before retrying a model call that Vertex rate-limited (429) or found unavailable (503), while the turn has time.
RETRY_DELAYS = (2, 4, 8, 16)
CONTEXT_MESSAGES = 40  # Recent conversation sent to the model (at least this much); the full plan stays in storage
# The window's start moves in steps of this many messages, not every turn, so the beginning of the conversation
# stays the same for several turns and the model provider can serve it from its cache.
CONTEXT_STEP = 20
HOUR_CACHE = {"type": "ephemeral", "ttl": "1h"}
# Model choice is deferred; keep it configurable. Defaults to the starter's model.
MODEL = os.environ.get("SCAVAGENT_MODEL", "vertex_ai/gemini-3.5-flash-lite")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")
# Optional reasoning effort (low, medium, high) for models that take one. Claude uses medium unless this is set.
REASONING_EFFORT = os.environ.get("SCAVAGENT_REASONING_EFFORT") or None
# Claude's output cap per call, thinking included. LiteLLM would otherwise ask for the model's 128K maximum.
CLAUDE_MAX_TOKENS = 32_000
# Reasoning a model returns alongside its reply (Claude's thinking blocks, Kimi's reasoning_content).
REASONING_FIELDS = ("thinking_blocks", "reasoning_content")


def is_claude(model: str) -> bool:
    """Claude on Vertex (vertex_ai/claude-...) or Anthropic's API (anthropic/claude-...)."""
    return model.split("/")[-1].startswith("claude")


def model_options(model: str) -> dict:
    """Extra litellm.completion arguments for the configured model."""
    options = {"reasoning_effort": REASONING_EFFORT} if REASONING_EFFORT else {}
    if is_claude(model):
        # Thinking is always on for Claude 5.5 models; effort is the control. Automatic prompt caching: every
        # tool round re-sends the turn so far, and the cached part costs a tenth.
        options = {"reasoning_effort": "medium", **options, "max_tokens": CLAUDE_MAX_TOKENS,
                   "cache_control": {"type": "ephemeral"}}
    return options


MODEL_OPTIONS = model_options(MODEL)
# When a model can't answer (no credit, a key it rejects, unreachable, or still overloaded after one quick retry), this
# one answers the turn instead, and turns skip the failed model for FALLBACK_COOLDOWN seconds. Empty turns it off.
FALLBACK_MODEL = os.environ.get("SCAVAGENT_FALLBACK_MODEL", "vertex_ai/gemini-3.5-flash-lite") or None
FALLBACK_COOLDOWN = 300
_skip_until: dict[str, float] = {}  # model -> monotonic time before which turns go straight to its fallback

# --- The Harness ---


def run_agent(messages: list[dict], tool_calls: list[dict], ctx: ToolContext | None = None,
              model: str | None = None) -> str:
    """Complete until the model answers without asking for a tool.

    Returns the final text. Every tool call is appended to `tool_calls` as it runs,
    so the caller keeps the trace even if a later model call raises. Session tools
    receive `ctx`, which the server binds to the current session. A model that can't answer (see
    `unusable`) hands the turn to its fallback, which redoes it from the user's message (tools that
    already ran stay in the trace and in the session's state).
    """
    model = model or MODEL
    turn_start = len(messages)
    deadline = monotonic() + TURN_SECONDS
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            reply = complete(messages, deadline, model=model)
        except Exception as error:
            fallback = fallback_for(model)
            if not fallback or not unusable(error):
                raise
            logger.warning("%s can't answer (%s: %s); %s answers for the next %d s", model, type(error).__name__,
                           str(error)[:300], fallback, FALLBACK_COOLDOWN)
            _skip_until[model] = monotonic() + FALLBACK_COOLDOWN
            del messages[turn_start:]
            messages[0] = system_message(fallback)
            model = fallback
            continue
        if reply is None:
            return "That took me too long, so I stopped. Please send your message again."

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        assistant = reply.model_dump()
        messages += [assistant]

        if not reply.tool_calls:
            return reply.content or "I didn't get an answer from the model. Please send that again."

        # The harness, not the model, runs each tool and appends the result
        early = lookups_at_once(reply.tool_calls) if monotonic() < deadline else {}
        for i, call in enumerate(reply.tool_calls):
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                # Vertex's OpenAI-compatible endpoint rejects every later request of a conversation that holds
                # malformed arguments, so the model's copy gets {} next to the error; the trace keeps what it sent.
                assistant["tool_calls"][i]["function"]["arguments"] = "{}"
            if i in early:
                result = early[i]  # Started with its round, before the deadline
            elif monotonic() >= deadline:
                result = tool_error(
                    "INTERNAL_ERROR",
                    "This turn ran out of time, so the tool was not run.",
                    retryable=True,
                    next_step="Tell the user it took too long and to send the message again.",
                )
            elif isinstance(args, dict):
                result = run_tool(call.function.name, args, ctx)
            else:
                result = tool_error(
                    "INVALID_ARGUMENT",
                    "Tool arguments were not a JSON object.",
                    retryable=False,
                    next_step="Call the tool again with a JSON object matching its schema.",
                )
                args = {"_unparsed": call.function.arguments}
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            # The trace keeps the result as an object; the model receives it as JSON text.
            messages += [{"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}]

    # Out of tool rounds: ask once more with tools off, so the user still gets an answer (for example the
    # briefing for a plan it just saved) instead of only the limit message.
    reply = complete(messages, deadline, tool_choice="none", model=model)
    if reply is not None and reply.content:
        messages += [{"role": "assistant", "content": reply.content}]  # Any tool call it still made is dropped
        return reply.content
    return "Sorry, I hit my tool-call limit before finishing."


def lookups_at_once(calls) -> dict[int, dict]:
    """Results by position for a round made only of lookups, run concurrently; {} for any other round.

    Lookups (every tool but the session tools) read public sources and never the session, so a
    round of them can run at once: three research_place calls took 16 s one after another (Kyle,
    September 29). A round with a session tool keeps running in order, one call at a time."""
    jobs = {}
    for i, call in enumerate(calls):
        name = call.function.name
        if name not in TOOL_MAP or name in SESSION_TOOLS:
            return {}
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            continue  # the loop reports it
        if isinstance(args, dict):
            jobs[i] = (name, args)
    if len(jobs) < 2:
        return {}
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="lookup") as pool:
        futures = {i: pool.submit(run_tool, name, args) for i, (name, args) in jobs.items()}
    return {i: future.result() for i, future in futures.items()}


def complete(messages: list[dict], deadline: float, tool_choice: str | None = None, model: str | None = None):
    """One model reply, or None once the turn is out of time.

    A rate-limited (429), overloaded or failed (500/529), or unavailable (503) call is retried after each wait
    in RETRY_DELAYS that still fits before the deadline (only the first wait when a fallback model can take
    over); after that the error propagates. A reply with neither text nor a tool call is asked for once more.
    """
    model = model or MODEL
    options = MODEL_OPTIONS if model == MODEL else model_options(model)
    waits, asked_again = list(RETRY_DELAYS[:1] if fallback_for(model) else RETRY_DELAYS), False
    while (remaining := deadline - monotonic()) > 0:
        try:
            reply = litellm.completion(
                model=model,
                vertex_location=VERTEX_LOCATION,
                messages=with_cache_points(messages) if is_claude(model) else messages,
                tools=TOOLS,
                timeout=remaining,
                **options,
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ).choices[0].message
        except Exception as error:
            if not retryable(error) or not waits or waits[0] >= deadline - monotonic():
                raise
            sleep(waits.pop(0))
            continue
        if reply.tool_calls or reply.content or asked_again:
            return reply
        asked_again = True
    return None


def retryable(error: Exception) -> bool:
    """Rate limited (429), failed (500), unavailable (503), or overloaded (529): worth asking again.

    LiteLLM reports Claude's 529 overloaded_error on Vertex as a plain APIError, so the status decides.
    """
    return isinstance(error, (litellm.RateLimitError, litellm.InternalServerError, litellm.ServiceUnavailableError)) or (
        isinstance(error, litellm.APIError) and getattr(error, "status_code", None) in (500, 503, 529))


def unusable(error: Exception) -> bool:
    """The model can't answer right now: no credit, a key it rejects, no access, unreachable, or overloaded."""
    if isinstance(error, (litellm.AuthenticationError, litellm.PermissionDeniedError, litellm.APIConnectionError)):
        return True
    if isinstance(error, litellm.BadRequestError) and "credit balance" in str(error).lower():
        return True  # Anthropic answers 400 "Your credit balance is too low to access the Anthropic API"
    return retryable(error)


def fallback_for(model: str) -> str | None:
    return FALLBACK_MODEL if FALLBACK_MODEL and model != FALLBACK_MODEL else None


def usable_now(model: str) -> str:
    """`model`, or its fallback while it is being skipped after failing."""
    fallback = fallback_for(model)
    return fallback if fallback and _skip_until.get(model, 0) > monotonic() else model


# --- Context for each turn ---


def app_context(record: SessionRecord, now: datetime) -> str:
    """Server facts the model needs every turn: time, location freshness, adventure status."""
    local = now.astimezone(ZoneInfo(NYC_TIMEZONE))
    lines = [f"Now: {local:%A %Y-%m-%d %H:%M} ({NYC_TIMEZONE})"]
    location = record.adventure.latest_location
    if location is None:
        lines.append("Latest location: none. If you need it, ask the user to type where they are.")
    else:
        age = int((now - location.observed_at).total_seconds() // 60)
        where = location.place_text or f"{location.point.lat:.5f},{location.point.lng:.5f}"
        accuracy = f", accuracy {location.accuracy_m:.0f} m" if location.accuracy_m is not None else ""
        lines.append(f"Latest location: {where} (from {location.source}, {age} min old{accuracy}).")
    adventure = record.adventure
    if adventure.active_plan_id:
        lines.append(
            f"Adventure: {adventure.status}, plan {adventure.active_plan_id}, current checkpoint "
            f"{adventure.current_checkpoint_id or 'none'}. Call get_adventure_state for details."
        )
    else:
        lines.append("Adventure: none yet.")
    return "App context (from the server, not the user):\n" + "\n".join(f"- {line}" for line in lines)


def system_message(model: str | None = None) -> dict:
    """The agent's instructions: the same for every session and turn.

    For Claude they are cached for an hour, so with the tools before them (about 10K tokens) they are read at a
    twentieth of the price instead of written again.
    """
    if not is_claude(model or MODEL):
        return {"role": "system", "content": SYSTEM_PROMPT}
    return {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": HOUR_CACHE}]}


def user_message(text: str, context: str) -> dict:
    """The newest message as the model receives it: this turn's server context, then what the user wrote.

    The context rides with the newest message, not in the system message, so everything before it is unchanged
    since the last turn and can come from the provider's cache. It is not stored: the next turn has its own.
    """
    return {"role": "user", "content": [{"type": "text", "text": context}, {"type": "text", "text": text}]}


def recent(messages: list[dict]) -> list[dict]:
    """At least the last CONTEXT_MESSAGES messages, starting at a user message so tool results keep their calls.

    The start moves in steps of CONTEXT_STEP messages, so it stays put for several turns (see CONTEXT_STEP).
    """
    start = max(0, (len(messages) - CONTEXT_MESSAGES) // CONTEXT_STEP * CONTEXT_STEP)
    while start > 0 and messages[start].get("role") != "user":
        start -= 1
    return messages[start:]


def with_cache_points(messages: list[dict]) -> list[dict]:
    """Claude's request copy, with one-hour cache breakpoints where the history ended before the last two user messages.

    The previous turn cached the history up to its user message, so this turn reads it; the breakpoint before the
    newest message caches what the previous turn added, for the next turn. An hour covers the walk between stops.
    With the instructions' breakpoint and the automatic one for this turn's tool rounds, that is four, the most a
    request may have. A turn stored before every turn ended on an assistant reply may not get one; it then only
    misses the cache.
    """
    marked = list(messages)
    for i in [i for i, m in enumerate(messages) if m.get("role") == "user"][-2:]:
        before = messages[i - 1] if i > 0 else {}
        if before.get("role") == "assistant" and not before.get("tool_calls") and isinstance(before.get("content"), str) \
                and before["content"]:
            marked[i - 1] = {**before, "content": [{"type": "text", "text": before["content"], "cache_control": HOUR_CACHE}]}
    return marked


def without_reasoning(messages: list[dict]) -> list[dict]:
    """Copies of earlier turns without the model's reasoning, which is replayed only within the turn that made it.

    Claude binds each thinking block to the exact conversation before it, and the server context in the system
    message changes every turn, so an older block would be rejected. Removing every earlier block is allowed.
    Sessions stored before this change may still hold them. The copies are deep: a save during the turn may
    compact the stored history, and the conversation the model already saw must not change under it.
    """
    plain = copy.deepcopy(messages)
    for message in plain:
        for key in REASONING_FIELDS:
            message.pop(key, None)
            (message.get("provider_specific_fields") or {}).pop(key, None)  # LiteLLM keeps a second copy there
    return plain


def turn_messages(text: str, replies: list[dict], response: str) -> list[dict]:
    """This turn as stored: the user's message without the server context, then the model's messages without
    reasoning or empty replies, ending with the reply the user saw.

    A turn cut short (out of time or tool rounds, or a failed model call) otherwise ends on a tool result or
    the user's message, and the model would not know what the user was told.
    """
    kept = [m for m in without_reasoning(replies) if m.get("role") != "assistant" or m.get("content") or m.get("tool_calls")]
    last = kept[-1] if kept else {}
    if last.get("role") != "assistant" or last.get("tool_calls") or not last.get("content"):
        kept.append({"role": "assistant", "content": response})
    return [{"role": "user", "content": text}, *kept]


# --- FastAPI App ---

app = FastAPI()
store = state.store_from_env()
logger = logging.getLogger("scavagent")


def session_id_or_400(session_id: str | None) -> str:
    if session_id is None:
        return str(uuid.uuid4())
    if not state.SESSION_ID_PATTERN.match(session_id):
        raise HTTPException(400, "session_id must be 1-128 letters, digits, '.', '_' or '-'.")
    return session_id


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Get or create the session. A client may choose its own id; the server makes one otherwise.
    session_id = session_id_or_400(request.session_id)
    message_id = request.client_message_id
    record = store.load(session_id)

    # A resent message gets the reply already given, without running tools twice.
    if message_id and record and message_id in record.replies:
        return record.replies[message_id]
    if not message_id:
        return run_turn(record or SessionRecord.new(session_id), request)

    # Claim the message before the agent runs, so a resend that arrives mid-turn gets a 409
    # instead of a second turn (and a second camera capture).
    claim = store.claim(session_id, message_id, state.utc_now())
    if claim is None:
        raise HTTPException(409, "Still working on that message. Send it again in a moment.")
    try:
        record = store.load(session_id)  # The first turn may have finished between the load and the claim
        if record and message_id in record.replies:
            reply = record.replies[message_id]
        else:
            reply = run_turn(record or SessionRecord.new(session_id), request)
    except Exception:
        store.release(session_id, message_id, claim)
        raise
    # Only a crash skips this; its claim then expires after state.IN_FLIGHT_TIMEOUT.
    store.release(session_id, message_id, claim)
    return reply


def run_turn(record: SessionRecord, request: ChatRequest) -> dict:
    """Answer one user message, then store the whole turn in one save."""
    session_id = record.session_id
    now = state.utc_now()
    if request.location:
        state.update_location(record, request.location)

    # The user's message joins the stored conversation only when the turn ends, so a save
    # during the turn (a captured photo) never stores half a turn.
    newest = user_message(request.message, app_context(record, now))
    model = usable_now(MODEL)  # Skip a model that just failed
    conversation = [system_message(model)] + recent(without_reasoning(record.messages) + [newest])
    new_from = len(conversation)  # The model's messages for this turn start after the newest message

    tool_calls = []
    try:
        ctx = ToolContext(record=record, store=store, message_id=request.client_message_id)
        response = run_agent(conversation, tool_calls, ctx, model=model)
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        # Provider errors can quote request details, so those go only to the server log.
        # Tools that already ran stay in the trace.
        logger.exception("Model call failed")
        response = f"Model call failed ({type(e).__name__}). Please try again in a moment."

    # Keep this turn's messages, made plain JSON so any store can hold them.
    record.messages += json.loads(json.dumps(turn_messages(request.message, conversation[new_from:], response), default=str))
    reply = ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls).model_dump(mode="json")
    stored = json.loads(json.dumps(reply))  # Trimming may shorten the stored copy; the reply sent stays whole
    # The message id lets a reloaded page tell that its pending message was already answered.
    message_id = {"client_message_id": request.client_message_id} if request.client_message_id else {}
    record.transcript += [
        {"role": "user", "text": request.message, "at": now.isoformat(), **message_id},
        {"role": "assistant", "text": response, "tool_calls": stored["tool_calls"], "at": state.utc_now().isoformat(),
         **message_id},
    ]
    if request.client_message_id:
        record.remember_reply(request.client_message_id, stored)
    try:
        record.trim()
        store.save(record)
    except VersionConflict:
        # Another message for this session saved first. Its progress stands; this turn's reply is not stored.
        reply["response"] = (
            "Another message in this conversation was handled at the same time, so this reply wasn't saved. "
            "Anything already recorded, such as a photo, is kept. Please send your message again."
        )
    except state.RecordTooLarge:
        # Only the active plan, photos, and progress are left and they alone exceed one document.
        logger.error("Session record too large to store even after trimming")
        reply["response"] = (
            "This conversation has grown too large to save, so this reply wasn't stored. "
            "Please start a new conversation to keep going."
        )
    return reply


@app.get("/history")
def history(session_id: str):
    """What the chat shows, so a reloaded page can restore the conversation."""
    record = store.load(session_id_or_400(session_id))
    if record is None:
        raise HTTPException(404, "No conversation with that session_id.")
    return {"session_id": session_id, "messages": record.transcript}


@app.get("/media/{asset_id}")
def media(asset_id: str):
    """A saved camera still. The unguessable asset id is the capability; the live camera is never re-fetched."""
    found = store.get_asset(asset_id) if asset_id.isalnum() else None
    if found is None:
        raise HTTPException(404, "No saved image with that id.")
    data, content_type = found
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/clear")
def clear(session_id: str | None = None):
    if session_id:
        store.delete(session_id_or_400(session_id))
    return {"status": "ok"}


if __name__ == "__main__":
    # Cloud Run provides PORT and needs 0.0.0.0; locally this stays on 127.0.0.1:8000.
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
