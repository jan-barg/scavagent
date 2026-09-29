# Scavagent

A chat agent that turns a walk through New York City into a short mystery adventure. Tell it where you are, and optionally how much time you have, where you need to end up, a stop you must pass, how you want to travel, and a theme. It then:

- researches real places near your route (Wikipedia and NYC Landmarks Preservation Commission records);
- times the route on foot and by subway;
- writes a story whose clues you earn at each stop;
- guides you stop by stop in chat.

At verified viewpoints it can save an NYC DOT traffic-camera still of you as a souvenir. Built by Jan and Kyle for a Columbia agents course (October 2026).

**Try it:** https://scavagent-b57mvtutma-ue.a.run.app. The site deploys from `main` through Cloud Run continuous deployment.

## How to use it

- **Start:** type where you are. Everything else is optional. Scavagent answers with a briefing (your role, your handler, the mission), the stops, the time it takes, and the first stop.
- **Play:** type "ready" to get directions, and tell it when you arrive. It gives you a task at each stop, a puzzle or something to look at, and your answer earns a clue for the case. You can ask for a "hint", "skip" a stop, or change plans ("I only have 15 minutes left").
- **Chat only:** everything happens in the chat box. Sharing your browser location is optional and only helps with directions.
- **Tool calls:** each reply lists the tools it used. The `/chat` API returns `response`, `session_id`, and `tool_calls` (each with `name`, `args`, and `result`).

## Sample queries for graders

1. "I'm at Central Park West and West 86th Street. Give me a 1960s spy adventure."
2. "I'm at Central Park West and West 86th Street. I have 45 minutes, need to finish at West 72nd Street and Broadway, and must pass West 81st Street and Columbus Avenue. Walking only, architecture theme, and include a camera stop if one fits."
3. A follow-up once an adventure is under way (for example after query 2, "ready", and reaching the first stop): "Skip the next optional stop. I have only 15 minutes left, and I still need to reach my destination."

Planning a researched adventure takes up to a minute or two while the agent searches, routes, and checks its plan. The page keeps waiting on its own. Camera stops appear only at standing spots a person has approved: 147 in Manhattan (Upper East/West Side and Midtown), each matched on the live camera image and map imagery rather than tested in person, which Scavagent tells the user while offering a retake. In query 2 the camera stop is the viewpoint at Broadway and West 72nd Street, next to the finish; where no spot fits, Scavagent says so. `scripts/acceptance_checks.py <URL>` replays all three queries against a running app and checks what must hold.

## Tools

Full descriptions, sources, and configuration are in [docs/TOOLS.md](docs/TOOLS.md).

| Tool | What it does |
|---|---|
| `evaluate_adventure_plan` | **Kyle's original tool.** Builds the agent's draft into a routed plan and checks it before the user sees it: timing against the user's limits, required stops and their order, travel modes, sourced facts and links, and the story rules (briefing, introduced characters, clues that add up, theme links, no real people as characters). A plan that fails can't be saved. |
| `find_camera_checkpoints`, `capture_camera_checkpoint` | **Jan's original tool.** Finds NYC DOT traffic cameras at pedestrian standing positions a person verified on the camera image and map, or in the field (never the camera's mounting point). When the user says they're in position, it saves the live still as a souvenir, at most once per message, and shows it again in the finale. |
| `geocode_place`, `find_places`, `research_place` | Resolve typed places, find candidate stops, and gather sourced facts (Wikipedia revisions, NYC LPC records). |
| `get_route`, `get_walking_times`, `get_next_directions` | Walking routes (OpenStreetMap Valhalla) and subway/bus legs (Google Routes), directions to the next stop. |
| `get_transit_arrivals` | Live subway arrivals and service alerts (MTA GTFS-realtime). |
| `find_filming_records` | NYC film-permit history for a street, with its coverage dates (NYC Open Data). |
| `save_adventure_plan`, `get_adventure_state`, `update_adventure_state` | Save a passing plan and keep progress server-side: stops completed or skipped, clues revealed, photos, arrival. |
| `get_weather` | Current weather (Open-Meteo), from the course starter. |

## Run locally

Use Python 3.10 or later and `uv`. Model calls go through Vertex AI with Google Application Default Credentials and the project `agentic-ai-msds` (see [docs/DEPLOY.md](docs/DEPLOY.md)). The model is set by `SCAVAGENT_MODEL`: Gemini 3.5 Flash-Lite today, with a Claude and open-model comparison in progress.

```sh
uv sync
gcloud auth application-default login
uv run app.py
```

Open http://localhost:8000. Sessions persist in `.data/scavagent.db`, so a conversation and its progress survive a restart. Other settings are in [.env.example](.env.example). For a synthetic test adventure, start with `SCAVAGENT_DEV_FIXTURES=1 uv run app.py` and ask for "the constrained_route test adventure"; its places and clues are invented and labeled as such.

Checks: `uv run pytest` (runs without network access) and `node --test tests/frontend.test.cjs`.

## Project docs

- Status and who owns what: [docs/STATUS.md](docs/STATUS.md), [docs/WORK_SPLIT.md](docs/WORK_SPLIT.md).
- Design: [docs/PLAN.md](docs/PLAN.md), [docs/STORY_DESIGN.md](docs/STORY_DESIGN.md), [docs/CONTRACTS.md](docs/CONTRACTS.md).
- Deployment: [docs/DEPLOY.md](docs/DEPLOY.md). Course requirements: [docs/ASSIGNMENT.md](docs/ASSIGNMENT.md).
- Coding-agent instructions: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md). The [original starter README](docs/STARTER_README.md) is kept for provenance.
