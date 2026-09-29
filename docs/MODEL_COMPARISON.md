# Model comparison

Which model should run Scavagent? Measured by Claude (Jan's agent) on September 29, 2026, project `agentic-ai-msds`. Rerun after Kyle's story-design work (docs/STORY_DESIGN.md) merges, because the prompt and evaluator change what "good" looks like.

## Access on Vertex AI (September 29, 2026)

Every model runs through `SCAVAGENT_MODEL` (LiteLLM string) and `VERTEX_LOCATION`. The Cloud Run service account's `roles/aiplatform.user` covers the Vertex models; Claude runs through Anthropic's API with a key (docs/DEPLOY.md, "Claude through Anthropic's API").

| Model | `SCAVAGENT_MODEL` | `VERTEX_LOCATION` | Status |
|---|---|---|---|
| Claude Opus 5.5 | `anthropic/claude-opus-5-5` (Anthropic's API, `ANTHROPIC_API_KEY`) | ignored | Works |
| Claude Sonnet 5.5 | `anthropic/claude-sonnet-5-5` | ignored | Works |
| Claude on Vertex | `vertex_ai/claude-opus-5-5`, `vertex_ai/claude-sonnet-5-5` (also `-5` versions) | `global` only (404 in every region tried) | Enabled in Model Garden, but quota 0 and none will be granted for this project's billing setup: every call returns 429 |
| Gemini 3.5 Flash-Lite | `vertex_ai/gemini-3.5-flash-lite` | `global` | Works (current default) |
| Gemini 3.5 Flash | `vertex_ai/gemini-3.5-flash` | `global` | Works; occasional 429 (shared capacity) |
| Kimi K2 Thinking | `vertex_ai/moonshotai/kimi-k2-thinking-maas` | `global` | Works |
| Qwen3 235B Instruct | `vertex_ai/qwen/qwen3-235b-a22b-instruct-2507-maas` | `global` | Works |
| gpt-oss-120b | `vertex_ai/openai/gpt-oss-120b-maas` | `us-central1` | Works, but most calls are refused with 429 "too many concurrent requests", even one at a time |

## Method

Each model ran the same local app (`app.py` on this branch, in-memory store, live tools, Routes API billed to `agentic-ai-msds`), one server per model:

- `scripts/acceptance_checks.py`: the README's three grader queries (18 checks, 6 turns).
- Jan: "I'm at 72 and west end. I have 25 minutes. Im looking for something related to music history", then "Ready, let's go."
- Kyle: "I am on 96th and 2nd ave, and need to get to FiDi in 2 hours. I am a big fan of the band The Strokes. Help plan a trip to get there", then "Ready, let's go."

Per turn: wall time, model calls, tokens, and LiteLLM's cost estimate (list prices; cached input discounted), each model call counted once in the turn it belongs to. Story quality is judged by reading the saved plan and replies against the seven principles in docs/STORY_DESIGN.md. One run per model, so single turns vary; treat small differences as noise.

## Round 1: current `main` prompt and evaluator (before story design v2)

| Model | Acceptance | Planning turn (s) | Follow-up turn (s, median / max) | Cost per planning query | Cost, all 10 turns |
|---|---|---|---|---|---|
| Claude Opus 5.5 (effort medium) | 18/18 | 76–124 | 15 / 25 | $0.20–0.28 | $1.88 |
| Claude Sonnet 5.5 (effort medium) | 18/18 | 39–77 | 7 / 18 | $0.09–0.13 | $0.84 |
| Gemini 3.5 Flash-Lite | 18/18 | 23–47 | 5 / 13 | $0.015 | $0.11 |
| Gemini 3.5 Flash | 15/18 (all three from one 429 on Q1) | 52–104 | 21 / 69 | $0.13–0.25 | $1.08 |
| Kimi K2 Thinking | 17/18 | 66–97 | 9 / 24 | $0.03–0.06 | $0.34 |
| Qwen3 235B | 14/18 | 86–122 (two hit the 16-round limit) | 7 / 10 | $0.02 | $0.12 |

No turn exceeded the 240-second limit (gpt-oss, below, reached it once). The Claude runs used commit `70e494d` (same prompt and evaluator; the harness difference is Claude-only prompt caching of the instructions).

Story quality (principle numbers from STORY_DESIGN.md):

- **Claude Opus 5.5:** the best stories, and the only run close to the v2 design without its prompt. Openings are briefings: an operation name, the handler and how they reach you, named suspects, the stakes, then stops, time, and "Ready?" (1, 2). Stops earn clues that the finale uses: alibis that clear suspects, a dead-drop combination derived from the museum's landmark date, Jan's finale keyed to the Beacon's 2,894 seats (3, 5). Every cast member is invented; real people appear only as sourced history (6). For Kyle it found Mercury Lounge, Ludlow Street, and Bowery Ballroom, then said plainly that its sources don't mention The Strokes, so it makes no claims about the band (4). Jan's 25 minutes got three stops (7). On query 3 it gave honest options when 15 minutes could not cover the required corner. Weaknesses: follow-ups cost $0.06–0.23 each, and planning turns take up to about two minutes.
- **Claude Sonnet 5.5:** nearly as good and about twice as fast and cheap. Handler briefings, invented casts, honest "no Strokes connection confirmed" for Kyle, and a neat clue chain for Jan (a catalog code that spells 1954, the year "Rock Around the Clock" was recorded at the Pythian Temple). Thinner than Opus: Kyle's two hours got two stops (7), and some clues are arithmetic on a number rather than something the story needs.
- **Flash-Lite:** reliable with the tools, weak on story. Openings are one or two lines with no role or handler (1). Stops are "describe one detail" observations that earn nothing (3). Kyle's two-hour trip got one stop at the Chrysler Building with no sourced link to The Strokes (4, 7), and a cast member named "Agent Julian" (6).
- **Flash:** the best stories of the non-Claude models. It found the real Strokes venues (Mercury Lounge, Arlene's Grocery) and ended on a Strokes song title. Jan's got Verdi Square and the Ansonia with a puzzle built on a sourced fact. Still thin openings, observation stops that earn no clue, and a "Julian (Archivist)". Slower, about 10× Flash-Lite's cost, and one query failed on a 429.
- **Kimi K2 Thinking:** the most story: three stops each, puzzles whose answers become clues, a finale built from them. But it casts real people as speaking characters: all five Strokes on the radio for Kyle, "Roxy" Rothafel and Steve Ostrow for Jan (6). It also invents history ("private after-hours concerts" at the Beacon in 1972). The current evaluator only catches architects in the cast, so these plans passed.
- **Qwen3:** not usable. It asked the user to restate "72 and west end" instead of geocoding it, ran out of tool rounds twice, and invented Strokes history ("demo tapes were stored here in a safe deposit box").
- **gpt-oss-120b:** not usable on this project. Vertex refused 62 of 81 calls with 429 "too many concurrent requests" (one request at a time), so turns took 2–4 minutes, one reached the 240-second limit, and acceptance was 6/14.

## Claude notes

- Claude runs through Anthropic's API (`anthropic/claude-...`), not Vertex (no quota for this project). The app sends adaptive thinking at effort `medium` (override with `SCAVAGENT_REASONING_EFFORT`), a 32K output cap, automatic prompt caching, and the instructions as a separately cached block (one hour); tool choice stays `auto`. Thinking blocks are replayed only within their own turn, as Claude 5.5's preserved-thinking rules require when the server context changes every turn.
- Neither model wrote a link that did not come from a tool result, cast a real person, or claimed a Strokes connection the research did not support.
- Where the money goes: each turn's first call writes the conversation so far to the cache again (about 20K tokens, roughly $0.10 on Opus, $0.05 on Sonnet), because the per-turn server context sits before the history. Moving that context after the history (into the new user message) would let later turns read the history from cache and cut follow-up turns by roughly half. Not done yet: it changes where the model sees the server context, so it needs its own before/after run.

## Recommendation

**Claude Sonnet 5.5 now** (`SCAVAGENT_MODEL=anthropic/claude-sonnet-5-5`), and decide between it and Opus 5.5 after Kyle's story-design PR merges.

- Sonnet 5.5 and Opus 5.5 are the only models that passed all 18 acceptance checks and also followed the story rules the current evaluator cannot see (no real people as characters, no invented history, links only from sources, honest when a theme has no sourced place).
- Opus 5.5 writes the richer mysteries, but it costs about 2.2× as much and takes about 1.6× as long per planning turn (up to 124 s). The v2 design adds briefing, cast, clue, and theme-link checks, so planning turns will need more evaluator rounds; Sonnet has more room under the 240-second turn limit.
- Against the current Flash-Lite default: Flash-Lite is about 8× cheaper than Sonnet and faster, but its stories are the thin ones STORY_DESIGN.md was written about.
- Rerun both Claude models with Kyle's merged prompt and evaluator. If Opus's planning turns stay well under 240 s, switch to Opus 5.5 for the submission; the cost difference is a few dollars for grading.
