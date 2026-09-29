# Implementation status

Initial handoff recorded September 28, 2026. This file records observed implementation, not scheduled completion. Each owner updates their own section after a meaningful change; inspect the checkout before relying on old notes.

## Established

- GitHub repository: https://github.com/jan-barg/scavagent
- Starter imported unchanged at commit `8811dcc37142bfa8e61e5422a9b959a7616316d0`.
- Existing source: `app.py`, `tools.py`, `index.html`, `pyproject.toml`, `uv.lock`, `.gitignore`, and the preserved starter README.
- Stack: Python/FastAPI, LiteLLM, plain HTML/JavaScript. Current example tool: Open-Meteo weather.
- Starter syntax/TOML was checked when imported. A successful live model call, dependency installation, Cloud Run deployment, or adventure feature is not established by those checks.
- Public NYC DOT catalogue and one camera image were retrieved successfully earlier in the project. Exact participant viewpoints remain uncalibrated.
- Product plan, role split, assignment reference, shared-contract proposal, and agent briefs are prepared in this documentation change.

## Jan workstream

| ID | Task | Status | Depends on |
|---|---|---|---|
| J1 | Shared schemas, fixture examples, tool-result convention | Merged to `main` (PR #1, September 28): `schemas.py`, `fixtures/`, tools/harness on the result convention, trace kept when a later model call fails; 17 pytest checks pass; one live local `/chat` call (Vertex Gemini + Open-Meteo) returned the new trace shape. Clarified on the J2 branch: `PhotoAsset.checkpoint_id` is the `CameraCheckpoint` id | Kyle review of `docs/CONTRACTS.md` |
| J2 | App/session persistence and compatible tool traces | Merged to `main` (PR #2, September 28): `state.py` (SQLite locally, Firestore + Cloud Storage via `SCAVAGENT_STORE=firestore`), optimistic save versions, `client_message_id` replay, client location, `/history`, `/media/{asset_id}`, session-bound `get_adventure_state`/`update_adventure_state`, dev-only `load_dev_adventure`. After two Codex reviews: a resend while the first turn runs gets 409 instead of a second run (token-owned claims stored apart from the session; no model call or tool starts after a 4-minute turn deadline); a captured photo is saved immediately and survives a turn that dies; a Firestore save retried after a lost race no longer overwrites the winner; provider error text stays in the server log. 52 pytest checks pass; a mutation check planted 73 single bugs across app.py, state.py, tools.py, and schemas.py and the suite caught 72 (the survivor removes a redundant replay check, which changes no behavior); a live Gemini run loaded the dev hunt, recorded a check-in, and resumed after a store restart. Against the real Firestore database and bucket (September 28): save/reload, stale-save rejection, photo bytes, claim/release/expiry with token-owned release, and a mid-turn photo save followed by the end-of-turn save all passed. Camera tools registered on `jan/camera-registration`: session-bound capture that reuses a photo already taken for the same message, and a finder that rejects server-only arguments | — |
| J3 | Camera adapter, saved images, calibrated catalogue, original finder | Implemented on `jan/camera-frontend`: bounded DOT adapter, pedestrian point/corridor selector, session-injected capture, exports, and three disabled UWS candidates. Live September 28 probe: 1,064 catalogue rows; `isOnline` strings; CPW/86th JPEG, 18,631 bytes; no established frame time. Observations in `data/camera_catalogue.json`. 62 mocked camera checks pass (118 total pytest checks). September 29 regression coverage isolates streamed byte limits without Content-Length (at/over the limit), max_distance_m with default detour, both corridor segment ends, and absolute media URLs with a /media path; all four corresponding in-memory mutations were caught. No implementation changes were needed. No field-verified positions or participant photos | Registration and per-message capture replay are merged in PR #4; calibrate standing positions before enabling. Pending candidates are separate because `CameraCheckpoint.stand_location` cannot be null |
| J4 | Chat presentation, foreground location and resume | Implemented in standalone `index.html`: mobile chat, DOM-safe Markdown/media, read-only tool activity, fresh browser location, client-created session UUID stored before the first pending message, legacy null-session recovery, strict reply session matching, manual retry after 409, and history restore without text-only matching. 11 dependency-free Node regression checks pass for session persistence, lost first reply across reload, refreshed/omitted GPS, legacy pending messages, 409, wrong-session replies, repeated text, storage failure, and media safety. September 29: an invalid stored session such as ../x is replaced before history/chat requests and the replacement survives reload; removing validation in memory was caught. Earlier mobile browser mock checks passed | J2 contract is merged: history, capability media URLs, and in-flight claims. Camera registration and capture replay are merged in PR #4; full camera integration and phone field tests remain |
| J5 | Cloud Run setup and GitHub continuous deployment | Deployed, CD not connected. September 28: Jan deployed `jan/j2-session-persistence` (commit `1cca150`) from local source as revision `scavagent-00001-sxd` at https://scavagent-b57mvtutma-ue.a.run.app (public, runtime SA `scavagent-run`, `SCAVAGENT_STORE=firestore`). Live checks passed: page loads; `/chat` shape; weather tool call; memory within a session; a second session did not see the first; retried `client_message_id` returned the same reply once; `/history` restored 6 entries; unknown history/media ids 404. Not done: GitHub continuous deployment (PR #1 is merged; merge PR #2 first so `main` has the Dockerfile) | Merge PR #2; Jan's browser approval for the GitHub connection |
| J6 | Final photo recap, recovery checks, deployment documentation | Not started | J2–J5 |

Latest changes/checks: J1, J2, and J3/J4 (PR #3) are merged. Camera tools are registered on `jan/camera-registration` (112 pytest checks and 10 frontend checks pass). No camera position is field-verified yet, so live capture returns NO_MATCH until Jan/Kyle calibrate one. Next action: merge the registration PR, connect continuous deployment, then field-calibrate a pilot camera position.

## Kyle workstream

| ID | Task | Status | Depends on |
|---|---|---|---|
| K1 | Research/geocoding/routing API spikes and fixture outputs | Done on `kyle/workstream` (pushed, not merged): geocoding, places and research, walking and subway/bus routes, live-checked September 29 | Provider/configuration choice; independent of J1 implementation |
| K2 | Agent progression and dynamic planner | Implemented on `kyle/workstream`: `agent.py` instructions; the model plans with the tools and can save only a plan that passed the evaluator; `get_next_directions` guides each leg. All three grader queries ran end to end locally | J1 and K1, both in the branch |
| K3 | Original adventure evaluator | Implemented: `adventure/validation.py` behind `evaluate_adventure_plan`, about 30 violation codes, tests that break one rule at a time | — |
| K4 | Grounded story, activities, hints, coherent replanning | Implemented through the instructions, the revision builder (`adventure/drafts.py`), and the evaluator: completed stops and revealed clues kept, stranded clues moved to chat, waivers only from the user, time limits re-checked. Story quality varies with the model | K2/K3 |
| K5 | MTA arrivals and filming evidence with freshness handling | Implemented: `get_transit_arrivals` (live GTFS-realtime and alerts; refuses feeds over 10 minutes old) and `find_filming_records` (coverage dates; refuses dates after the data ends). Both live-checked | — |
| K6 | Tool documentation, example queries, planning acceptance checks | Implemented: `docs/TOOLS.md`, README grader section, `scripts/acceptance_checks.py` | A deployed URL for the final run |

Latest changes/checks (September 29, overnight):

- Branch `kyle/workstream` holds everything: Kyle's K1 commit, a merge of `main` at `9b54995` (J1–J5), then Kyle's work. It is pushed to GitHub as `kyle/workstream` (a push earlier in the night was blocked by local Claude Code permissions; the final one went through). Nothing new was on `main` at the last fetch (about 2:20 AM).
- Built:
  - Adapters on the shared schemas: `geocode_place`, `find_places`, `research_place` (claims pinned to Wikipedia revisions and LPC records), `get_route`, `get_walking_times`.
  - Transit: `integrations/transit.py` asks Google Routes for each leg, counts the wait for the train in door-to-door time, and caps and caches lookups. A leg rides only when that saves at least 4 minutes over a walk longer than 12. The Routes API is enabled on Kyle's project `kc3936-ieor4570-p1` (Kyle approved), and local runs bill it through `SCAVAGENT_ROUTES_PROJECT`.
  - Planning: `adventure/validation.py` (the evaluator), `adventure/drafts.py` (draft to routed plan, revisions), and `adventure/agent_tools.py` (`evaluate_adventure_plan`, `save_adventure_plan`, `get_next_directions`); `agent.py` (instructions).
  - Live data: `integrations/mta.py` (`get_transit_arrivals`) and `integrations/filming.py` (`find_filming_records`).
  - Documentation and checks: `docs/TOOLS.md`, `scripts/acceptance_checks.py`, `scripts/capture_integration_fixtures.py`, `fixtures/integrations/` (labeled live captures).
  - Edits to Jan's files, each with its original text and a revert command in `docs/SHARED_FILE_EDITS.md`: `tools.py` (registers Kyle's tool lists), `app.py` (agent prompt; tool rounds 8 → 16), `pyproject.toml`/`uv.lock` (`gtfs-realtime-bindings`), and the README grader section.
- Checks: `uv run pytest -q` passes 252 tests (Jan's 112 plus Kyle's 140); deliberately breaking key guards made their tests fail. An independent review of the new modules found 12 bugs, each confirmed with a script (for example, a clue told in chat for a skipped stop blocked every revision, and a revision could reuse a dropped stop's id so the new stop was never visited). All 12 are fixed, each with a regression test that fails on the earlier code. Final local acceptance run after the fixes (`scripts/acceptance_checks.py`, Gemini 3.5 Flash-Lite, in-memory store): 18 of 18 checks passed. Earlier runs hit Vertex AI `429` rate limits on `agentic-ai-msds` for both Flash-Lite and Flash after repeated runs.
- Found in live runs and now handled in code or instructions: invented props and hand-offs (`INVENTED_PROP`), a nearby landmark standing in for a required corner (`REQUIRED_MISMARKED`), real architects written into the plot (`REAL_PERSON_IN_FICTION`), broken invented links in plan text (`UNSOURCED_LINK`), completing a stop on arrival, finishing before the destination, "15 minutes left" read as a total budget, and a UTC time taken for New York time.
- Model choice (still open): Flash-Lite answers in about 3–60 s per turn but follows the rules less reliably; it still sometimes writes invented Wikipedia links in replies, which the evaluator cannot see. Gemini 3.5 Flash followed the rules better (honest options at query 3, correct links) but took 15–130 s per turn and hit `429` rate limits. Gemini 3.5 Pro is not available on the project.
- For Jan:
  1. Review and merge `kyle/workstream`, using `docs/SHARED_FILE_EDITS.md`.
  2. Transit on Cloud Run: enable `routes.googleapis.com` on `agentic-ai-msds`, so the runtime service account can call it with no key; or set `SCAVAGENT_ROUTES_PROJECT` or `GOOGLE_MAPS_API_KEY` on the service. Without either, legs fall back to walking with a warning.
  3. Harness: retry Vertex `429`s with backoff inside `TURN_SECONDS` (for example LiteLLM `num_retries`), and retry once when the model returns neither text nor a tool call (seen once).
  4. State guards to consider: refuse `complete_checkpoint` for a checkpoint other than the current one, and `finish_adventure` while a destination remains. The instructions ask for both, but the model slipped in earlier runs.
  5. A public accessor for the camera catalogue: `adventure/agent_tools.camera_lookup` uses `cameras._checkpoints`.
- Next: push and merge; deploy and run `scripts/acceptance_checks.py <deployed URL>`; field-calibrate a camera position so a camera stop can pass the evaluator; walk-test a generated hunt; decide the model.

## Joint release work

- [ ] Choose and walk-test a pilot area; calibrate at least three usable camera viewpoints.
- [ ] Integrate and test a complete deployed adventure.
- [ ] Test start-only requests, deadlines, required stops, skips, unavailable data, and story fallbacks.
- [ ] Test reload/lock/resume, two independent sessions, retries, and persistent images.
- [ ] Document the two original tools and contribution split.
- [ ] Optional Spotify addition only after core testing passes.
- [ ] Verify README examples, required files, actual authors, deployment URL, and continuous deployment.
- [ ] Submit October 7 and keep the service reachable through grading.

## Decisions or access still needed

- Final model/provider is deferred; verify the starter configuration when first running it.
- Maps/search providers and credentials; GCP project, region, storage/database configuration.
- Pilot neighborhood and calibrated pedestrian viewpoints.
- Final deployment URL and both Columbia UNIs/emails for `submission.json`.
- Spotify account ownership/login approach and access test, only in its final optional phase.

Do not invent deployed URLs, credentials, working feeds, passing tests, or field-verification results to fill gaps. Record the actual limitation and continue independent work.
