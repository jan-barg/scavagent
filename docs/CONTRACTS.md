# Shared contracts — proposal for Phase 1

Status: the first version is implemented in [`schemas.py`](../schemas.py) with labeled fixtures in [`fixtures/`](../fixtures/) (J1). Jan maintains it; both workstreams review changes. Change the code and this document together.

### What shipped in J1

- Every record in the table below is a Pydantic model in `schemas.py`. Unknown fields are rejected, timestamps must be timezone-aware, and cross-references are checked: a plan cannot reference an unknown place, claim (evidence), story beat, or leg endpoint.
- Progress lives only in `AdventureState` (completed/skipped/blocked ids, revealed beats, photos). A `Checkpoint` inside a plan has no status field, so a plan version stays immutable.
- `RouteLeg.from_id`/`to_id` is a checkpoint id or one of `start`, `current_location`, `destination`. A revision routes from `current_location`.
- `AdventureRequest.defaulted_fields` names the fields filled by app defaults rather than stated by the user.
- `Activity` requires a non-empty `fallback`; a `verified_feature` activity requires `evidence_ids`; a `camera_capture` checkpoint requires `camera_checkpoint_id`. `Claim.basis` is `source`, `field_verified`, or `user_reported`; a `source` claim must cite a URL.
- `CameraCheckpoint.verification_status` is `field_verified`, `unverified`, or `synthetic_fixture`; `field_verified` requires `last_field_verified_at`.
- Tools return `tool_ok(...)`/`tool_error(...)` dicts (see "Tool result conventions"). The `/chat` trace keeps `result` as that object; the model receives it as JSON text.
- `ChatRequest` accepts the optional `location` (`LocationContext`) and `client_message_id` fields and ignores unknown fields. J2 wires them into persistence and replay protection.
- Fixtures: `fixtures/start_only.json`, `constrained_route.json`, `revision_after_skip.json`, and `cameras.json`, loaded with `from fixtures import load_scenario, load_cameras`. Each carries `provenance.kind = "synthetic_fixture"` and `field_verified = false`.

## Integration boundaries

- `app.py` owns the HTTP request/session boundary and restores authoritative state.
- Agent behavior interprets the message and proposes tool calls, plan creation, or plan revisions.
- Tools receive a server-created session context. A model-generated argument never decides which user's state to access.
- State operations validate and persist allowed changes atomically using the current version. The agent must not keep a separate mutable progress store.
- `tools.py` stays the central registry/dispatcher initially. Put adapters in a differently named package such as `integrations/`; avoid ambiguity between `tools.py` and a `tools/` package.
- `schemas.py` exists. Proposed remaining module paths are `agent.py`, `state.py`, `integrations/cameras.py`, `integrations/research.py`, `integrations/routes.py`, `integrations/mta.py`, `integrations/filming.py`, and `adventure/validation.py`. These are future organization suggestions, not existing files.

## Record shapes to agree

| Record | Minimum contents |
|---|---|
| `LocationContext` | Coordinates or resolved typed place; source (`browser`, `user`, or `geocoded`); observed time; accuracy when supplied |
| `AdventureRequest` | Required start; optional destination, deadline/duration, required stops and their dwell/windows, allowed modes, theme; explicit user preferences distinguished from defaults |
| `PlaceEvidence` | Place ID, address/coordinates, individual supported claims and source URLs, physical-feature evidence, access information, checked time and uncertainty |
| `CameraCheckpoint` | Checkpoint and camera IDs; participant standing coordinates; landmark/side-of-street instructions; reference view; verification time; availability and fallback |
| `Checkpoint` | Stable ID; place reference; user-required flag; activity type/payload; physical requirements/evidence references; answer rule; hints/fallback; dwell time; story beat; optional camera checkpoint reference |
| `RouteLeg` | From/to IDs; permitted and actual travel modes; departure/arrival estimates; duration; route instructions; retrieval time and uncertainty |
| `AdventurePlan` | Plan ID/version; constraints; ordered checkpoint IDs and route legs; evidence references; story outline/solution; estimated total, contingency, validation report |
| `AdventureState` | Version; status; active plan/current checkpoint; completed/skipped/blocked IDs; revealed beats; choices/user reports; latest location; photo references |
| `PhotoAsset` | Asset ID and authorized retrieval reference; camera/checkpoint IDs; retrieval time; frame time only when established; visibility confirmation status |

