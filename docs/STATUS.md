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
| J4 | Chat presentation, foreground location and resume | Implemented in standalone `index.html`: mobile chat, DOM-safe Markdown/media, foreground GPS, client-created session UUID and persisted pending message, legacy recovery, and reply session matching. September 29 recovery: transient failures retry sequentially every 5 seconds for up to 4.5 minutes; manual Send cannot overlap. Reload matches an assistant history entry by client_message_id to clear an already completed turn without resending or duplicating it; unfinished turns resume automatically with the same IDs. After the window, the composer is editable: unchanged text retries the same turn, edited text starts a new one. Links retain balanced URL parentheses; failed plan checks show failed; tool activity discloses +N more beyond 12 entries and marks compacted route verdicts as checked. History timeouts cover body reads; malformed links do not rescan the same text; permanent errors retain the manual retry ID. 33 deterministic Node checks pass, including lost replies, 409s, retry expiry, reload reconciliation, GPS/media/session boundaries, links, and tool labels. `uv run --frozen pytest -q`: 272 passed (2 dependency deprecation warnings) | Camera registration and capture replay are merged in PR #4. Claude reviews/merges the frontend recovery PRs; deployed Safari verification, full camera integration, and phone field tests remain |
| J5 | Cloud Run setup and GitHub continuous deployment | Deployed with continuous deployment (connected September 29: a merge to `main` builds and deploys; env `SCAVAGENT_STORE=firestore`, `SCAVAGENT_ASSET_BUCKET`, runtime SA `scavagent-run`) at https://scavagent-b57mvtutma-ue.a.run.app. First live checks (September 28): page, `/chat` shape, tools, session memory and isolation, `client_message_id` replay, `/history`, 404s. Model switching: `docs/DEPLOY.md` "Changing the model"; Claude runs through Anthropic's API with the key in Secret Manager (commands there, not run yet) | Jan: pick the model, create the secret, update the service |
| J6 | Final photo recap, recovery checks, deployment documentation | Not started | J2–J5 |

Camera checkpoint, deployed check (September 29, 6:15–6:35 PM, revision `scavagent-00018-r28`, Claude Sonnet 5.5):
- Grader query 2 found the Amsterdam @ 72 St spots with live stills. The saved plan put a `camera_capture` stop at the Broadway and 72nd Street viewpoint before the finish, passed the evaluator, and the reply disclosed the image-only verification.
- Skipping the two optional stops moved their clues into chat.
- "in position" captured a real still (25,296 bytes, overlay 6:27:29 PM, retrieved 22:27:30Z); `/media` served it as image/jpeg.
- "I can't see myself" recorded `set_photo_visibility` as `user_reported_not_visible`.
- On arrival the case file showed the photo, and the deployed chat UI rendered it after a reload.
- Not verified: a capture with a person in frame, which needs someone standing there.

