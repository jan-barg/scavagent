import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

import litellm
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

import state
from schemas import NYC_TIMEZONE, ChatRequest, ChatResponse, tool_error
from state import SessionRecord, ToolContext, VersionConflict
from agent import SYSTEM_PROMPT
from tools import TOOLS, run_tool

# --- Config ---

# The agent's instructions live in agent.py (Kyle's workstream); see docs/SHARED_FILE_EDITS.md.
# Planning a researched adventure takes more tool rounds than the starter's 8; TURN_SECONDS still bounds the turn.
MAX_TOOL_ROUNDS = 16
# A turn starts no model call or tool after this, and each model call gets only the time left. Tools bound
# their own time. Kept well under state.IN_FLIGHT_TIMEOUT, so an expired claim's turn is no longer working.
TURN_SECONDS = 240
CONTEXT_MESSAGES = 40  # Recent conversation sent to the model; the full plan stays in storage
# Model choice is deferred; keep it configurable. Defaults to the starter's model.
MODEL = os.environ.get("SCAVAGENT_MODEL", "vertex_ai/gemini-3.5-flash-lite")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")

# --- The Harness ---


def run_agent(messages: list[dict], tool_calls: list[dict], ctx: ToolContext | None = None) -> str:
    """Complete until the model answers without asking for a tool.

    Returns the final text. Every tool call is appended to `tool_calls` as it runs,
    so the caller keeps the trace even if a later model call raises. Session tools
    receive `ctx`, which the server binds to the current session.
    """
    deadline = monotonic() + TURN_SECONDS
    for _ in range(MAX_TOOL_ROUNDS):
        remaining = deadline - monotonic()
        if remaining <= 0:
            return "That took me too long, so I stopped. Please send your message again."
        reply = litellm.completion(
            model=MODEL,
            vertex_location=VERTEX_LOCATION,
            messages=messages,
            tools=TOOLS,
            timeout=remaining,
        ).choices[0].message

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content or ""

        # The harness, not the model, runs each tool and appends the result
        for call in reply.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            if monotonic() >= deadline:
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

    return "Sorry, I hit my tool-call limit before finishing."


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


def recent(messages: list[dict]) -> list[dict]:
    """The last CONTEXT_MESSAGES messages, starting at a user message so tool results keep their calls."""
    start = max(0, len(messages) - CONTEXT_MESSAGES)
    while start > 0 and messages[start].get("role") != "user":
        start -= 1
    return messages[start:]


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
    context = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + app_context(record, now)}]
    conversation = context + recent(record.messages + [{"role": "user", "content": request.message}])
    new_from = len(conversation) - 1

    tool_calls = []
    try:
        ctx = ToolContext(record=record, store=store, message_id=request.client_message_id)
        response = run_agent(conversation, tool_calls, ctx)
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        # Provider errors can quote request details, so those go only to the server log.
        # Tools that already ran stay in the trace.
        logger.exception("Model call failed")
        response = f"Model call failed ({type(e).__name__}). Please try again in a moment."

    # Keep this turn's messages, made plain JSON so any store can hold them.
    record.messages += json.loads(json.dumps(conversation[new_from:], default=str))
    reply = ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls).model_dump(mode="json")
    record.transcript += [
        {"role": "user", "text": request.message, "at": now.isoformat()},
        {"role": "assistant", "text": response, "tool_calls": reply["tool_calls"], "at": state.utc_now().isoformat()},
    ]
    if request.client_message_id:
        record.remember_reply(request.client_message_id, reply)
    record.trim()

    try:
        store.save(record)
    except VersionConflict:
        # Another message for this session saved first. Its progress stands; this turn's reply is not stored.
        reply["response"] = (
            "Another message in this conversation was handled at the same time, so this reply wasn't saved. "
            "Anything already recorded, such as a photo, is kept. Please send your message again."
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
