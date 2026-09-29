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
| K1 | Research/geocoding/routing API spikes and fixture outputs | Not started | Provider/configuration choice; independent of J1 implementation |
| K2 | Agent progression and dynamic planner | Not started | J1 and K1; a labeled development hunt can exercise progression first |
| K3 | Original adventure evaluator | Not started | J1; route estimates from K1 |
| K4 | Grounded story, activities, hints, coherent replanning | Not started | K2/K3 and shared state operations |
| K5 | MTA arrivals and filming evidence with freshness handling | Not started | Provider/feed checks and common tool contracts |
| K6 | Tool documentation, example queries, planning acceptance checks | Not started | Implemented behavior to document; checks can be designed earlier |

Latest changes/checks: no application work yet. Next action: K1 while Jan implements J1; review the contract proposal and send concrete interface needs through the shared repository workflow.

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