Use stable IDs throughout and timezone-aware timestamps. Interpret user-facing NYC times in `America/New_York`. Unknown source information remains null/unknown, not a model-created fact. A duration starts at the agreed start time; if departure is delayed, refresh feasibility rather than silently extending a hard arrival deadline.

## Implemented app and state interfaces (J2)

- `state.SessionRecord` is the one stored record per session: model messages, the chat transcript, `AdventureState`, plans by id, `PhotoAsset`s by id, and replies cached by `client_message_id`. Saves check `storage_version`, so two concurrent turns cannot overwrite each other; the losing turn asks the user to resend.
- Retries: `/chat` claims the `client_message_id` in the store (`store.claim`, kept apart from the session record) before the agent runs, and releases it when the turn ends or fails; release needs the claim's token, so a late turn cannot free a successor's claim. A resend of a stored reply returns that reply; a resend while the first turn is still running gets HTTP 409 (retry later), so tools and captures do not run twice. A turn starts no model call or tool after `app.TURN_SECONDS` (4 minutes); a claim older than `state.IN_FLIGHT_TIMEOUT` (5 minutes) therefore belongs to a turn that has stopped or crashed, and the message runs again. The user's message is stored only when its turn finishes.
- Camera tools are registered in `tools.py`. `find_camera_checkpoints` accepts only its schema's arguments (anything else is `INVALID_ARGUMENT`). `capture_camera_checkpoint` is a session tool bound to `ctx.save_asset`; `save_asset` records the photo under the turn's `client_message_id`, so if a turn dies after a capture and the message runs again, the tool returns that photo with `already_captured: true` instead of fetching another. A new message is a real retake. The record is dropped once the message has a stored reply.
- Session ids: a client should create its own `session_id` (for example a UUID) before its first message, so a retry of a first message whose reply was lost reaches the same session. A request without one gets a new server-issued session.
- Tools that need the session take a server-built `state.ToolContext` as their first argument and are listed in `tools.SESSION_TOOLS`. `run_tool(name, args, ctx)` injects it; a model-supplied `session_id` argument is rejected.
- State operations, each returning a tool result: `state.save_plan(ctx, plan, activate=..., waived_required_ids=..., expected_version=...)`, `resolve_checkpoint` (completed/skipped/blocked), `reveal_beat`, `set_status`, `set_photo_visibility`. Activation, and any revision while active, requires `plan.validation.ok`. A revision must set `supersedes_plan_id`, keep completed checkpoints unchanged, keep revealed beats, and keep unresolved required stops unless waived. Repeating a completion is a no-op. Only the current checkpoint can be completed (later ones can be skipped ahead), and `finish_adventure` needs every checkpoint resolved; `abandon_adventure` ends early.
- Evaluated plans waiting for `save_adventure_plan` are stored in `SessionRecord.drafts` (at most `state.MAX_DRAFTS`, newest kept), so a save works after a restart or on another instance.
- Model calls: a Vertex `429`/`503` is retried after 2, 4, 8, 16 s while each wait fits inside the turn deadline, then reported; a reply with neither text nor a tool call is asked for once more.
- Model-facing: `get_adventure_state` (compact summary plus the current checkpoint in full) and `update_adventure_state(operation, ...)`. Kyle's planner saves plans through `state.save_plan` once `evaluate_adventure_plan` passes.
- Photos: a camera tool calls `ctx.save_asset(data, content_type, camera_id, checkpoint_id, source_url, retrieved_at, frame_time=None)`, which stores bytes, attaches the photo to progress, saves the session immediately (so the frame survives a turn that dies later), and returns a `PhotoAsset` with `media_url = /media/{asset_id}`. The camera tool must not attach it again.
- `PhotoAsset.checkpoint_id` is the `CameraCheckpoint` id, not the adventure stop id (agreed September 28). A plan stop links to it through `Checkpoint.camera_checkpoint_id`, which `get_adventure_state` lists per stop, so the recap can match photos to stops.
- `/media/{asset_id}` is a capability URL: the unguessable asset id alone grants access, so a plain `<img>` works after reload. It is not checked against the session.
- HTTP: `POST /chat` (request adds optional `location` and `client_message_id`), `GET /history?session_id=` returning `{"session_id", "messages": [{"role", "text", "tool_calls"?, "at"}]}`, `GET /media/{asset_id}`, `POST /clear?session_id=`.

