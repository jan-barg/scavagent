# Implementation status

Initial handoff recorded September 28, 2026. This file records observed implementation, not scheduled completion. Each owner updates their own section after a meaningful change; inspect the checkout before relying on old notes.

## Current state (September 30, 2026)

- **Team:** Jan Barganowski (`jgb2170`) and Kyle Coletta (`kc3936`).
- **Repository:** https://github.com/jan-barg/scavagent (public). The course starter was imported unchanged at `8811dcc37142bfa8e61e5422a9b959a7616316d0`. Stack: Python/FastAPI, LiteLLM, plain HTML/JavaScript.
- **Deployed:** https://scavagent-b57mvtutma-ue.a.run.app, Cloud Run `scavagent` in `agentic-ai-msds`, us-east1. Continuous deployment from `main`: every merge builds and deploys a new revision, so the live site is always the latest `main`. Checked September 30: `scavagent-00027-mrj` from `922321f` (#29).
- **Model:** `SCAVAGENT_MODEL=anthropic/claude-sonnet-5-5`, with `ANTHROPIC_API_KEY` from Secret Manager (`anthropic-api-key`). If Claude can't answer, Gemini 3.5 Flash-Lite on Vertex AI answers (the fallback added in #20). Why Sonnet: [MODEL_COMPARISON.md](MODEL_COMPARISON.md).
- **Storage:** Firestore for sessions, Cloud Storage `agentic-ai-msds-scavagent-photos` for photos, SQLite locally. The Routes API is enabled on `agentic-ai-msds` for transit legs.
- **Cameras:** 151 `image_verified` standing spots on 102 Manhattan cameras. None is `field_verified` yet.
- **Checks on `main` after the cleanup (#29), network blocked:** `uv run --frozen pytest -q` passes 419, and `node --test tests/frontend.test.cjs` passes 36. The cleanup removed tests only together with the code they covered.
- **Required files** are at the repository root: `app.py`, `pyproject.toml`, `uv.lock`, `README.md`, and `submission.json` (authors `jgb2170` and `kc3936`).
- **Plan to submission:**
  - feature freeze October 2 (bug fixes only after it);
  - walk test October 3–4 ([WALK_TEST.md](WALK_TEST.md));
  - fixes October 5;
  - release check October 6;
  - submission October 7, then no merges while grading runs.

## Jan workstream

| ID | Task | Status | Depends on |
|---|---|---|---|
| J1 | Shared schemas, fixture examples, tool-result convention | Done. Merged in #1. `schemas.py`, `fixtures/`, and the `tool_ok`/`tool_error` result convention; the trace is kept when a later model call fails. `PhotoAsset.checkpoint_id` is the `CameraCheckpoint` id. Later additive fields: story design v2 (#13) and `destination_reached_at` (#15) | — |
| J2 | App/session persistence and compatible tool traces | Done. Merged in #2, with harness work in #7, #8, #14, #15, #20 and #25 (#24 from Kyle): model-call retries within the turn, an empty reply asked for once more, a final answer after the last tool round, finishing only after the destination is reached, Claude through Anthropic with a Gemini fallback, a round of lookup tools run at once. The original J2 notes: `state.py` (SQLite locally, Firestore + Cloud Storage via `SCAVAGENT_STORE=firestore`), optimistic save versions, `client_message_id` replay, client location, `/history`, `/media/{asset_id}`, session-bound `get_adventure_state`/`update_adventure_state`, dev-only `load_dev_adventure`. After two Codex reviews: a resend while the first turn runs gets 409 instead of a second run (token-owned claims stored apart from the session; no model call or tool starts after a 4-minute turn deadline); a captured photo is saved immediately and survives a turn that dies; a Firestore save retried after a lost race no longer overwrites the winner; provider error text stays in the server log. 52 pytest checks pass; a mutation check planted 73 single bugs across app.py, state.py, tools.py, and schemas.py and the suite caught 72 (the survivor removes a redundant replay check, which changes no behavior); a live Gemini run loaded the dev hunt, recorded a check-in, and resumed after a store restart. Against the real Firestore database and bucket (September 28): save/reload, stale-save rejection, photo bytes, claim/release/expiry with token-owned release, and a mid-turn photo save followed by the end-of-turn save all passed. Camera tools registered on `jan/camera-registration`: session-bound capture that reuses a photo already taken for the same message, and a finder that rejects server-only arguments | — |
| J3 | Camera adapter, saved images, calibrated catalogue, original finder | Done except field verification. #3, #4, #17, #18, #22: 151 `image_verified` spots on 102 cameras, a live capture checked on the deployed site (below), and the evaluator's own camera search from #24 and #26. Open: a capture with a person in frame, and upgrading that spot to `field_verified` (the walk test). The original J3 notes: bounded DOT adapter, pedestrian point/corridor selector, session-injected capture, exports, and three disabled UWS candidates. Live September 28 probe: 1,064 catalogue rows; `isOnline` strings; CPW/86th JPEG, 18,631 bytes; no established frame time. Observations in `data/camera_catalogue.json`. 62 mocked camera checks pass (118 total pytest checks). September 29 regression coverage isolates streamed byte limits without Content-Length (at/over the limit), max_distance_m with default detour, both corridor segment ends, and absolute media URLs with a /media path; all four corresponding in-memory mutations were caught. No implementation changes were needed. No field-verified positions or participant photos | Registration and per-message capture replay are merged in PR #4; calibrate standing positions before enabling. Pending candidates are separate because `CameraCheckpoint.stand_location` cannot be null |
| J4 | Chat presentation, foreground location and resume | Done. #3, #9–#11, #23 (Codex, reviewed by Claude). #23 made a rejected message say to edit and send again, and added tests that Send stays blocked while a reply is pending. 38 Node checks pass. Open: a deliberate pass on a phone (the walk test) and Safari. The original J4 notes: mobile chat, DOM-safe Markdown/media, foreground GPS, client-created session UUID and persisted pending message, legacy recovery, and reply session matching. September 29 recovery: transient failures retry sequentially every 5 seconds for up to 4.5 minutes; manual Send cannot overlap. Reload matches an assistant history entry by client_message_id to clear an already completed turn without resending or duplicating it; unfinished turns resume automatically with the same IDs. After the window, the composer is editable: unchanged text retries the same turn, edited text starts a new one. Links retain balanced URL parentheses; failed plan checks show failed; tool activity discloses +N more beyond 12 entries and marks compacted route verdicts as checked. History timeouts cover body reads; malformed links do not rescan the same text; permanent errors retain the manual retry ID. 33 deterministic Node checks pass, including lost replies, 409s, retry expiry, reload reconciliation, GPS/media/session boundaries, links, and tool labels. `uv run --frozen pytest -q`: 272 passed (2 dependency deprecation warnings) | Camera registration and capture replay are merged in PR #4. Claude reviews/merges the frontend recovery PRs; deployed Safari verification, full camera integration, and phone field tests remain |
| J5 | Cloud Run setup and GitHub continuous deployment | Done. Continuous deployment was connected September 29: a merge to `main` builds and deploys. Env: `SCAVAGENT_STORE=firestore`, `SCAVAGENT_ASSET_BUCKET`, `SCAVAGENT_MODEL=anthropic/claude-sonnet-5-5`, and `ANTHROPIC_API_KEY` from Secret Manager; runtime service account `scavagent-run`. First live checks (September 28): the page, the `/chat` shape, tools, session memory and isolation, `client_message_id` replay, `/history`, 404s. Changing the model: [DEPLOY.md](DEPLOY.md) | — |
| J6 | Final photo recap, recovery checks, deployment documentation | Mostly done. The finale's case file shows the saved photo, and the deployed UI rendered it after a reload (deployed check below). Recovery is covered by server tests (claims, replay, deadline) and Node checks (retry, reload, resume). Deployment is documented in [DEPLOY.md](DEPLOY.md). Open: one deliberate recovery pass on the deployed site at the October 6 release check | — |

Camera checkpoint, deployed check (September 29, 6:15–6:35 PM, revision `scavagent-00018-r28`, Claude Sonnet 5.5):
- Grader query 2 found the Amsterdam @ 72 St spots with live stills. The saved plan put a `camera_capture` stop at the Broadway and 72nd Street viewpoint before the finish, passed the evaluator, and the reply disclosed the image-only verification.
- Skipping the two optional stops moved their clues into chat.
- "in position" captured a real still (25,296 bytes, overlay 6:27:29 PM, retrieved 22:27:30Z); `/media` served it as image/jpeg.
- "I can't see myself" recorded `set_photo_visibility` as `user_reported_not_visible`.
- On arrival the case file showed the photo, and the deployed chat UI rendered it after a reload.
- Not verified: a capture with a person in frame, which needs someone standing there.

Camera catalogue (September 29, night; merged as #18): Jan reviewed the 227 drafts in the workbench and approved 151 spots on 102 cameras (150 drafts approved as drafted, 1 edited), skipping 48 cameras. A Codex review found 4 flawed ones (1 Ave @ 40 St: the pin description contradicted the instructions because `describe()` judged by centerlines on a very wide avenue; Herald Square: a three-street camera name became an invented street); they are listed in the catalogue's `excluded_workbench_spots` and re-drafted for Jan's re-approval. `import-spots` added the other 147 to `data/camera_catalogue.json` as enabled `image_verified` checkpoints, each with a `field_log` record (evidence still SHA-256, reviewer, review time, workbench spot id, Claude draft id). No position is `field_verified`. The branch also makes the OSM geometry robust (error-remark fallback, alternate names, name-suffix cleaning, and a grid fallback that the import never called; the fallback was removed in #29). 383 pytest (also network-blocked) + 33 frontend pass. Jan approved the four re-drafts, which were added in #22: 151 spots in total.

Camera checkpoint work (September 29, evening; merged as #17): Jan chose image-based calibration over field visits. New `image_verified` status (schema, finder, capture, evaluator; the agent tells visitors the spot was checked on the camera image and offers a retake). `integrations.cameras.load_checkpoints()` is the public catalogue read (Kyle's `camera_lookup` uses it). All 308 online Manhattan street cameras were pulled and screened (259 usable, stills in `agent-handoffs/camera-evidence/`, outside Git); a private workbench page lists them, Jan marks or approves spots, and `scripts/calibrate_camera.py import-spots` turns them into catalogue entries (address, stop name, and side of street derived from the pin with `scripts/camera_geometry.py`). Claude drafts spots from the stills for Jan's review (pilot: 17 drafts on 9 cameras; the Park Ave @ 116 St draft landed 1.7 m from Jan's own pin). Planner fixes so a found camera stop stays in query 2's plan (prompt, draft check, camera-last cut suggestions; recorded in `docs/SHARED_FILE_EDITS.md`), re-applied onto story design v2 at the merge. Edits to Codex's `data/camera_catalogue.json` come only from Jan's approved spots. 351 pytest + 33 frontend checks pass. Since done: Jan's review, the import (#18, #22), the deploy, and the live capture check (above).

Latest changes/checks (September 29): J1–J4, camera registration, and Kyle's workstream are merged, plus Codex's retry and reload fixes (#9–#11); continuous deployment is connected. The Routes API is enabled on `agentic-ai-msds` (transit legs no longer fall back to walking). Story design v2 (#13) and the README and `submission.json` (#16) are merged. Models (PR #14, `jan/claude-models`): `SCAVAGENT_MODEL` works for Claude through Anthropic's API (Vertex gives this project no Claude quota) and for the Vertex open models; a model's reasoning is replayed only within its own turn; malformed tool arguments no longer break a session; 500/529 errors retry; the server context rides with the newest message so follow-ups read the conversation from cache (steady follow-ups about 40% cheaper); optional planner/guide split (`SCAVAGENT_PLANNER_MODEL`, removed in #29 after the decision below). Comparison in `docs/MODEL_COMPARISON.md`: with story design v2, Claude Sonnet 5.5 and Opus 5.5 pass 26/26 and write the best stories. Jan's decision (September 29): Claude Sonnet 5.5 alone (Opus too expensive; Sonnet planning with a Flash-Lite guide saved $0.10–0.15 per adventure but delivered each stop noticeably worse). Live since September 29 (evening): PR #14 merged (`cf0d55e`), and revision `scavagent-00016-z6s` runs `SCAVAGENT_MODEL=anthropic/claude-sonnet-5-5` with `ANTHROPIC_API_KEY` from Secret Manager (`anthropic-api-key:latest`, version 2; version 1 disabled). `scripts/acceptance_checks.py` against the deployed URL: 26/26, longest turn 54 s. To go back to Gemini, set `SCAVAGENT_MODEL=vertex_ai/gemini-3.5-flash-lite` on the service. Automatic fallback (#20): when Claude reports no credit, rejects the key, cannot be reached, or stays overloaded, Gemini 3.5 Flash-Lite answers that turn and the next 5 minutes; checked live with an invalid key. The story redesign follow-ups, and the camera review and deploy, have since landed (#17–#26). Physical-feature challenges stay parked: not before submission.

Latest changes/checks (September 30):
- Reviewed and merged Kyle's #24 (cameras, depth, guiding, speed) and #26 (review fixes), and Codex's #23 (frontend).
- #25 (Claude): `app.py` now keeps the results of a lookup round that finishes past the 240 s turn deadline; before, they were reported as "not run". Tests failed on the old code for each fix, and planted bugs in #24 and #26 were caught (11 of 12 in #24; the survivor was covered by #26).
- Deployed: revision `scavagent-00024-ks8` from `f19af1f`. No model calls were made against the deployed agent.
- Kyle's #28 (double negatives ask for a camera; his STATUS section) was reviewed and merged.
- Cleanup (#29, reviewed by Claude and merged, deployed as `scavagent-00027-mrj`): removed code, schema fields, and fixtures that nothing used; the optional planner/guide split (`SCAVAGENT_PLANNER_MODEL` is no longer read); the unused grid fallback in `scripts/camera_geometry.py`; and the pre-#3 pending-message migration in `index.html`. Codex reviewed each change as safe. With the network blocked, `uv run --frozen pytest -q` passed 419 and `node --test tests/frontend.test.cjs` passed 36. Kyle's files that changed are listed in [SHARED_FILE_EDITS.md](SHARED_FILE_EDITS.md).
- Skipping before the start (#32, Jan's live run `cc521363`): the user cut a 3-stop plan to its last stop before starting, the model started and skipped two stops, and the story arrived as a bare catch-up with an unintroduced character. `update_adventure_state` now refuses `skip_checkpoint` while the adventure is proposed, or started in the same message with nothing done yet (that start is undone), and tells the model to re-plan with kind "new". Kyle's prompt and evaluator follow-ups are in issue #32.
- Jan's decision (September 30): no AI-disclosure line on the page. This is an internal class project, and the course staff know it is an AI.

## Kyle workstream

Everything below is merged and deployed (#6, #13, #24, #26, #28).

| ID | Task | Status |
|---|---|---|
| K1 | Research, geocoding, routing | `geocode_place` looks places up in Overpass (the mirror is asked when the main server is slow), NYC GeoSearch, and Nominatim. `find_places` searches each key term separately and reports `matched_terms`. `research_place` returns claims pinned to Wikipedia revisions and LPC records. `get_route` and `get_walking_times` route on foot (Valhalla or OSRM) and by subway or bus (Google Routes), counting the wait for the train. |
| K2 | Agent planner | `agent.py` covers planning, guiding, changes mid-adventure, and the ending. The model can save only a plan that passed the evaluator. `get_next_directions` gives each leg, with the spot's instructions at a camera stop. |
| K3 | Original tool: `evaluate_adventure_plan` | `adventure/` has 48 violation codes covering time, the user's request (including a camera stop they asked for), route data, physical honesty, sources and real people, the story design, the number of stops, and revisions. [TOOLS.md](TOOLS.md) has its section for graders. |
| K4 | Story, activities, hints, replanning | Story design v2 (#13) adds a briefing, a cast, clues that add up, and theme links. Depth (#24) adds 4 stops for two hours and clues in words. A revision keeps completed stops and revealed clues. |
| K5 | MTA arrivals, filming records | `get_transit_arrivals` reads live GTFS-realtime and alerts, and refuses stale feeds. `find_filming_records` reports the data's coverage dates and refuses dates after them. |
| K6 | Tool docs, grader examples, checks | [TOOLS.md](TOOLS.md), [CONTRIBUTIONS.md](CONTRIBUTIONS.md), and [WALK_TEST.md](WALK_TEST.md). Three check scripts: `scripts/acceptance_checks.py` (28 checks), `scripts/guiding_checks.py` (18), and `scripts/camera_checks.py` (11). |

Checks:

- **#24**, on Sonnet 5.5 with a local server ($2.55 of Anthropic credit):
  - Acceptance passed 28/28 twice, guiding 18/18, and camera 11/11.
  - The two-hour Strokes query got 4 stops, with clues that are names and places.
  - Planning turns took 43–72 s after lookups began running at once (45–95 s before).
- **#26:** a camera the user turns down, or one asked for in an earlier adventure, no longer triggers the evaluator's camera search. A test shows the Nominatim lock keeps concurrent lookups a second apart; it fails without the lock.
- **#28:** double negatives such as "Don't forget the camera stop!", "not to miss", and "never skip" count as asking for a camera. `uv run --frozen pytest -q` passes 424 with the network blocked.

Still open:

- The walk test with Jan, October 3–4, following [WALK_TEST.md](WALK_TEST.md). Fixes go in on October 5.
- The release check, October 6, including the deployed acceptance run (with Jan's go, since it costs money).
- Known limits:
  - The evaluator can't see chat replies. Links and answers that would give a puzzle away rely on the instructions, which Sonnet followed in every run.
  - The Gemini fallback plans thinner stories, and in local runs it couldn't plan query 2 with its camera.
  - Overpass answers in 5–15 s under load.
  - A redraft rewrites the whole plan. Sending only the changes waits until after submission, because it needs a change to `state.EvaluatedDraft`.
- Cleanup: done in #29. Kyle's files that changed are listed in [SHARED_FILE_EDITS.md](SHARED_FILE_EDITS.md).

## Joint release work

- [x] Calibrate at least three usable camera viewpoints: 151 `image_verified` standing spots on 102 Manhattan cameras (#18, #22, Jan-approved). None is `field_verified` yet. Live capture was verified on the deployed site (September 29).
- [ ] Walk-test a pilot area, October 3–4: grader query 2 on a phone, following [WALK_TEST.md](WALK_TEST.md). It would also give a first in-frame souvenir and let its camera spot be upgraded to `field_verified`.
- [x] Integrate and test a complete deployed adventure. Deployed acceptance run: 26/26 (September 29, revision `00016`). Deployed query 2 run through the camera capture, "can't see myself", and the finale's photo (revision `00018`). Kyle's local Sonnet runs of #24 (acceptance 28/28, guiding 18/18, camera 11/11). The final deployed run is due at the October 6 release check, with Jan's go (it costs money).
- [ ] Test start-only requests, deadlines, required stops, skips, unavailable data, and story fallbacks. Covered so far by the acceptance script (Q1 start-only, Q2 deadline, required stop and camera, Q3 skip and re-time), `scripts/guiding_checks.py` (hint, wrong answer, give up, skip), and offline tests for unavailable data. Remaining: a deliberate pass on the deployed site on October 6.
- [ ] Test reload/lock/resume, two independent sessions, retries, and persistent images. Covered by server tests and Node checks; a photo survived a reload on the deployed site (September 29). Remaining: a deliberate pass on the deployed site on October 6.
- [ ] Document the two original tools and the contribution split. The evaluator has its own section in [TOOLS.md](TOOLS.md), and [CONTRIBUTIONS.md](CONTRIBUTIONS.md) gives the split. Remaining: the camera tools have only a table row in TOOLS.md, with no section like the evaluator's (Jan).
- [ ] Optional Spotify addition only after core testing passes. Not started. With the October 2 feature freeze, it would have to be decided now.
- [ ] Verify README examples, required files, actual authors, deployment URL, and continuous deployment. All were checked September 30 (the README model and camera-count lines were corrected). Re-check October 6.
- [ ] Submit October 7 (one person submits the repository URL on Courseworks), and keep the service reachable through grading.

## Decisions

Made:
- **Model:** Claude Sonnet 5.5 through Anthropic's API, with the Gemini 3.5 Flash-Lite fallback (September 29).
- **Infrastructure:** GCP project `agentic-ai-msds`, us-east1, Firestore and Cloud Storage. Routes API enabled.
- **Cameras:** image-based calibration; 151 approved spots.
- **Submission:** `submission.json` has the deployed URL and both UNIs. The repository is public, so no grader collaborator invites are needed.
- **AI disclosure:** none on the page (September 30).

Still open:
- Spotify: drop it, or build it before October 2.
- Who submits on Courseworks.
- The Anthropic credit balance has to last through grading. If it runs out, the Gemini fallback answers, noticeably worse.

Do not invent deployed URLs, credentials, working feeds, passing tests, or field-verification results to fill gaps. Record the actual limitation and continue independent work.