Camera catalogue (September 29, night; merged as #18): Jan reviewed the 227 drafts in the workbench and approved 151 spots on 102 cameras (150 drafts approved as drafted, 1 edited), skipping 48 cameras. A Codex review found 4 flawed ones (1 Ave @ 40 St: the pin description contradicted the instructions because `describe()` judged by centerlines on a very wide avenue; Herald Square: a three-street camera name became an invented street); they are listed in the catalogue's `excluded_workbench_spots` and re-drafted for Jan's re-approval. `import-spots` added the other 147 to `data/camera_catalogue.json` as enabled `image_verified` checkpoints, each with a `field_log` record (evidence still SHA-256, reviewer, review time, workbench spot id, Claude draft id). No position is `field_verified`. The branch also makes the OSM geometry robust (error-remark fallback, alternate names, name-suffix cleaning, grid fallback flagged as approximate). 383 pytest (also network-blocked) + 33 frontend pass.

Camera checkpoint work (September 29, evening; merged as #17): Jan chose image-based calibration over field visits. New `image_verified` status (schema, finder, capture, evaluator; the agent tells visitors the spot was checked on the camera image and offers a retake). `integrations.cameras.load_checkpoints()` is the public catalogue read (Kyle's `camera_lookup` uses it). All 308 online Manhattan street cameras were pulled and screened (259 usable, stills in `agent-handoffs/camera-evidence/`, outside Git); a private workbench page lists them, Jan marks or approves spots, and `scripts/calibrate_camera.py import-spots` turns them into catalogue entries (address, stop name, and side of street derived from the pin with `scripts/camera_geometry.py`). Claude drafts spots from the stills for Jan's review (pilot: 17 drafts on 9 cameras; the Park Ave @ 116 St draft landed 1.7 m from Jan's own pin). Planner fixes so a found camera stop stays in query 2's plan (prompt, draft check, camera-last cut suggestions; recorded in `docs/SHARED_FILE_EDITS.md`), re-applied onto story design v2 at the merge. Edits to Codex's `data/camera_catalogue.json` come only from Jan's approved spots. 351 pytest + 33 frontend checks pass. Open: Jan's review, import, deploy, live capture check.

Latest changes/checks (September 29): J1–J4, camera registration, and Kyle's workstream are merged, plus Codex's retry and reload fixes (#9–#11); continuous deployment is connected. The Routes API is enabled on `agentic-ai-msds` (transit legs no longer fall back to walking). Story design v2 (#13) and the README and `submission.json` (#16) are merged. Models (PR #14, `jan/claude-models`): `SCAVAGENT_MODEL` works for Claude through Anthropic's API (Vertex gives this project no Claude quota) and for the Vertex open models; a model's reasoning is replayed only within its own turn; malformed tool arguments no longer break a session; 500/529 errors retry; the server context rides with the newest message so follow-ups read the conversation from cache (steady follow-ups about 40% cheaper); optional planner/guide split (`SCAVAGENT_PLANNER_MODEL`). Comparison in `docs/MODEL_COMPARISON.md`: with story design v2, Claude Sonnet 5.5 and Opus 5.5 pass 26/26 and write the best stories. Jan's decision (September 29): Claude Sonnet 5.5 alone (Opus too expensive; Sonnet planning with a Flash-Lite guide saved $0.10–0.15 per adventure but delivered each stop noticeably worse). Live since September 29 (evening): PR #14 merged (`cf0d55e`), and revision `scavagent-00016-z6s` runs `SCAVAGENT_MODEL=anthropic/claude-sonnet-5-5` with `ANTHROPIC_API_KEY` from Secret Manager (`anthropic-api-key:latest`, version 2; version 1 disabled). `scripts/acceptance_checks.py` against the deployed URL: 26/26, longest turn 54 s. To go back to Gemini, set `SCAVAGENT_MODEL=vertex_ai/gemini-3.5-flash-lite` on the service. Automatic fallback (PR, `jan/claude-fallback`): when Claude reports no credit, rejects the key, cannot be reached, or stays overloaded, Gemini 3.5 Flash-Lite answers that turn and the next 5 minutes; checked live with an invalid key. Open: story redesign follow-ups (Kyle), camera review and deploy (above), physical-feature challenges (parked until the current flow works well).

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

Latest changes/checks (September 29, afternoon: story design v2, branch `kyle/story-design-v2`, pull request open for review, not merged):

- Implemented `docs/STORY_DESIGN.md` following its plan:
  - the optional `schemas.py` fields (approved by Jan);
  - the draft fields and the `evaluate_adventure_plan` schema;
  - the new evaluator codes (plus `CLUE_OUT_OF_ORDER`), with `REAL_PERSON_IN_FICTION` and `UNSOURCED_LINK` extended;
  - the prompt changes;
  - story checks in the acceptance script.
- `get_adventure_state` also returns the cast, the clues still to tell in chat, and the finale. This is a small additive edit to Jan's `state.py`, logged in `docs/SHARED_FILE_EDITS.md` for Jan to accept or drop.
- The design's implementation notes record the choices the spec left open.
- `find_places`: Wikipedia's search requires every word of a query. Near 96th & 2nd, "music rock historic landmark" matched 2 unrelated articles where "music" alone matched 24, and landmark records ignored the query. Each key term is now searched separately, and each candidate lists its `matched_terms`. Live near the Bowery, 'Strokes rock "music venue"' now leads with Mercury Lounge, Bowery Ballroom, Arlene's Grocery, and CBGB.
- Tests: `uv run --frozen pytest -q` passes 310 with the network blocked (272 before). Both regression drafts fail with the listed codes, and the worked example passes.
- Acceptance, local (Gemini 3.5 Flash-Lite, in-memory store): 26 checks, 8 of them new story checks. There were six runs, each followed by a fix:

  | Run | Result | What happened, and the fix |
  |---|---|---|
  | 1 | 25/26, story 8/8 | Q3 got an empty model reply. OBJECT_UNEARNED had looped five times, so the rule now follows the clue chain. |
  | 2 | 25/26, story 8/8 | Q1 planned one stop inside a budget the model chose, so only a limit the user gives now excuses fewer stops. Q3 did not re-time. |
  | 3 | 16/22 | Q1 hit a model timeout. Q2 took 9 drafts: an architect stated as fact was rejected (now allowed), and a museum 275 m away stood in for the required corner (the messages now name the fix). The model finally moved the corner itself, which the script caught. |
  | 4 | 25/26, story 8/8 | Q3 did not re-time. The puzzles had no `solution`, so the OBJECT_UNEARNED message now names them. |
  | 5 | 24/26, story 8/8 | Q2 bounced between DEADLINE_EXCEEDED and TOO_FEW_STOPS, then hit the 16-round limit after saving, so the user got no briefing. The messages now say where another stop fits. Q3 finished before the destination. |
  | 6 | 24/26, story 8/8 | Q1 and Q2 passed on the second evaluation. Q3 completed the required corner the user had not reached, then finished early. |

- Deployed (still `main`, before this change): 20/26. The 6 story checks that need a v2 story fail, as expected, and Q3 waived a required stop on the user's behalf.
- For Jan:
  1. Review the pull request. The `schemas.py` and `state.py` edits are logged in `docs/SHARED_FILE_EDITS.md`.
  2. Harness: after `MAX_TOOL_ROUNDS`, ask the model once more without tools for its reply. In run 5 a plan was saved, but the reply was only "Sorry, I hit my tool-call limit".
  3. State: track arrival at the destination so `finish_adventure` refuses while the user is still on the way. Runs 5 and 6 finished early at query 3.
  4. Model: rerun `scripts/acceptance_checks.py` once Claude is configured. Flash-Lite's query-3 guiding varies from run to run.

Latest changes/checks (September 29, night: cameras, story depth, guiding, speed; branch `kyle/cameras-and-depth`, pull request open for review, not merged). Jan's list for the night, item by item. The three follow-ups from the block above are closed by Jan's #14 and #15.

- **Camera stops in planning (1):**
  - The camera session's edits to Kyle's files in #17 are sound.
  - A requested camera stop is now enforced. When the user's words ask for a camera (or `"camera_stop"` is in `user_stated`) and the plan has none, `evaluate_adventure_plan` runs `find_camera_checkpoints` along the route itself. A verified position whose detour and photo fit the free time becomes `CAMERA_STOP_MISSING`, naming the id to add; anything else becomes a note for the reply.
  - `get_next_directions` to a camera stop carries the spot's instructions and verification status. The planning turn has scrolled away by then, and `get_adventure_state` shows only the id.
  - Grader query 2 with a real camera: see `scripts/camera_checks.py` below.
- **Deeper stories (2):**
  - `TOO_FEW_STOPS` asks for 4 stops from 100 minutes.
  - A new check, `CLUE_BARE_NUMBER`, fails clues that are only a number.
  - The prompt asks for clues that carry the plot (an alias, an alibi, a place, a time) and a turn by the middle stop. Its example no longer names a handler: models kept reusing "Mara Quill".
- **Guiding (3):**
  - `scripts/guiding_checks.py` has 18 checks over arrival, a hint, a wrong answer, the right answer, a skip, and "only 15 minutes left".
  - The prompt: a wrong answer gets the next hint and never the answer, giving up means the fallback and a skip, and an answer always belongs to the current stop.
- **Planning speed (4):**
  - A round made only of lookup tools now runs at once (Jan's `app.py`, logged in `docs/SHARED_FILE_EDITS.md`).
  - The Overpass mirror is asked when the main server hasn't answered after 4 s.
  - `OBJECT_UNEARNED` quotes the text to change.
  - Measured on Sonnet before these changes: planning turns took 37–95 s, of which the model took 28–54 s. Tools took the rest: an intersection took 5–33 s on Overpass, and three research calls in one round took 16 s one after another. The evaluator itself takes under half a second.
- **Walk test (5):** a plan to follow is in [WALK_TEST.md](WALK_TEST.md).
- **Grading docs (6):** the evaluator section of [TOOLS.md](TOOLS.md) is rewritten for graders, and [CONTRIBUTIONS.md](CONTRIBUTIONS.md) says who built what (Jan: please check your half).
- **Results on Sonnet 5.5** (local server with Kyle's key; $2.55 of the night's $6, over two batches):
  - Acceptance: 28/28.
  - Guiding: 18/18. A hint and a wrong guess gave nudges, not the answer. "Roxy" completed only that stop, the skip was a skip with its clue told, and "15 minutes" was re-timed twice with a clear answer.
  - Camera: 11/11. The camera stop was at Amsterdam @ 72 St, the capture was real, "can't see myself" was recorded with a retake offered and the image-only verification explained, and the photo was in the finale.
  - Depth: Kyle's two-hour Strokes query got 4 stops (Irving Plaza, Luna Lounge, Mercury Lounge, Bowery Ballroom), with subway legs. Its clues were an alias, a password, the buyer, and a street. Jan's 25-minute query got 2 stops with an alias and a hiding place. A camera run's clues were "Quoin", "Pitti", and nine o'clock: names and a time, not digit sums.
  - Planning time in the second batch, after the speed changes: 43–72 s per planning turn (it was 45–95 s), with tools taking 8–23 s of it (it was 16–60 s). The model's drafting (35–59 s, with one redraft in most plans) is now most of the time. The second batch also passed 28/28, with the same depth.
- **Flash-Lite, for comparison:** guiding went from 10/18 to 17/18 with the prompt changes. Before them, it completed a stop on "banana" and applied the next answer to a stop the user had not reached. It still could not plan query 2 with its camera: it hit the tool limit on `OBJECT_UNEARNED` before that message quoted the text.
- **For Jan:**
  1. Review the pull request. Besides a README link, the only change to your files is the concurrent lookup round in `app.py`.
  2. The walk test.

## Joint release work

- [x] Calibrate at least three usable camera viewpoints: 147 `image_verified` standing spots on Manhattan cameras (#18, Jan-approved); none `field_verified` yet. Live capture verified on the deployed site (September 29).
- [ ] Choose and walk-test a pilot area (a walk through query 2 would also give a first in-frame souvenir and upgrade its camera spot to `field_verified`).
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
