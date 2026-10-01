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

## Story design v2 (branch `kyle/story-design-v2`, from `main` at `00c25f4`)

Two edits to Jan's files, each in its own commit. The story work builds on the schema edit (the evaluator imports `Character`, and the prompt asks for the new fields), so reverting the schema edit means reverting that work too: every later commit on the branch except `42229e5` (`find_places`), newest first, then `94aad1f`.

| File | Commit | Revert |
|---|---|---|
| `schemas.py` (optional story fields; approved by Jan in `docs/STORY_DESIGN.md`) | `94aad1f` | revert the story commits after it (see above), then `git revert 94aad1f` |
| `state.py` (`state_summary` shows the cast, clues to tell in chat, and the finale) | `6fcb1c9` | `git revert 6fcb1c9` (then drop `test_the_adventure_state_shows_the_cast_and_holds_the_finale_until_the_stops_are_done` from `tests/test_story.py`) |

### `schemas.py`: optional story design v2 fields

Why: the agreed design (`docs/STORY_DESIGN.md`). Every new field is optional with a default, so plans stored before it keep loading (`tests/test_story.py::test_plans_stored_before_story_v2_still_load` validates the stored fixture plan and a session record holding it). New models `Character` and `ThemeLink`; new fields `Activity.solution`, `Checkpoint.theme_link`, `StoryBeat.characters`, `StoryBeat.clue`, `StoryBeat.uses`, `Story.briefing`; `Story.cast` is now `list[Character | str]`.

Original lines at `00c25f4`:

```python
    fallback: str = Field(min_length=1, description="How the user continues if this cannot be done")

    @model_validator(mode="after")
    def _physical_tasks_need_evidence(self):
```

```python
class Checkpoint(Record):
    checkpoint_id: Id
    place_id: Id
    required_by_user: bool = False
    activity: Activity
    dwell_minutes: int = Field(ge=0)
    story_beat_id: Id | None = None
    camera_checkpoint_id: Id | None = None
```

```python
class StoryBeat(Record):
    """Invented plot. Never presented as historical fact."""

    beat_id: Id
    checkpoint_id: Id | None = None  # None: delivered in chat, not tied to a stop
    summary: str
    reveals: str | None = None


class Story(Record):
    premise: str
    cast: list[str] = []
    solution: str
    beats: list[StoryBeat] = []
```

### `state.py`: `state_summary` carries the story across turns

