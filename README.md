# Scavagent

A chat-based NYC adventure agent being built by Jan and Kyle for a class project due October 7, 2026. Users provide a starting location and optionally a destination, time budget, required stops, transport preferences, and theme. The planned agent researches real places, creates a grounded fictional adventure, adapts as plans change, and captures traffic-camera souvenirs at verified pedestrian viewpoints.

**Current status:** the supplied web tool-calling starter is imported. Its existing demonstration is a weather tool. Adventure features and deployment are not yet implemented or verified. This repository now includes the implementation handoff for the team and its coding agents.

## Start development

Read [the handoff entrypoint](docs/START_HERE.md). It links the [product plan](docs/PLAN.md), [work split and phases](docs/WORK_SPLIT.md), [proposed shared contracts](docs/CONTRACTS.md), and [implementation status](docs/STATUS.md).

- Jan's Codex/Claude sessions: [starting brief](docs/briefs/JAN.md).
- Kyle's agents: [starting brief](docs/briefs/KYLE.md).
- Shared coding-agent instructions: [AGENTS.md](AGENTS.md); Claude entrypoint: [CLAUDE.md](CLAUDE.md).
- Course requirements: [supplied assignment](docs/ASSIGNMENT.md).

Parallel coding sessions should use separate checkouts/worktrees and clearly assigned tasks. Shared schemas are the first integration milestone. Spotify soundtracks come only after the core experience is implemented and tested.

## Run the existing starter

Use Python 3.10 or later and `uv`. The current starter uses Google application-default credentials and a configured Google Cloud project with access to its selected model. Final model choice is deferred.

```sh
uv sync
gcloud auth application-default login
uv run app.py
```

Open http://localhost:8000. Try: “Is it nice enough to go for a walk in New York?” The existing weather integration uses Open-Meteo. Confirm your Google Cloud project/billing/API configuration if the model call fails. These commands describe the imported starter; no successful authenticated run is asserted by the documentation handoff.

The [original starter README](docs/STARTER_README.md) is preserved for provenance.

## Planned grader examples

These are implementation acceptance targets, not currently supported adventure behavior. Finalize the starting locations after selecting the field-tested pilot area.

1. “I'm at Central Park West and West 86th Street. Give me a 1960s spy adventure.”
2. “I'm at Central Park West and West 86th Street. I have 45 minutes, need to finish at West 72nd Street and Broadway, and must pass West 81st Street and Columbus Avenue. Walking only, architecture theme, and include a camera stop if one fits.”
3. Follow-up to an active adventure: “Skip the next optional stop. I have only 15 minutes left, and I still need to reach my destination.”

## Submission status

The final release must preserve the required `/chat` response fields and tool traces, demonstrate at least three tools including external data and two original tools, and run on Cloud Run with GitHub continuous deployment. `submission.json` will need the actual deployment URL and both team members' Columbia UNIs/emails. Those values have not been supplied; do not substitute fictional values. See [the plan's compliance checklist](docs/PLAN.md#10-assignment-compliance).
