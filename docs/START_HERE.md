# Start here — Scavagent

This handoff contains the decisions needed to continue implementation without the original chat. Jan and Kyle are building a chat-only NYC adventure agent for October 7, 2026. The starter is imported; adventure features are not yet built. Spotify is an optional final addition.

## Read in this order

| File | Purpose |
|---|---|
| [STATUS.md](STATUS.md) | What exists, what was checked, and what still needs work |
| [PLAN.md](PLAN.md) | Product, grounded stories, geography, cameras, state, tools, and compliance |
| [WORK_SPLIT.md](WORK_SPLIT.md) | Ownership, six development phases, and completion criteria |
| [CONTRACTS.md](CONTRACTS.md) | Proposed shared data and integration boundaries to settle first |
| [Jan's brief](briefs/JAN.md) | Starting task for Jan's Codex or Claude session |
| [Kyle's brief](briefs/KYLE.md) | Starting task for Kyle's coding agents |
| [ASSIGNMENT.md](ASSIGNMENT.md) | Supplied class brief, preserved as reference |
| [STARTER_README.md](STARTER_README.md) | Original starter instructions |

The humans have accepted the overall product direction. The suggested ownership and module boundaries are working defaults, and the shared schema proposal still needs to become code. The latest explicit human instruction takes precedence. Keep these documents aligned when a decision changes.

## Open this repository in your coding tool

Repository: https://github.com/jan-barg/scavagent

Jan's existing local clone is at `Desktop/COLUMBIA CLASSES/SEM3/Agents/PROJECT_1`. Kyle should use his own clone. Open the repository root as the project/working folder in Codex or Claude Code. This makes the repository's agent instructions and source files available together. You do not need to import or replay the full conversation.

Use the prompt in the appropriate brief. Other coding tools that do not automatically load `AGENTS.md` should be explicitly told to read it. In Codex, add/open this folder as a local project before starting its implementation chat; these files do not themselves move or create an app conversation.

## First coordination step

Jan owns the initial shared-contract implementation. Kyle can immediately verify research/routing integrations and prepare fixture results matching [CONTRACTS.md](CONTRACTS.md). Merge a small schema-and-fixture change early, then both build against it. This avoids two competing definitions of an adventure.

For multiple agents on Jan's side, a useful split is one agent on session/app/state integration and another on frontend/camera work. Assign exact files for each phase and use isolated checkouts. Whoever owns the shared registry incorporates tool definitions once their interfaces are ready.

## Known uncertainties to carry forward

- A final model/provider, map/search provider, pilot area, and cloud resource names have not been chosen. Keep defaults configurable; do not invent working credentials or deployed resources.
- The camera image endpoint was verified, but exact participant standing positions still require field calibration. An online camera is not necessarily pointing at its reference sidewalk.
- A September 28 API check found the film-permit data too old to establish present-day filming. See the measured result in [PLAN.md](PLAN.md).
- Current deployment status and each teammate's work must be verified from code and [STATUS.md](STATUS.md), not inferred from the delivery schedule.
- Both Columbia UNIs/emails and the final deployment URL are needed before generating a complete `submission.json`.

Tool-specific project instruction behavior is documented in [OpenAI's AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) and [Claude Code's project memory guide](https://code.claude.com/docs/en/memory).
