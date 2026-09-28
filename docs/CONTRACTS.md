# Shared contracts — proposal for Phase 1

Status: documentation proposal, not implemented or frozen code. Jan is the suggested maintainer; both workstreams review changes. Implement the initial version as shared validated Python models plus small JSON fixtures, then update this document to match what ships.

## Integration boundaries

- `app.py` owns the HTTP request/session boundary and restores authoritative state.
- Agent behavior interprets the message and proposes tool calls, plan creation, or plan revisions.
- Tools receive a server-created session context. A model-generated argument never decides which user's state to access.
- State operations validate and persist allowed changes atomically using the current version. The agent must not keep a separate mutable progress store.
- `tools.py` stays the central registry/dispatcher initially. Put adapters in a differently named package such as `integrations/`; avoid ambiguity between `tools.py` and a `tools/` package.
- Proposed module paths are `schemas.py`, `agent.py`, `state.py`, `integrations/cameras.py`, `integrations/research.py`, `integrations/routes.py`, `integrations/mta.py`, `integrations/filming.py`, and `adventure/validation.py`. These are future organization suggestions, not existing files.

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

Use explicit success/failure, data, warnings where useful, and an error with `code`, `message`, `retryable`, and an actionable next step. A failure is not a fabricated empty success. Data freshness and whether an answer is live, historical, scheduled, or user-reported should be visible when relevant.

Suggested error cases: `INVALID_ARGUMENT`, `NO_MATCH`, `UPSTREAM_UNAVAILABLE`, `STALE_DATA`, `OUTSIDE_COVERAGE`, `PLAN_INFEASIBLE`, `MISSING_EVIDENCE`, and `STATE_VERSION_CONFLICT`. These are proposed labels to normalize, not a reason to build a large framework.

The evaluator checks actual route/dwell totals, required stops, allowed modes, evidence references, and activity/story dependencies. It returns concrete violations and estimates. It cannot certify arbitrary factual accuracy merely because a source URL was present.

## State writes

Prefer specific operations such as creating a validated plan, completing/skipping/blocking a checkpoint, recording a clue reveal, changing a stated constraint, attaching a saved photo, or replacing remaining stops. Avoid unrestricted model-generated JSON patches.

Use expected-version checks for concurrent changes. Replayed messages must not duplicate captures, completions, or later Spotify playlist creation. Previously completed stops, photos, and revealed facts survive a remaining-route revision. A user can waive a required stop explicitly; an agent cannot silently drop it to make a route fit.

Photo bytes belong in asset storage. Auth/session boundaries apply to their retrieval. A media URL must keep showing the saved frame even after the live camera changes. Avoid passing raw images, full source pages, or the entire candidate search history on every turn.

## Phase 1 fixtures

Build three small synthetic fixtures with explicit fixture provenance:

1. A start-only request and a short adventure proposal.
2. A route with a destination, deadline, and a required stop.
3. A revision after skipping an optional checkpoint and reducing available time.

Include a separately labeled camera adapter fixture for development. Do not describe synthetic coordinates, test images, or placeholder access notes as field-verified. Replace fixtures in the live product path with real results by Phase 2.
