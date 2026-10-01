# Who built what

Lou was built by Jan Barganowski (`jgb2170`) and Kyle Coletta (`kc3936`), each working with coding agents: Kyle with Claude Code, Jan with Claude Code and Codex. Each reviewed the other's pull requests.

## Jan

- Camera souvenirs (original tool). Sends the user to a sidewalk spot an NYC traffic camera can see and saves the photo, using 151 standing spots Jan calibrated and approved.
- Chat server. Runs the agent loop and keeps the tool trace, with retries, a 4-minute turn limit, and a Gemini fallback when Claude can't answer.
- Sessions and progress. Conversations, progress, and photos survive reloads and failed turns (SQLite locally, Firestore and Cloud Storage when deployed).
- The page. The mobile chat with Lou, the current-stop sign, the case board, and a live log of each tool as it runs.
- Model choice. Compared seven models on the same queries and picked Claude Sonnet 5.5.
- Deployment. Cloud Run with continuous deployment from GitHub.

## Kyle

- Plan evaluator (original tool). Builds the agent's draft into a routed plan and checks it against the user's request, the sources, and the story rules before anyone sees it.
- Agent instructions. How Lou plans, guides each stop, handles changes of plan, and ends an adventure.
- Research. Finds places and gathers sourced facts from Wikipedia and NYC Landmarks Preservation Commission records.
- Routes and transit. Walking, subway, and bus routes, live train arrivals, and directions to each stop.
- Check scripts. Replay the grader queries, a guided walk, and a camera stop against a running server.
