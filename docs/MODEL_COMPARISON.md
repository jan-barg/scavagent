# Model comparison

Which model should run Scavagent? Measured by Claude (Jan's agent) on September 29, 2026, project `agentic-ai-msds`. Rerun after Kyle's story-design work (docs/STORY_DESIGN.md) merges, because the prompt and evaluator change what "good" looks like.

## Access on Vertex AI (September 29, 2026)

Every model runs through `SCAVAGENT_MODEL` (LiteLLM string) and `VERTEX_LOCATION`. The Cloud Run service account's `roles/aiplatform.user` covers all of them.

| Model | `SCAVAGENT_MODEL` | `VERTEX_LOCATION` | Status |
|---|---|---|---|
| Claude Opus 5.5 | `vertex_ai/claude-opus-5-5` | `global` | Enabled in Model Garden (Anthropic access form approved); quota 0, so every call returns 429 until the quota request is granted |
| Claude Sonnet 5.5 | `vertex_ai/claude-sonnet-5-5` | `global` | Same as Opus 5.5 |
| Claude Opus 5 / Sonnet 5 | `vertex_ai/claude-opus-5`, `vertex_ai/claude-sonnet-5` | `global` | Enabled; quota 0 |
| Gemini 3.5 Flash-Lite | `vertex_ai/gemini-3.5-flash-lite` | `global` | Works (current default) |
| Gemini 3.5 Flash | `vertex_ai/gemini-3.5-flash` | `global` | Works; occasional 429 (shared capacity) |
| Kimi K2 Thinking | `vertex_ai/moonshotai/kimi-k2-thinking-maas` | `global` | Works |
| Qwen3 235B Instruct | `vertex_ai/qwen/qwen3-235b-a22b-instruct-2507-maas` | `global` | Works |
| gpt-oss-120b | `vertex_ai/openai/gpt-oss-120b-maas` | `us-central1` | Works, but most calls are refused with 429 "too many concurrent requests", even one at a time |

Claude quota: IAM & Admin → Quotas, "Online prediction requests per base model per minute", region `global`, base models `anthropic-claude-opus` and `anthropic-claude-sonnet`.

## Method

Each model ran the same local app (`app.py` on this branch, in-memory store, live tools, Routes API billed to `agentic-ai-msds`), one server per model:

- `scripts/acceptance_checks.py`: the README's three grader queries (18 checks, 6 turns).
- Jan: "I'm at 72 and west end. I have 25 minutes. Im looking for something related to music history", then "Ready, let's go."
- Kyle: "I am on 96th and 2nd ave, and need to get to FiDi in 2 hours. I am a big fan of the band The Strokes. Help plan a trip to get there", then "Ready, let's go."

Per turn: wall time, model calls, tokens, and LiteLLM's cost estimate (list prices; cached input discounted). Story quality is judged by reading the saved plan and replies against the seven principles in docs/STORY_DESIGN.md. One run per model, so single turns vary; treat small differences as noise.

## Round 1: current `main` prompt and evaluator (before story design v2)

| Model | Acceptance | Planning turn (s) | Follow-up turn (s, median / max) | Cost per planning query | Cost, all 10 turns |
|---|---|---|---|---|---|
| Gemini 3.5 Flash-Lite | 18/18 | 23–47 | 5 / 13 | $0.015 | $0.13 |
| Gemini 3.5 Flash | 15/18 (all three from one 429 on Q1) | 52–104 | 21 / 69 | $0.13–0.25 | $1.25 |
| Kimi K2 Thinking | 17/18 | 66–97 | 9 / 24 | $0.03–0.06 | $0.40 |
| Qwen3 235B | 14/18 | 86–122 (two hit the 16-round limit) | 7 / 10 | $0.02 | $0.14 |

No turn exceeded the 240-second limit.

Story quality (principle numbers from STORY_DESIGN.md):

- **Flash-Lite:** reliable with the tools, weak on story. Openings are one or two lines with no role or handler (1). Stops are "describe one detail" observations that earn nothing (3). Kyle's two-hour trip got one stop at the Chrysler Building with no sourced link to The Strokes (4, 7), and a cast member named "Agent Julian" (6).
- **Flash:** the best stories so far. It found the real Strokes venues (Mercury Lounge, Arlene's Grocery) and ended on a Strokes song title. Jan's got Verdi Square and the Ansonia with a puzzle built on a sourced fact. Still thin openings, observation stops that earn no clue, and a "Julian (Archivist)". Slower, about 10× Flash-Lite's cost, and one query failed on a 429.
- **Kimi K2 Thinking:** the most story: three stops each, puzzles whose answers become clues, a finale built from them. But it casts real people as speaking characters: all five Strokes on the radio for Kyle, "Roxy" Rothafel and Steve Ostrow for Jan (6). It also invents history ("private after-hours concerts" at the Beacon in 1972). The current evaluator only catches architects in the cast, so these plans passed.
- **Qwen3:** not usable. It asked the user to restate "72 and west end" instead of geocoding it, ran out of tool rounds twice, and invented Strokes history ("demo tapes were stored here in a safe deposit box").
- **gpt-oss-120b:** not usable on this project: Vertex refuses most calls for concurrency, so turns crawl.

## Claude

Not measured yet: waiting for quota. The app is ready: `vertex_ai/claude-opus-5-5` sends adaptive thinking at effort `medium` (override with `SCAVAGENT_REASONING_EFFORT`), a 32K output cap, and automatic prompt caching; tool choice stays `auto`. Thinking blocks are replayed only within their own turn, as Claude 5.5's preserved-thinking rules require when the system message changes every turn. The request shape is checked against LiteLLM's real Vertex transformation in `tests/test_models.py`.

## Recommendation

Pending the Claude runs.
