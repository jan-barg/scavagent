# Scavagent

A chat-based NYC adventure agent being built by Jan and Kyle for a class project due October 7, 2026. Users provide a starting location and optionally a destination, time budget, required stops, transport preferences, and theme. The planned agent researches real places, creates a grounded fictional adventure, adapts as plans change, and captures traffic-camera souvenirs at verified pedestrian viewpoints.

**Current status:** shared schemas, durable sessions, and adventure state tools are implemented; adventure planning, cameras, the new frontend, and deployment are in progress. See [docs/STATUS.md](docs/STATUS.md). This repository now includes the implementation handoff for the team and its coding agents.

## Start development

Read [the handoff entrypoint](docs/START_HERE.md). It links the [product plan](docs/PLAN.md), [work split and phases](docs/WORK_SPLIT.md), [proposed shared contracts](docs/CONTRACTS.md), and [implementation status](docs/STATUS.md).

- Jan's Codex/Claude sessions: [starting brief](docs/briefs/JAN.md).
- Kyle's agents: [starting brief](docs/briefs/KYLE.md).
- Shared coding-agent instructions: [AGENTS.md](AGENTS.md); Claude entrypoint: [CLAUDE.md](CLAUDE.md).
- Course requirements: [supplied assignment](docs/ASSIGNMENT.md).

Parallel coding sessions should use separate checkouts/worktrees and clearly assigned tasks. Shared schemas are the first integration milestone. Spotify soundtracks come only after the core experience is implemented and tested.

## Run locally

Use Python 3.10 or later and `uv`. The model call uses Google Application Default Credentials and the project `agentic-ai-msds` (see [DEPLOY.md](docs/DEPLOY.md)). Final model choice is deferred; set `SCAVAGENT_MODEL` to change it.

```sh
uv sync
gcloud auth application-default login
uv run app.py
```

Open http://localhost:8000. Sessions persist in `.data/scavagent.db`, so a conversation and its adventure progress survive a server restart. Other settings are listed in [.env.example](.env.example).

To try progression before the planner exists, start with `SCAVAGENT_DEV_FIXTURES=1 uv run app.py` and ask for “the constrained_route test adventure”. It loads a labeled synthetic hunt; its places and clues are invented.

Run the checks with `uv run pytest`. Deployment steps are in [docs/DEPLOY.md](docs/DEPLOY.md).

The [original starter README](docs/STARTER_README.md) is preserved for provenance.

## Grader examples

Type a starting place; everything else is optional. Try:

1. “I'm at Central Park West and West 86th Street. Give me a 1960s spy adventure.”
2. “I'm at Central Park West and West 86th Street. I have 45 minutes, need to finish at West 72nd Street and Broadway, and must pass West 81st Street and Columbus Avenue. Walking only, architecture theme, and include a camera stop if one fits.”
3. Follow-up to an active adventure: “Skip the next optional stop. I have only 15 minutes left, and I still need to reach my destination.”

On Kyle's branch these run end to end against live data locally (September 29, 2026). They are not yet deployed. No camera position is field-verified yet, so example 2 says no camera stop fits. `scripts/acceptance_checks.py` replays all three against a running app and checks what must hold. The tools, including the two original ones, are documented in [docs/TOOLS.md](docs/TOOLS.md).

## Submission status

The final release must preserve the required `/chat` response fields and tool traces, demonstrate at least three tools including external data and two original tools, and run on Cloud Run with GitHub continuous deployment. `submission.json` will need the actual deployment URL and both team members' Columbia UNIs/emails. Those values have not been supplied; do not substitute fictional values. See [the plan's compliance checklist](docs/PLAN.md#10-assignment-compliance).
