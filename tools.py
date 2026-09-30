"""The tools the harness can run, and the JSON that describes them to the model."""

import os
from datetime import datetime, timezone

import requests

import state
from adventure import agent_tools
from integrations import cameras, tool_specs
from schemas import Freshness, tool_error, tool_ok
from state import ToolContext

# Open-Meteo is free and needs no API key.
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def get_weather(location: str) -> dict:
    """Get the current weather for a location."""
    try:
        places = requests.get(GEOCODE_URL, params={"name": location, "count": 1}, timeout=10).json()
        if not places.get("results"):
            return tool_error(
                "NO_MATCH",
                f"City '{location}' was not found.",
                retryable=False,
                next_step="Retry with a plain city name such as 'New York'.",
            )
        place = places["results"][0]

        current = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
            timeout=10,
        ).json()["current"]
    except (requests.RequestException, KeyError, ValueError) as e:
        # The model cannot see an exception. Return something it can reason about.
        return tool_error(
            "UPSTREAM_UNAVAILABLE",
            f"Weather service failed: {type(e).__name__}",
            retryable=True,
            next_step="Answer without the weather, or try once more.",
        )

    return tool_ok(
        {
            "location": place["name"],
            "temp_f": current["temperature_2m"],
            "humidity": current["relative_humidity_2m"],
            "wind_mph": current["wind_speed_10m"],
        },
        freshness=Freshness(kind="live", retrieved_at=datetime.now(timezone.utc)),
    )


# --- Adventure state tools ---
# These receive the server's ToolContext for the current session as their first
# argument. The model never passes, or chooses, a session id.

STATUS_OPERATIONS = {"start_adventure": "active", "finish_adventure": "completed", "abandon_adventure": "abandoned"}
CHECKPOINT_OPERATIONS = {"complete_checkpoint": "completed", "skip_checkpoint": "skipped", "block_checkpoint": "blocked"}


def get_adventure_state(ctx: ToolContext) -> dict:
    """The current adventure's progress, current checkpoint, and revealed story."""
    return tool_ok(state.state_summary(ctx.record))


def update_adventure_state(
    ctx: ToolContext,
    operation: str,
    checkpoint_id: str | None = None,
    beat_id: str | None = None,
    note: str | None = None,
    user_waived_required: bool = False,
    asset_id: str | None = None,
    visibility: str | None = None,
    expected_version: int | None = None,
) -> dict:
    """Apply one allowed progress change to this session's adventure."""
    def missing(argument):
        return tool_error("INVALID_ARGUMENT", f"{operation} needs {argument}.", retryable=False,
                          next_step=f"Call again with {argument}.")

    if operation in CHECKPOINT_OPERATIONS:
        if not checkpoint_id:
            return missing("checkpoint_id")
        if operation == "skip_checkpoint" and (refused := state.skip_before_start(ctx)):
            return refused
        return state.resolve_checkpoint(
            ctx, checkpoint_id, CHECKPOINT_OPERATIONS[operation], note=note,
            user_waived_required=user_waived_required, expected_version=expected_version,
        )
    if operation == "reveal_beat":
        return state.reveal_beat(ctx, beat_id, expected_version=expected_version) if beat_id else missing("beat_id")
    if operation == "reach_destination":
        return state.reach_destination(ctx, expected_version=expected_version)
    if operation in STATUS_OPERATIONS:
        return state.set_status(ctx, STATUS_OPERATIONS[operation], expected_version=expected_version)
    if operation == "set_photo_visibility":
        if not asset_id or visibility not in ("user_confirmed_visible", "user_reported_not_visible"):
            return missing("asset_id and visibility ('user_confirmed_visible' or 'user_reported_not_visible')")
        return state.set_photo_visibility(ctx, asset_id, visibility)
    return tool_error("INVALID_ARGUMENT", f"Unknown operation '{operation}'.", retryable=False,
                      next_step="Use one of the operations listed in the tool schema.")


def load_dev_adventure(ctx: ToolContext, scenario: str) -> dict:
    """Development only: load a labeled synthetic fixture adventure into this session."""
    from fixtures import SCENARIOS, load_scenario

    if scenario not in SCENARIOS:
        return tool_error("INVALID_ARGUMENT", f"Unknown scenario '{scenario}'.", retryable=False,
                          next_step=f"Use one of: {', '.join(SCENARIOS)}.")
    fixture = load_scenario(scenario)
    ctx.record.plans[fixture.plan.plan_id] = fixture.plan
    progress = fixture.state.model_dump(include={"completed_ids", "skipped_ids", "blocked_ids", "revealed_beat_ids"})
    loaded = ctx.record.adventure.model_copy(update={
        **progress,
        "status": "active",  # Fixtures carry no evaluation; only this dev path may activate them.
        "active_plan_id": fixture.plan.plan_id,
        "current_checkpoint_id": fixture.state.current_checkpoint_id,
        "user_reports": [],
    })
    state._commit(ctx, loaded)
    return tool_ok(
        state.state_summary(ctx.record),
        warnings=["Synthetic development fixture: places, routes, and clues are invented, not researched."],
        freshness=Freshness(kind="synthetic_fixture"),
    )


# --- Camera checkpoints (integrations/cameras.py) ---
# The model sets only the arguments in each tool's schema. The camera functions' other
# parameters (checkpoint lists, HTTP client, dev fixtures, storage) are server-side only.

FINDER_ARGUMENTS = {
    name for tool in cameras.CAMERA_TOOLS if tool["function"]["name"] == "find_camera_checkpoints"
    for name in tool["function"]["parameters"]["properties"]
}