Why: the model sees only the last 40 messages (`app.CONTEXT_MESSAGES`), so by the second stop the draft it wrote (the cast's contact channels, the finale, a clue moved to chat after a skip) is gone, and `get_adventure_state` showed only the current stop's beat. Three keys were added; nothing existing changed:

- `cast`: the story's cast, plain names or `Character` objects.
- `clues_to_tell_in_chat`: unrevealed beats that are chat beats (except the finale) or belong to a stop the user skipped or could not reach.
- `finale_if_finished`: the last chat beat, once no checkpoint is current (like `solution_if_finished`).

Original: `state_summary` at `00c25f4` had none of these keys and no `finale`/`missed` locals.
- `agent.py` (after PR #13): one sentence in "Ending": when the user says they have arrived at the destination, call `update_adventure_state reach_destination`, then `finish_adventure`; the server now refuses to finish a plan with a destination before `reach_destination`. Why: grader query 3 sometimes ended the adventure while the user was still on the way (Kyle's report).

# Jan's camera session: edits to Kyle's files (September 29)

Made on `claude/camera-calibration` so a field-verified camera position reaches the plan. Each is small and in its own commit; revert the commit to undo it.

| File | Commit | Change | Revert |
|---|---|---|---|
| `adventure/agent_tools.py` | `016ca8e` | `camera_lookup` reads `cameras.load_checkpoints(allow_synthetic=dev_mode())` instead of the private `cameras._checkpoints` (Kyle's request) | `git revert 016ca8e` (also removes the accessor) |
| `scripts/acceptance_checks.py` | `977017a` | New check: when the finder returns a camera for query 2, the passing plan includes a `camera_capture` stop | `git revert 977017a` |
| `agent.py`, `adventure/drafts.py`, `adventure/validation.py`, `tests/test_planning_tools.py` | `76c6103` | The prompt says how to add a found camera stop; a draft refuses a stop with `camera_checkpoint_id` whose activity is not `camera_capture`; the evaluator lists a camera stop last among cuts | `git revert 76c6103` |

When `main` (with Kyle's story design v2 prompt) was merged into this branch, the three camera sentences were re-applied to the rewritten `agent.py` and the camera-last ordering was kept beside the new "or swap it" suggestion text, in the merge commit; revert those hunks there too.

Why `76c6103`: in local live runs of query 2 with a camera position 5 m from the start, Flash-Lite dropped the camera in 5 of 5 runs and said no position fit. It either put the camera at the start with a `user_observation` activity (the plan passed but would never capture) or followed the first cut suggestion, which named the camera stop ("saves up to 16 minutes" for a stop 0.6 minutes from the start). With the three changes, 2 of 2 runs kept it.

## Cameras, story depth, and guiding (branch `kyle/cameras-and-depth`, from `main` at `bcc3ea9`)

- `README.md` ("Project docs" only): one link added to `docs/CONTRIBUTIONS.md`. Original line:

  ```markdown
  - Status and who owns what: [docs/STATUS.md](docs/STATUS.md), [docs/WORK_SPLIT.md](docs/WORK_SPLIT.md).
  ```

- `app.py` (the harness): a round made only of lookup tools (every tool but the session tools) runs its calls at once (`lookups_at_once`), and the trace keeps the model's order. A round with any session tool runs one call at a time, as before. Why: on Sonnet, three `research_place` calls in one round took 16 s one after another, and three `geocode_place` calls took 12 s. Test: `tests/test_lookups_at_once.py`. To revert, drop `lookups_at_once` and the `early` lookup. The original lines:

  ```python
  from tools import TOOLS, run_tool
  ```

  ```python
          # The harness, not the model, runs each tool and appends the result
          for i, call in enumerate(reply.tool_calls):
  ```

  ```python
              elif isinstance(args, dict):
                  result = run_tool(call.function.name, args, ctx)
  ```

No other file of Jan's changed. `adventure/`, `agent.py`, `scripts/`, `tests/test_planning_tools.py`, `tests/test_story.py`, and `docs/TOOLS.md` are Kyle's; `docs/CONTRIBUTIONS.md` is new (Jan: please check your half).

## Full names (branch `claude/status-readme`, from `main` at `f19af1f`)

- `docs/CONTRIBUTIONS.md` (Kyle's), first sentence only: Jan asked for full names. Original: "Scavagent was built by Jan (`jgb2170`) and Kyle (`kc3936`)." Now: "Scavagent was built by Jan Barganowski (`jgb2170`) and Kyle Coletta (`kc3936`)." The same change was made to the team lines of the shared `docs/PLAN.md` and `docs/WORK_SPLIT.md`.

## Redundancy cleanup (branch `claude/redundancy-cleanup`, from `main` at `3ca0f5c`)

Jan asked for code that nothing uses to be removed, and Codex reviewed each change as safe. Kyle's files that changed:

- `adventure/validation.py`: `_meters` was an exact copy of `integrations.common.distance_m`. Its three callers now use `distance_m`, imported from `integrations.common`. The original:

  ```python
  def _meters(lat1, lng1, lat2, lng2):
      p1, p2 = math.radians(lat1), math.radians(lat2)
      a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
      return 2 * 6_371_000 * math.asin(math.sqrt(a))
  ```

- `adventure/drafts.py`: `_contingency(draft, request, legs, checkpoints)` is now `_contingency(draft, legs, checkpoints)`, because `request` was never used. Both callers were updated.
- `fixtures/integrations/*.json` (4 files) and `scripts/capture_integration_fixtures.py` were deleted. Nothing loaded the captures, and their `find_places` results predate `matched_terms`. The same-named `fixtures/*.json` are separate files and stay.

To restore any of them: `git checkout 3ca0f5c -- adventure/validation.py adventure/drafts.py fixtures/integrations scripts/capture_integration_fixtures.py`.

## Stale lines after #28 and #29 (branch `claude/stale-docs`, from `main` at `922321f`)

- `docs/STATUS.md`, Kyle's section, three factual updates only:
  - "The one change still in review is the double-negative fix" was dropped, and #28 was added to the merged list;
  - "**In review (`kyle/status-and-double-negatives`):**" became "**#28:**";
  - "Cleanup ... waits for the read-only session's list" became "Cleanup: done in #29".

## Lou's voice (branch `jan/frontend-redesign`, from `main` at `ac19b86`)

Part of the frontend redesign ([FRONTEND_REDESIGN.md](FRONTEND_REDESIGN.md)). Jan chose the guide's name, Lou, and the voice; Kyle's Claude greenlit the edit on September 30 with one wording change to the first Voice bullet, and Jan's main Claude agreed. Kyle's change: the dispatcher handles sourced facts *except the stop's theme-link fact*, which the character carries into the arrival scene, because Guiding's arrival rule and `docs/STORY_DESIGN.md` put that fact in the handler's scene, and the first wording would have made the model choose. Every existing rule is unchanged; "short, warm, and practical" stays.

Revert: `git revert 0ad1c66`. The page renders replies without this formatting as plain paragraphs.

- `agent.py`, the opening line. Original:

  ```text
  You are Scavagent, a guide for playful, real-world NYC adventures run entirely in chat. The user walks or \
  rides between messages; each message is one turn. Keep replies short, warm, and practical: one step at a time.
  ```

  Now:

  ```text
  You are Lou, the dispatcher at Scavagent, a guide for playful, real-world NYC adventures run entirely in chat. The user walks or \
  rides between messages; each message is one turn. Keep replies short, warm, and practical: one step at a time.
  ```

- `agent.py`, a new section before `## Ground rules` (nothing was removed):

  ```text
  ## Voice
  - You work the desk and are never a character in the story. You handle what is real: directions, times, hints, \
  photos, how the game works, and sourced facts, except the stop's theme-link fact, which the character carries into the \
  scene in the story's voice, with its source link. Each adventure's invented handler and cast speak only inside \
  scenes, and you patch them through ("Patching your handler through."). Never give a character your name.
  - Sound like a seasoned New York night dispatcher: dry, quick, and kind. Short sentences; street names, cross \
  streets, clock times, and minutes from the tools. At most one wry line per reply. No emoji, no gushing, no \
  "Great question".
  - Format for a phone: the briefing is one blockquote that starts "> **Briefing:**"; every line a character speaks \
  is a blockquote that starts with their name and channel in bold, e.g. "> **Name (radio):** ..."; walking \
  directions are a short numbered list; the finale's case file starts with the line "### Case file". Everything \
  outside a blockquote is you.
  - End with the one thing the user can type next ("Say 'here' when you're outside."). You are never on the street: \
  never claim to be somewhere or to see the user.
  ```

- `agent.py`, planning step 8, the first words only. Original: "8. Reply with the briefing first, as its own short paragraph, then the number of stops, ..." Now: "8. Reply with the briefing first, as its own blockquote (see Voice), then the number of stops, ..."

Kyle confirmed that none of the text checks in `scripts/acceptance_checks.py` or `scripts/guiding_checks.py` break under the blockquote and list format. Checked on Claude Sonnet 5.5 against a local server (September 30): acceptance 28/28, guiding 18/18, and `--scenario replan` 8/8, every turn answered by Sonnet.

## Lou wordmark and Abandon trip (branch `kyle/lou-wordmark-abandon-trip`, from `main` at `61851c6`)

Kyle asked for two frontend changes: the header says the guide's name, and one click (plus a confirmation) ends a trip and opens the page as on a first visit. Both files are Jan's. Revert: `git checkout 61851c6 -- index.html tests/frontend.test.cjs`.

- `index.html`, the header wordmark. The page `<title>` and every other mention of Scavagent are unchanged. Original:

  ```html
        <span class="wordmark">Scavagent</span>
  ```

- `index.html`, the composer footer. The hint line now shares a row with a small **Abandon trip** button. The button is hidden until there is a trip to end (a session and at least one message) and disabled while a reply is pending. Original:

  ```html
        <p class="help" id="composer-help">Type “ready”, “here”, “hint”, or “skip”, or change your plans.</p>
  ```

- `index.html`, added code; nothing existing was removed:
  - CSS: `.composer-foot`, `.abandon`, `dialog.confirm` and `.confirm-actions`, using the existing color tokens (`--fail` for Yes).
  - A `<dialog id="abandon-dialog">` that asks "Are you sure you want to end this trip?", with No and Yes buttons. No has focus when it opens, and Escape or No closes it without changing anything.
  - Script: two lines in `refreshControls()`, and an "Abandoning the trip" section. `abandonTrip()` removes `scavagent.session_id` and `scavagent.pending_message.v1` from localStorage, calls the existing `POST /clear?session_id=` (with a 10 s timeout, and errors ignored), then reloads the page. With no stored session, the page loads the way it does on a first visit: the welcome screen and an empty chat. The device forgets the session before the server call, so even if the server can't be reached, the trip is still gone from this phone.
- `tests/frontend.test.cjs`: the fake `Element` has `showModal()` and `close()`, the harness knows the four new ids, and `window.location.reload` is counted. Three tests were added:
  - the button appears only with a trip and is disabled during a reply;
  - No keeps the trip; Yes posts `/clear`, removes both keys and reloads, and a fresh page then makes no requests and shows the welcome;
  - Yes still starts fresh when `/clear` fails.

## The guide's name on the page and in the instructions (branch `claude/lou-name`, from `main` at `2457b93`)

Jan asked for the user-facing "Scavagent" mentions to say Lou, after #38 changed the header. "Scavagent" stays the project's name: the repository, the service, environment variables, storage keys, the HTTP User-Agent, and the docs.

- `agent.py` (Kyle's), the opening line only. Original: "You are Lou, the dispatcher at Scavagent, a guide for playful, real-world NYC adventures run entirely in chat." Now: "You are Lou, a New York dispatcher who guides playful, real-world NYC adventures run entirely in chat." Every rule, including the Voice section, is unchanged. Revert: `git checkout 2457b93 -- agent.py`.
- Kyle's files, first-line docstrings only: `agent.py` ("Scavagent's agent instructions" → "Lou's agent instructions"), `adventure/validation.py` ("Scavagent's original planning tool" → "Lou's original planning tool"), and `scripts/acceptance_checks.py` ("against a running Scavagent" → "against a running Lou"). No code changed.
- Jan's files: `index.html` (the `<title>` and the meta description); `README.md` (the title is now "# Lou", the intro starts "Lou is a chat agent", and three sentences where the agent acts say Lou); and the first-line docstrings of `schemas.py` and `integrations/cameras.py`. Jan asked to rebrand the README and these docstrings as Lou. The repository, the URL, the other docs, and the identifiers above keep "scavagent".

## The Abandon trip test stores an unsent message (branch `kyle/abandon-test-pending`, from `main` at `b08d26b`)

Jan's review of #38 found a gap: "No keeps the trip; Yes clears it…" checked that the unsent message was removed, but its setup never stored one, so that half passed without testing anything. The test now sends "ready" first and gets a 400, so the message stays stored for a retry. It checks that No leaves the message stored and that Yes removes it. If `abandonTrip()` stops removing `scavagent.pending_message.v1`, this test now fails (checked by removing that line). No other test and no page code changed.

- `tests/frontend.test.cjs`, that test's first lines. Revert: `git checkout b08d26b -- tests/frontend.test.cjs`. Original:

  ```js
    const h = harness({ [SESSION]: TRIP }, [tripSoFar(), response({ status: "ok" })]);
    await settled();
    click(h, "abandon");
  ```

  and, after `assert.equal(h.storage.get(SESSION), TRIP);`, there was no check of `PENDING`.
