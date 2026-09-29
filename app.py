import json
import os
import uuid
from pathlib import Path

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

from schemas import ChatRequest, ChatResponse, tool_error
from tools import TOOLS, run_tool

# --- Config ---

SYSTEM_PROMPT = (
    "You are a helpful assistant. When a question depends on the weather or "
    "outdoor conditions, call get_weather first, then answer in a sentence."
)
MAX_TOOL_ROUNDS = 5
# Model choice is deferred; keep it configurable. Defaults to the starter's model.
MODEL = os.environ.get("SCAVAGENT_MODEL", "vertex_ai/gemini-3.5-flash-lite")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")

# --- The Harness ---


def run_agent(messages: list[dict], tool_calls: list[dict]) -> str:
    """Complete until the model answers without asking for a tool.

    Returns the final text. Every tool call is appended to `tool_calls` as it runs,
    so the caller keeps the trace even if a later model call raises.
    """
    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model=MODEL,
            vertex_location=VERTEX_LOCATION,
            messages=messages,
            tools=TOOLS,
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
            if isinstance(args, dict):
                result = run_tool(call.function.name, args)
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


# --- Session Store ---

# session_id -> list of messages. In-memory, single process.
sessions: dict[str, list] = {}

# --- FastAPI App ---

app = FastAPI()


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Get or create the session
    session_id = request.session_id or str(uuid.uuid4())
    if session_id not in sessions:
        sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Append user's message to the context
    sessions[session_id] += [{"role": "user", "content": request.message}]

    tool_calls = []
    try:
        response = run_agent(sessions[session_id], tool_calls)
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        # Tools that already ran stay in the trace.
        response = f"Model call failed: {type(e).__name__}: {str(e)[:300]}"

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    # Cloud Run provides PORT and needs 0.0.0.0; locally this stays on 127.0.0.1:8000.
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
