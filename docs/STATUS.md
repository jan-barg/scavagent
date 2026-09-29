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
| J1 | Shared schemas, fixture examples, tool-result convention | Not started | Review proposed contracts |
| J2 | App/session persistence and compatible tool traces | Not started | J1 |
| J3 | Camera adapter, saved images, calibrated catalogue, original finder | Not started | Camera API spike; J1 for final integration; joint field checks |
| J4 | Chat presentation, foreground location and resume | Not started | J1; J2 for integration |
| J5 | Cloud Run setup and GitHub continuous deployment | Not started | Cloud project/configuration access; can start alongside J1 |
| J6 | Final photo recap, recovery checks, deployment documentation | Not started | J2–J5 |

Latest changes/checks: no application work yet. Next action: implement J1 and verify J5 prerequisites; a separate isolated task can inspect the camera integration.

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
