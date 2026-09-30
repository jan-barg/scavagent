# Who built what

Scavagent was built by Jan (`jgb2170`) and Kyle (`kc3936`). Each directed coding agents: Kyle worked with Claude Code, and Jan with Claude and Codex. Each owns one original tool, reviewed the other's pull requests before they merged, and can explain the other's design. The planned split is in [WORK_SPLIT.md](WORK_SPLIT.md), and edits either person made to the other's files are logged in [SHARED_FILE_EDITS.md](SHARED_FILE_EDITS.md).

## Kyle: planning, research, and the plan evaluator

- **Original tool: `evaluate_adventure_plan`** (`adventure/`).
  - It builds the agent's draft into a routed plan, using only places and facts the research tools returned. It then checks the plan against the user's request and the product's rules before the user sees it: time, required stops, travel modes, physical honesty, sourced links, the story design, and revisions mid-adventure.
  - A plan that fails can't be saved. [TOOLS.md](TOOLS.md) lists every check.
- **The agent's instructions** (`agent.py`): how it plans, guides each stop, handles skips and changed limits, and ends. Kyle implemented story design v2, which Jan and Kyle agreed together.
- **Research and travel tools** (`integrations/`):
  - `geocode_place`: OpenStreetMap and NYC GeoSearch.
  - `find_places` and `research_place`: claims pinned to Wikipedia revisions and NYC Landmarks Preservation Commission records.
  - `get_route` and `get_walking_times`: walking routes from Valhalla and OSRM, and subway or bus legs from Google Routes, including the wait for the train.
  - `get_transit_arrivals`: live MTA GTFS-realtime.
  - `find_filming_records`: NYC Open Data, with its coverage dates.
- **Planning tools:** `save_adventure_plan` and `get_next_directions`.
- **Checks and documentation:** the grader-query acceptance script (`scripts/acceptance_checks.py`), the mid-adventure guiding checks (`scripts/guiding_checks.py`), the story regression tests, and [TOOLS.md](TOOLS.md).

## Jan: the app, progress, cameras, and deployment

- **Original tool: `find_camera_checkpoints` and `capture_camera_checkpoint`** (`integrations/cameras.py`).
  - These find NYC DOT traffic-camera views at pedestrian standing positions, never the camera's mounting point.
  - The positions come from a calibration workbench, where Jan approved 151 image-verified spots on 102 cameras.
  - The capture tool saves the live still as a souvenir, at most once per message, and shows it again in the finale.
- **App and harness** (`app.py`): the `/chat` loop and trace, retries, the final answer after the last tool round, and model selection (Claude Sonnet 5.5 through Anthropic, with a Gemini fallback and prompt caching). Also the model comparison ([MODEL_COMPARISON.md](MODEL_COMPARISON.md)).
- **Progress and storage** (`state.py`, `tools.py`): durable sessions (SQLite locally, Firestore and Cloud Storage deployed), the progress operations and their guards (plan order, arrival at the destination), photos, and history.
- **Frontend** (`index.html`): the mobile chat, optional location, and recovery after a reload or a slow reply.
- **Deployment:** Cloud Run with continuous deployment from GitHub, secrets, the README, and `submission.json`.
- **The shared contract** (`schemas.py`), which both halves build on, plus the story design write-up and its regression fixtures.

## Together

- The product plan and the shared contract.
- Story design v2, agreed after two disappointing live runs.
- The choice of image-verified camera positions.
- Testing on the deployed site, and the walk-test.
