# Jan workstream — starting brief

Use this in Codex, Claude Code, or another coding agent opened at the repository root. The full previous conversation is unnecessary.

## Paste this starting prompt

```text
You are working on Jan's side of Scavagent, the NYC adventure-agent class project due October 7, 2026.

Read AGENTS.md, docs/START_HERE.md, docs/STATUS.md, docs/WORK_SPLIT.md, and docs/CONTRACTS.md. Consult docs/PLAN.md for feature details. Inspect the current checkout and recent changes before assuming the handoff status is still current.

Start with Jan's Phase 1 tasks. First turn the proposed shared records and tool-result contract into small validated schemas and clearly labeled fixtures. Preserve the starter's /chat response fields and align the contract with the existing harness. Keep that first change small enough for Kyle to adopt immediately.

Then build Jan-owned app/session persistence, chat/media support, and the initial camera integration toward the Phase 1 completion criteria. Verify deployment prerequisites and prepare the required Cloud Run/GitHub deployment configuration. Use actual environment availability; do not invent credentials, resources, a deployment URL, or successful deployment. Continue useful local work if an external prerequisite is missing.

Kyle owns research/routing, planner/story behavior, the adventure evaluator, MTA, and filming. Do not independently implement or replace his whole workstream. Use labeled contract-compatible fixtures at integration boundaries until real adapters are ready. Coordinate shared interface changes through the repository.

Keep all interaction in chat plus Send. Model choice stays deferred; Jev is excluded. Spotify comes last, after core testing. Do not fabricate field-verified camera positions or user photographs.

Use an isolated feature checkout if another coding session is active. Implement the next unclaimed Jan milestone, verify the behavior you change, and update only the relevant status entries with actual results, remaining dependencies, and the next task. Report the files changed and checks performed. Do not mark the whole project finished after a scaffold works.
```

## If Jan uses Codex and Claude simultaneously

Assign two separate scopes and checkouts. A useful initial split is:

- **App/session task:** shared schema implementation, `app.py`, state persistence, central tool registration, deployment configuration.
- **Camera/frontend task:** camera adapter spike and catalogue format, then chat/media rendering using the merged schema. Prototype the adapter against the proposed contract while it is pending; do not redefine shared models.

Give the camera/frontend task a narrower prompt than the full brief above. Name its files and explicitly identify the app/session work already owned by the other task. Use the host tool's supported worktree/checkout workflow. Both agents changing different branches in one working directory still conflict.

## First completion criteria

Shared schemas and example data are usable by Kyle; a small development adventure persists through reload; the deployed request format remains compatible; a current camera image can be displayed and saved without claiming an unverified person is in it. Deployment and physical calibration are reported separately from local/fixture checks.
