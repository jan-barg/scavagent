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
| J3 | Camera adapter, saved images, calibrated catalogue, original finder | Implemented on `jan/camera-frontend`: bounded DOT adapter, pedestrian point/corridor selector, session-injected capture, exports, and three disabled UWS candidates. Live September 28 probe: 1,064 catalogue rows; `isOnline` strings; CPW/86th JPEG, 18,631 bytes; no established frame time. Observations in `data/camera_catalogue.json`. 56 mocked camera checks pass (108 total pytest checks after merging main). No field-verified positions or participant photos | Claude must register tools and bind session storage/idempotency; calibrate standing positions before enabling. Pending candidates are separate because `CameraCheckpoint.stand_location` cannot be null |
| J4 | Chat presentation, foreground location and resume | Implemented in standalone `index.html`: mobile chat, DOM-safe Markdown/media, read-only tool activity, fresh browser location, client-created session UUID stored before the first pending message, legacy null-session recovery, strict reply session matching, manual retry after 409, and history restore without text-only matching. 10 dependency-free Node regression checks pass for session persistence, lost first reply across reload, refreshed/omitted GPS, legacy pending messages, 409, wrong-session replies, repeated text, storage failure, and media safety. Earlier mobile browser mock checks passed | J2 contract is merged: history, capability media URLs, and in-flight claims. Claude still owns camera registration and capture replay after an interrupted turn; full camera integration and phone field tests remain |
| J5 | Cloud Run setup and GitHub continuous deployment | Deployed, CD not connected. September 28: Jan deployed `jan/j2-session-persistence` (commit `1cca150`) from local source as revision `scavagent-00001-sxd` at https://scavagent-b57mvtutma-ue.a.run.app (public, runtime SA `scavagent-run`, `SCAVAGENT_STORE=firestore`). Live checks passed: page loads; `/chat` shape; weather tool call; memory within a session; a second session did not see the first; retried `client_message_id` returned the same reply once; `/history` restored 6 entries; unknown history/media ids 404. Not done: GitHub continuous deployment (PR #1 is merged; merge PR #2 first so `main` has the Dockerfile) | Merge PR #2; Jan's browser approval for the GitHub connection |
| J6 | Final photo recap, recovery checks, deployment documentation | Not started | J2–J5 |

Latest changes/checks: J1, J2, and J3/J4 (PR #3) are merged. Camera tools are registered on `jan/camera-registration` (112 pytest checks and 10 frontend checks pass). No camera position is field-verified yet, so live capture returns NO_MATCH until Jan/Kyle calibrate one. Next action: merge the registration PR, connect continuous deployment, then field-calibrate a pilot camera position.

## Kyle workstream

| ID | Task | Status | Depends on |
|---|---|---|---|
| K1 | Research/geocoding/routing API spikes and fixture outputs | Walking and research adapters implemented on `kyle/k1-integration-spikes` (unmerged); transit routing blocked | Provider/configuration choice; independent of J1 implementation |
| K2 | Agent progression and dynamic planner | Not started | J1 and K1; a labeled development hunt can exercise progression first |
| K3 | Original adventure evaluator | Not started | J1; route estimates from K1 |
| K4 | Grounded story, activities, hints, coherent replanning | Not started | K2/K3 and shared state operations |
| K5 | MTA arrivals and filming evidence with freshness handling | Not started | Provider/feed checks and common tool contracts |
| K6 | Tool documentation, example queries, planning acceptance checks | Not started | Implemented behavior to document; checks can be designed earlier |

Latest changes/checks (2026-09-28):

- Environment: Kyle's gcloud default project and application-default-credentials quota project are `agentic-ai-msds`; Kyle's account was granted Vertex AI User and Service Usage Consumer there (deployment access unverified). After `uv sync`, `uv run app.py` returned a live `vertex_ai/gemini-3.5-flash-lite` (global) reply on `/chat` and completed the starter `get_weather` tool round trip with a valid trace.
- K1, on branch `kyle/k1-integration-spikes` (not merged): keyless adapters in `integrations/` return the CONTRACTS.md result envelope. `geocode_place` resolves intersections to the node both streets share in OpenStreetMap (Overpass, with a mirror fallback), addresses through NYC GeoSearch, and landmarks through Nominatim. `find_places` and `research_place` use Wikipedia (radius search, revision-pinned article text) and NYC Landmarks Preservation Commission landmark and building records. `get_route` and `get_walking_times` use FOSSGIS Valhalla, falling back to OSRM. `integrations/tool_specs.py` holds model-facing tool definitions ready to register.
- Checks: `uv run python -m unittest discover -s tests -t .` passes 29 offline tests covering fallbacks, error codes, provenance, and data-quality guards; breaking any of four guards makes its test fail. `uv run python scripts/capture_integration_fixtures.py` made live requests for the three CONTRACTS.md scenarios and wrote labeled captures to `fixtures/integrations/` (not field-verified). A scratch run of the starter's agent loop with these tools registered in-process, without changing `tools.py` or `app.py`, called geocode_place → find_places → research_place ×2 → get_route and answered with cited facts.
- Findings: Nominatim and GeoSearch do not resolve intersections. Manhattan sidewalks are unnamed in OpenStreetMap, so many turn instructions say "the walkway"; legs therefore also carry `via_streets` and a compass `heading`. LPC landmark points are tax-lot centers, so landmarks sharing one lot (the Met and the Arsenal) are withheld from distance ranking. Public Overpass and FOSSGIS servers are shared low-volume services; Overpass returned 429 and 504 during testing.
- Blocked: transit routing. Google Routes, Places, and Geocoding are not enabled on `agentic-ai-msds`, Kyle's roles cannot enable APIs, and no keyless NYC transit router was found. Options: course staff enable `routes.googleapis.com` (application-default credentials with that quota project would then work), a personal billing project with an API key, or walking-only for the release. Vertex AI grounding with Google Search and Google Maps works under Kyle's role, but it returns redirect links rather than source URLs, so it is kept out of the evidence path.
- For Jan (J1): replace the envelope helpers in `integrations/common.py` with the shared ones when they land. Register `TOOL_SPECS` and `TOOL_FUNCTIONS` in `tools.py`, sending the model a JSON string while keeping the dict in the `/chat` trace. `geocode_place` is a proposed addition to the PLAN tool list. These tools read only public data and need no session context.
- Next action: K2 progression over a labeled development hunt once J1's schemas and state operations land. Meanwhile: decide on transit, and try the LPC building database as a candidate source for architecture themes.

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
