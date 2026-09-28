# Kyle workstream — starting brief

Use this with a coding agent in Kyle's own clone of https://github.com/jan-barg/scavagent. The full previous conversation is unnecessary.

## Paste this starting prompt

```text
You are working on Kyle's side of Scavagent, the NYC adventure-agent class project due October 7, 2026.

Read AGENTS.md, docs/START_HERE.md, docs/STATUS.md, docs/WORK_SPLIT.md, and docs/CONTRACTS.md. Read the relevant parts of docs/PLAN.md and inspect the current checkout before assuming anything is implemented.

Start with Kyle's Phase 1 tasks: verify research/geocoding/routing integration options, produce normalized sample results using the proposed shared contract, and implement the basic agent progression against a clearly labeled development hunt. Starting location is the only mandatory user input. Do not require a time budget, destination, or theme to begin.

Jan owns the shared schemas, app/session boundary, persistence, frontend/location, cameras, central tool registration, dependencies, and deployment. If the shared schemas have not landed yet, work independently on integration adapters and fixture results rather than creating a competing schema. Describe required interface/dependency changes for Jan to integrate. Keep the starter's required /chat response and tool-call trace format intact.

After the contract is shared, build dynamic candidate discovery, grounded place research, route/dwell feasibility, stop selection, the original evaluate_adventure_plan tool, and story/challenge generation. Physical tasks need suitable evidence or user observations and a fallback. Fiction must stay distinct from historical fact. The evaluator should calculate and report concrete violations rather than ask another model for an unsupported approval.

Add skips, deadline changes, inaccessible places, and coherent remaining-route revisions while preserving completed stops, photos, and revealed clues. MTA arrivals and filming evidence follow the phased plan; inspect filming coverage dates before claiming a set exists today. Keep model/provider configurable, leave Jev out, and reserve Spotify for the final optional phase.

Use an isolated feature checkout. Implement the next unclaimed Kyle milestone, verify meaningful behavior, and update your own section of docs/STATUS.md with actual results, dependencies, and the next task. Report changed files and checks. Do not claim the whole project is complete when only fixtures work.
```

## If Kyle uses multiple agents

Split research/routing adapters from story/planner/evaluator work in separate checkouts. Give one integration owner responsibility for Kyle's `agent.py` and planning code. All tasks use Jan's shared schemas; do not let each agent redesign state, tool registration, or the frontend.

## First completion criteria

Real research and routing service responses can be normalized into the agreed shape, or their specific access blocker is documented. The basic agent can advance through a labeled example plan and produce the agreed proposed state changes. These are integration proofs; the live product must switch to dynamic tool-backed planning by Phase 2.
