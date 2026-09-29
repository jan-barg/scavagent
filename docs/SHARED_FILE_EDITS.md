# Kyle's edits to Jan-owned files

Kyle's workstream edited files Jan owns only where the agent could not work end to end otherwise. Each edit is small, lives in its own commit on `kyle/workstream`, and is listed here with the original text, so it can be reviewed, reverted, or re-applied while merging.

**Base:** `origin/main` at `9b54995` (Merge pull request #4, camera registration), merged into `kyle/workstream` on 2026-09-29.

**Restore Jan's originals of every file below:** `git checkout 9b54995 -- tools.py app.py`, or revert the commits named in each section. The planning and research modules (`agent.py`, `adventure/`, `integrations/tool_specs.py`, and the other adapters) are Kyle's own files and are unaffected.

## `tools.py`: register Kyle's tools

Why: the model can use only registered tools. The registry stays Jan's; the edit adds Kyle's tool lists beside the camera tools, without changing any existing tool.

Changes:
- Import `tool_specs` from `integrations` and `agent_tools` from `adventure`.
- Append `*tool_specs.PLACE_TOOLS, *agent_tools.PLANNING_TOOLS` after `*cameras.CAMERA_TOOLS` in `TOOLS`.
- Add `**tool_specs.PLACE_TOOL_MAP, **agent_tools.PLANNING_TOOL_MAP` to `TOOL_MAP`.
- Add `agent_tools.PLANNING_SESSION_TOOLS` (`evaluate_adventure_plan`, `save_adventure_plan`) to `SESSION_TOOLS`.

Original lines at the base commit:

```python
from integrations import cameras
```

```python
    *cameras.CAMERA_TOOLS,
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_weather": get_weather,
    "get_adventure_state": get_adventure_state,
    "update_adventure_state": update_adventure_state,
    "find_camera_checkpoints": find_camera_checkpoints,
    "capture_camera_checkpoint": capture_camera_checkpoint,
}

# Tools that receive the session's ToolContext as their first argument.
SESSION_TOOLS = {"get_adventure_state", "update_adventure_state", "load_dev_adventure", "capture_camera_checkpoint"}
```

## `app.py`: use the agent prompt; allow more tool rounds

Why: the interim prompt said "Kyle's agent work replaces it"; `agent.py` now holds the planning, guiding, adapting, and ending instructions. Planning a researched adventure takes more rounds than the starter's 8 (geocode, find, research, evaluate, fix, evaluate, save, reply), so the limit is 12. `TURN_SECONDS` still bounds every turn.

Original lines at the base commit:

```python
from tools import TOOLS, run_tool
```

```python
# Interim prompt so the state tools can be exercised. Kyle's agent work replaces it.
SYSTEM_PROMPT = (
    "You are Scavagent, a guide for playful NYC adventures run entirely in chat. The user walks between "
    "messages; each message is one turn. Keep replies short and practical.\n"
    "- Progress is saved on the server. Call get_adventure_state before judging an answer or moving on, and "
    "record every change with update_adventure_state. Never claim progress you did not record.\n"
    "- Keep sourced facts, what the user reports, and invented story clearly distinct. Never invent physical "
    "features, people, or access at a real place.\n"
    "- Every challenge has a fallback: offer hints, and let the user skip without getting stuck. A stop the "
    "user required is only skipped after they explicitly say they no longer need it.\n"
    "- Call capture_camera_checkpoint only right after the user types that they are standing in position. "
    "Then ask whether they can see themselves in the photo and record the answer with set_photo_visibility.\n"
    "- Only the starting location is required to plan. If the adventure planner is not available yet, say so "
    "plainly instead of inventing a route."
)
MAX_TOOL_ROUNDS = 8
```