## Preserve `/chat`

Retain request fields `message` and optional `session_id`. Proposed additive request fields are `location` and a `client_message_id` for safe retries. Decide their exact types in the shared schema.

Always retain the required response structure:

```json
{
  "response": "User-facing reply",
  "session_id": "server-issued-opaque-id",
  "tool_calls": [
    {
      "name": "tool_name",
      "args": {},
      "result": {
        "ok": true,
        "data": {},
        "error": null
      }
    }
  ]
}
```

Tool results must remain JSON-serializable. If the harness sends a serialized result to the model, keep the API trace consistently represented. Preserve traces from tools that already ran even if a later tool or model call fails. Never include secret credentials or cross-session data.

For the initial UI, render safe message text/Markdown and authorized image URLs within `response`. Any additional presentation fields must be optional and cannot replace the required fields. The full tool trace remains inspectable through the API; the chat can show concise read-only activity without exposing all future puzzle answers as ordinary narration.

## Tool result conventions

Every tool returns `{"ok", "data", "error", "warnings", "freshness"}`. `ok` is true exactly when `error` is null. An error has `code`, `message`, `retryable`, and `next_step` (what the model should do instead). `freshness.kind` is `live`, `scheduled`, `historical`, `user_reported`, `static_reference`, or `synthetic_fixture`, with optional `as_of` and `retrieved_at`. A failure is not a fabricated empty success.

Error codes: `INVALID_ARGUMENT`, `NO_MATCH`, `UPSTREAM_UNAVAILABLE`, `STALE_DATA`, `OUTSIDE_COVERAGE`, `PLAN_INFEASIBLE`, `MISSING_EVIDENCE`, `STATE_VERSION_CONFLICT`, plus `UNKNOWN_TOOL` and `INTERNAL_ERROR` from the dispatcher. Add a code in `schemas.ErrorCode` when a real case needs it.

The evaluator checks actual route/dwell totals, required stops, allowed modes, evidence references, and activity/story dependencies. It returns concrete violations and estimates. It cannot certify arbitrary factual accuracy merely because a source URL was present.

## State writes

Prefer specific operations such as creating a validated plan, completing/skipping/blocking a checkpoint, recording a clue reveal, changing a stated constraint, attaching a saved photo, or replacing remaining stops. Avoid unrestricted model-generated JSON patches.

Use expected-version checks for concurrent changes. Replayed messages must not duplicate captures, completions, or later Spotify playlist creation. Previously completed stops, photos, and revealed facts survive a remaining-route revision. A user can waive a required stop explicitly; an agent cannot silently drop it to make a route fit.

Photo bytes belong in asset storage. Auth/session boundaries apply to their retrieval. A media URL must keep showing the saved frame even after the live camera changes. Avoid passing raw images, full source pages, or the entire candidate search history on every turn.

## Phase 1 fixtures

Implemented in `fixtures/` (see above). The three synthetic scenarios are:

1. A start-only request and a short adventure proposal.
2. A route with a destination, deadline, and a required stop.
3. A revision after skipping an optional checkpoint and reducing available time.

`fixtures/cameras.json` is the separately labeled camera fixture. Do not describe synthetic coordinates, test images, or placeholder access notes as field-verified. Replace fixtures in the live product path with real results by Phase 2.
