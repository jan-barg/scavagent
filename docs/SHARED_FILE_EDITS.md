# Kyle's edits to Jan-owned files

Kyle's workstream edited files Jan owns only where the agent could not work end to end otherwise. Each edit is small, lives in its own commit on `kyle/workstream`, and is listed here with the original text, so it can be reviewed, reverted, or re-applied while merging.

**Base:** `origin/main` at `9b54995` (Merge pull request #4, camera registration), merged into `kyle/workstream` on 2026-09-29.

**Restore the originals of every file below:** `git checkout 9b54995 -- tools.py app.py pyproject.toml uv.lock README.md`, or revert the commits named in each section. Kyle's own files (`agent.py`, `adventure/`, `integrations/` other than `cameras.py`, `scripts/`, `docs/TOOLS.md`, `fixtures/integrations/`, and Kyle's tests) are unaffected.

| File | Commit on `kyle/workstream` | Revert |
|---|---|---|
| `tools.py`, `app.py` | `3f687b2` Register Kyle's tools and use the agent prompt | `git revert 3f687b2` |
| `pyproject.toml`, `uv.lock` | `ba4c953` Add gtfs-realtime-bindings | `git revert ba4c953`, then `uv lock` |
| `README.md` (grader examples section only) | `d96be0a` Guard against UTC/local time mix-ups; update README grader section | restore the section below |
| `app.py` (tool rounds 12 → 16) | `abf5df9` Allow 16 tool rounds per turn | `git revert abf5df9` |

Later commits registered more of Kyle's tools only inside Kyle's own lists (`PLACE_TOOLS`, `PLANNING_TOOLS`), so `tools.py` did not change again.

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

## `pyproject.toml` and `uv.lock`: add `gtfs-realtime-bindings`

Why: live subway arrivals (`integrations/mta.py`) come from the MTA's GTFS-realtime feeds, which are protobuf. The official Google bindings decode them; version 2.2.0 works with the installed protobuf 6. Added with `uv add gtfs-realtime-bindings`: one line in `pyproject.toml` (`"gtfs-realtime-bindings>=2.2.0"` in `dependencies`) and the matching `uv.lock` entries. If `uv.lock` conflicts while merging, take either side and run `uv lock` again.

Original: `pyproject.toml` and `uv.lock` at the base commit had no `gtfs-realtime-bindings`.

## `app.py`: use the agent prompt; allow more tool rounds

Why: the interim prompt said "Kyle's agent work replaces it"; `agent.py` now holds the planning, guiding, adapting, and ending instructions. Planning a researched adventure takes more rounds than the starter's 8 (geocode, find, research, evaluate, fix, evaluate, save, reply). The limit was 12 in `3f687b2`; a live grader-query-2 run then used all 12 and got the harness's "tool-call limit" reply, so it is 16 since `abf5df9`. `TURN_SECONDS` still bounds every turn.

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

## `README.md`: grader examples section

Why: Kyle leads the grader examples. The section now says the examples run end to end locally, notes the camera limitation, and links the acceptance checks and `docs/TOOLS.md`. The rest of the README is unchanged.

Original section at the base commit:

```markdown
## Planned grader examples

These are implementation acceptance targets, not currently supported adventure behavior. Finalize the starting locations after selecting the field-tested pilot area.
```

(The three numbered examples are unchanged.)

## Jan's edits to Kyle-owned files (after merging PR #6)

- `adventure/agent_tools.py`: evaluated drafts moved from the module-level `_drafts` dict into the session record (`ctx.record.remember_draft(...)`, `ctx.record.drafts`, with `state.EvaluatedDraft`). Why: on Cloud Run a restart, scale-to-zero, or another instance between evaluate and save lost the draft, and expired drafts were never pruned. The per-session check is now implicit; `DRAFT_SECONDS` is gone because `DRAFT_FRESH` (10 minutes) already refuses older drafts. `reset_for_tests()` stays as a no-op for the existing fixture.
- `adventure/agent_tools.py`: `evaluate_adventure_plan` now answers `INVALID_ARGUMENT` ("too large to keep") when `remember_draft` refuses a plan over `state.MAX_DRAFT_BYTES`.
- `tests/test_planning_tools.py`: two added tests (`test_a_draft_evaluated_before_a_restart_can_still_be_saved`, `test_a_plan_too_large_to_store_is_refused_with_a_fix`), and `test_minutes_left_mid_adventure_count_from_now` now fakes the routing call of its final "check" (it reached the live Valhalla server; the suite now passes with the network blocked).
- Kyle's items 3 and 4 from `docs/STATUS.md` are done in Jan's files: model-call retries in `app.py`; completion in plan order and finishing only with every stop resolved in `state.py`. Destination arrival itself is still not tracked in state.

# Jan's camera session: edits to Kyle's files (September 29)

Made on `claude/camera-calibration` so a field-verified camera position reaches the plan. Each is small and in its own commit; revert the commit to undo it.

| File | Commit | Change | Revert |
|---|---|---|---|
| `adventure/agent_tools.py` | `016ca8e` | `camera_lookup` reads `cameras.load_checkpoints(allow_synthetic=dev_mode())` instead of the private `cameras._checkpoints` (Kyle's request) | `git revert 016ca8e` (also removes the accessor) |
| `scripts/acceptance_checks.py` | `977017a` | New check: when the finder returns a camera for query 2, the passing plan includes a `camera_capture` stop | `git revert 977017a` |
| `agent.py`, `adventure/drafts.py`, `adventure/validation.py`, `tests/test_planning_tools.py` | `76c6103` | The prompt says how to add a found camera stop; a draft refuses a stop with `camera_checkpoint_id` whose activity is not `camera_capture`; the evaluator lists a camera stop last among cuts | `git revert 76c6103` |

Why `76c6103`: in local live runs of query 2 with a camera position 5 m from the start, Flash-Lite dropped the camera in 5 of 5 runs and said no position fit. It either put the camera at the start with a `user_observation` activity (the plan passed but would never capture) or followed the first cut suggestion, which named the camera stop ("saves up to 16 minutes" for a stop 0.6 minutes from the start). With the three changes, 2 of 2 runs kept it.
