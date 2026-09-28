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
| J1 | Shared schemas, fixture examples, tool-result convention | Done on branch `jan/j1-shared-contracts`: `schemas.py`, `fixtures/`, tools/harness on the result convention, trace kept when a later model call fails; 17 pytest checks pass; one live local `/chat` call (Vertex Gemini + Open-Meteo) returned the new trace shape | Kyle review of `docs/CONTRACTS.md` |
| J2 | App/session persistence and compatible tool traces | Done on branch `jan/j2-session-persistence` (stacked on J1): `state.py` (SQLite locally, Firestore + Cloud Storage via `SCAVAGENT_STORE=firestore`), optimistic save versions, `client_message_id` replay, client location, `/history`, `/media/{asset_id}`, session-bound `get_adventure_state`/`update_adventure_state`, dev-only `load_dev_adventure`. 34 pytest checks pass; a live Gemini run loaded the dev hunt, recorded a check-in, and resumed after a store restart. `FirestoreStore` round-trip (save/reload, stale-save rejection, photo bytes) passed locally against the real Firestore database and bucket on September 28 | — |
| J3 | Camera adapter, saved images, calibrated catalogue, original finder | Not started | Camera API spike; J1 for final integration; joint field checks |
| J4 | Chat presentation, foreground location and resume | Not started | J1; J2 for integration |
| J5 | Cloud Run setup and GitHub continuous deployment | In progress. Created September 28 in `agentic-ai-msds`: Run/Cloud Build/Artifact Registry/Firestore APIs enabled; Firestore `(default)` Native in `us-east1`; private bucket `gs://agentic-ai-msds-scavagent-photos` (`us-east1`); service account `scavagent-run@agentic-ai-msds.iam.gserviceaccount.com`. Not done: role grants for that service account, first deploy, GitHub continuous deployment. The image has not been built yet | Jan runs the service-account role grants; Jan's browser approval for the GitHub connection |
| J6 | Final photo recap, recovery checks, deployment documentation | Not started | J2–J5 |

Latest changes/checks: J1 landed on `jan/j1-shared-contracts` (see row). On September 28, local gcloud CLI and Application Default Credentials were reauthenticated with `agentic-ai-msds` as the configured project; a local `/chat` call then reached Vertex Gemini successfully. The project is under the Columbia organization with billing enabled and `aiplatform.googleapis.com` on; Cloud Run, Cloud Build, Artifact Registry, and Firestore APIs are not yet enabled, and nothing is deployed. Next action: J2 persistence (Claude worktree) and the camera adapter/frontend (Codex worktree, branch `jan/camera-frontend`).

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