def find_camera_checkpoints(**args) -> dict:
    """Rank verified pedestrian camera positions near a point or along a route."""
    if extra := sorted(set(args) - FINDER_ARGUMENTS):
        return tool_error("INVALID_ARGUMENT", f"find_camera_checkpoints does not take {extra}.", retryable=False,
                          next_step=f"Use only: {', '.join(sorted(FINDER_ARGUMENTS))}.")
    return cameras.find_camera_checkpoints(**args)


def capture_camera_checkpoint(ctx: ToolContext, checkpoint_id: str) -> dict:
    """Save a checkpoint's DOT still to this session, at most once per user message."""
    photo = ctx.saved_capture(checkpoint_id)
    if photo is not None:  # This message's earlier turn died after taking the photo
        status = cameras.verification_status(checkpoint_id)
        warnings = ["This message already saved this photo before its turn was interrupted; it was reused, not retaken."]
        if status == "image_verified":  # the replay must disclose what the first capture did
            warnings.append(cameras.IMAGE_VERIFIED_CAPTURE_WARNING)
        return tool_ok(
            {"photo": photo.model_dump(mode="json"), "already_captured": True, "verification_status": status},
            warnings=warnings,
        )
    return cameras.capture_camera_checkpoint(checkpoint_id, save_asset=ctx.save_asset)


# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather (temperature, humidity, wind) for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, e.g. 'New York'"},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_adventure_state",
            "description": (
                "Get this user's saved adventure: status, state_version, every checkpoint's outcome, the current "
                "checkpoint's full activity (prompt, answer rule, hints, fallback), revealed story beats, the next "
                "beat to reveal, latest location, and saved photos. Call it before judging an answer or changing progress."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_adventure_state",
            "description": (
                "Record one change to this user's adventure progress. complete_checkpoint when the user finishes "
                "a checkpoint's activity (put what they said or saw in note); skip_checkpoint when they choose to skip "
                "(a stop they required needs user_waived_required=true after they explicitly confirm); "
                "block_checkpoint when it is impossible, e.g. closed or camera offline; reveal_beat after telling "
                "the user a story beat; reach_destination only when the user says they have arrived at the plan's "
                "destination (required before finish_adventure on a plan with one); "
                "start_adventure / finish_adventure / abandon_adventure; "
                "set_photo_visibility after the user says whether they appear in a saved photo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [*CHECKPOINT_OPERATIONS, "reveal_beat", "reach_destination", *STATUS_OPERATIONS, "set_photo_visibility"],
                    },
                    "checkpoint_id": {"type": "string", "description": "For checkpoint operations, e.g. 'stop_2'"},
                    "beat_id": {"type": "string", "description": "For reveal_beat"},
                    "note": {"type": "string", "description": "The user's answer or report, in their words"},
                    "user_waived_required": {"type": "boolean", "description": "The user explicitly dropped a stop they required"},
                    "asset_id": {"type": "string", "description": "For set_photo_visibility"},
                    "visibility": {"type": "string", "enum": ["user_confirmed_visible", "user_reported_not_visible"]},
                    "expected_version": {"type": "integer", "description": "state_version you last read; rejected if stale"},
                },
                "required": ["operation"],
            },
        },
    },
    *cameras.CAMERA_TOOLS,
    *tool_specs.PLACE_TOOLS,  # Kyle: places, research, walking and transit routes
    *agent_tools.PLANNING_TOOLS,  # Kyle: evaluate_adventure_plan, save_adventure_plan
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_weather": get_weather,
    "get_adventure_state": get_adventure_state,
    "update_adventure_state": update_adventure_state,
    "find_camera_checkpoints": find_camera_checkpoints,
    "capture_camera_checkpoint": capture_camera_checkpoint,
    **tool_specs.PLACE_TOOL_MAP,
    **agent_tools.PLANNING_TOOL_MAP,
}

# Tools that receive the session's ToolContext as their first argument.
SESSION_TOOLS = {"get_adventure_state", "update_adventure_state", "load_dev_adventure", "capture_camera_checkpoint",
                 *agent_tools.PLANNING_SESSION_TOOLS}

if os.environ.get("SCAVAGENT_DEV_FIXTURES") == "1":
    TOOL_MAP["load_dev_adventure"] = load_dev_adventure
    TOOLS.append({
        "type": "function",
        "function": {
            "name": "load_dev_adventure",
            "description": (
                "DEVELOPMENT ONLY. Load a synthetic test adventure into this session so progression can be tested. "
                "Use only when the user explicitly asks for a test/dev adventure; tell them it is synthetic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string", "enum": ["start_only", "constrained_route", "revision_after_skip"]},
                },
                "required": ["scenario"],
            },
        },
    })


def run_tool(name: str, args: dict, ctx: ToolContext | None = None) -> dict:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return tool_error(
            "UNKNOWN_TOOL",
            f"Unknown tool '{name}'.",
            retryable=False,
            next_step=f"Use one of: {', '.join(TOOL_MAP)}.",
        )
    try:
        if name in SESSION_TOOLS:
            if ctx is None:
                raise RuntimeError("session tool called without a session")
            return TOOL_MAP[name](ctx, **args)
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return tool_error(
            "INVALID_ARGUMENT",
            f"Bad arguments for {name}: {e}",
            retryable=False,
            next_step="Call the tool again with the arguments its schema lists.",
        )
    except Exception as e:
        return tool_error(
            "INTERNAL_ERROR",
            f"{name} failed unexpectedly: {type(e).__name__}",
            retryable=False,
            next_step="Continue without this tool's result and tell the user it is unavailable.",
        )
