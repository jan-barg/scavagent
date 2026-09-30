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

## Round 2: story design v2 (#13) and the caching change (September 29, evening)

Same queries against `main` with Kyle's story design v2 merged. The acceptance script now has 26 checks (8 new story checks: briefing, characters introduced before they act, every stop's clue used later, theme links). "Before" is `087780b`; "after" adds the caching change `f8cbf3f`. No turn in either run hit the tool-round limit.

| Model | Acceptance (before → after) | Planning turn (s, after) | Follow-up cost (before → after) | 10 turns (before → after) |
|---|---|---|---|---|
| Claude Opus 5.5 | 26/26 → 26/26 | 70–144 | $0.174 → $0.149 | $2.04 → $1.84 |
| Claude Sonnet 5.5 | 26/26 → 26/26 | 32–64 | $0.055 → $0.072 | $0.82 → $0.84 |
| Gemini 3.5 Flash-Lite | 26/26 → 24/26 | 43–62 | $0.012 → $0.013 | $0.16 → $0.18 |

These runs send turns seconds apart, which flatters the old code: its server context changes only once a minute, so back-to-back turns already hit the cache. (Sonnet's "after" also spent 8 calls on the skip-a-stop turn against 2 before.) Flash-Lite's two misses were the briefing and cast checks on one query; the reply had a briefing, the saved draft did not. It passed them before, so this is probably run-to-run variance.

The caching change measured with realistic spacing: Sonnet, Kyle's query, then four follow-ups 75 seconds apart.

| Follow-up | Before: written / cost | After: written / cost |
|---|---|---|
| "Ready, let's go." | 15.8K / $0.064 | 18.7K / $0.096 |
| "I'm here now." | 17.7K / $0.054 | 5.7K / $0.034 |
| "Not sure. Can I get a hint?" | 17.9K / $0.048 | 3.5K / $0.031 |
| "Okay, I think I've got it. What's next?" | 18.0K / $0.048 | 3.6K / $0.028 |

Before, every follow-up re-wrote the whole history (read only the instructions); after, it reads the history and writes only the previous turn. The first follow-up after planning costs more, because the planning turn is written once at the one-hour rate. Steady follow-ups are about 40% cheaper, and the gap grows with the session, because the old cost rose with the history. Follow-up turns take about as long as before (medians within a second or two).

Stories: all three models now write briefings, casts with a handler and a channel, clues, and theme links. Examples with the full chat and saved plans: https://claude.ai/artifact/6HzgH4w8MPtw76aANkpCvf (private to Jan). Findings:

- Opus writes the richest stories (three stops on both new prompts, named handlers with distinct voices, clues from sourced dates and numbers). Sonnet is close, sometimes with two stops. Flash-Lite follows the format but thinly.
- agent.py's worked example (a freelance tape tracker, Mara Quill, a stolen reel, a locker code, Roxy) is Jan's music query. Opus and Sonnet both reused Mara Quill there, and on a new jazz prompt Opus opened with "You're a freelance tape tracker" and Sonnet built an archivist, a radio, and a locker code. The example is shaping every music story; a different example scenario would fix that.
- With v2, Opus's longest turn was 144 s, inside the 240-second limit.

## Split: Sonnet 5.5 plans, Flash-Lite guides (September 29, night)

`SCAVAGENT_PLANNER_MODEL=anthropic/claude-sonnet-5-5` with `SCAVAGENT_MODEL=vertex_ai/gemini-3.5-flash-lite` (commit `e5e7024`; the setting was removed in #29 after the decision). Sonnet takes turns with no adventure under way; Flash-Lite takes the rest; a Flash-Lite reply that tries to evaluate or save a plan is dropped and Sonnet redoes the turn.

| Run | Sonnet alone | Split |
|---|---|---|
| Acceptance + Jan/Kyle queries (10 turns) | $0.84, 26/26 | $0.67, 26/26 |
| Jazz walkthrough, 1 min between turns (plan, 6 guide turns, a replan, a last message) | $0.32 | $0.37 |

- Guide turns on Flash-Lite cost $0.002–0.014 against $0.017–0.107 on Sonnet, and are as fast or faster.
- A replan costs more in the split ($0.19 against $0.05–0.10): Sonnet has none of the Flash-Lite turns cached, so it writes the whole conversation at the one-hour rate. In the walkthrough the replan also finished the adventure, and the last message then went to Sonnet ($0.07), because finished adventures route to the planner.
- Quality: Flash-Lite delivers the saved beats (the handler's radio, the question, "hold on to that") but plainer, it gave stop 1's question before the user arrived, and it built its own Wikipedia links with fake anchors (the evaluator cannot see chat replies). After the handover Sonnet noticed those links, but it also wrongly retracted one of its own sourced links, whose research had left the context window.
- Fixes before using the split: route finished adventures to the guide (a new plan escalates anyway); write the planner's cache at the five-minute rate on an escalation; drop links in replies that are not in the session's sources. Estimated effect: a walkthrough with one replan about $0.23 against $0.32, one without about $0.13 against $0.27.

## Claude notes

- Claude runs through Anthropic's API (`anthropic/claude-...`), not Vertex (no quota for this project). The app sends adaptive thinking at effort `medium` (override with `SCAVAGENT_REASONING_EFFORT`), a 32K output cap, automatic prompt caching, and the instructions as a separately cached block (one hour); tool choice stays `auto`. Thinking blocks are replayed only within their own turn, as Claude 5.5's preserved-thinking rules require when the server context changes every turn.
- Neither model wrote a link that did not come from a tool result, cast a real person, or claimed a Strokes connection the research did not support.
- Caching (since `f8cbf3f`): the system message is only the instructions, the per-turn server context rides with the newest message and is not stored, the context window moves in steps of 20 messages, and Claude gets one-hour breakpoints at the end of the history before the last two user messages. Round 2 has the numbers.

## Decision

Claude Sonnet 5.5 alone (Jan, September 29). Opus 5.5 is too expensive, and the Sonnet-plans/Flash-Lite-guides split saved $0.10–0.15 per adventure but delivered each stop noticeably worse (it gave away answers, jumped ahead, invented links). Live on Cloud Run since revision `scavagent-00016-z6s` (`SCAVAGENT_MODEL=anthropic/claude-sonnet-5-5`, key from Secret Manager); acceptance checks against the deployed URL: 26/26. The code default is still Gemini 3.5 Flash-Lite, so local runs work without an Anthropic key. After this decision, the split's code (`SCAVAGENT_PLANNER_MODEL`) was removed in #29. The sections above record what was measured.

## Recommendation (before the decision)

Claude, Sonnet 5.5 or Opus 5.5; Jan to choose from the story samples.

- Only the two Claude models passed every acceptance check in both rounds and kept to the story rules the evaluator cannot fully check (no real people as characters, no invented history, links only from sources, honest when a theme has no sourced place).
- Under story design v2 both fit the 240-second turn limit (longest planning turns: Opus 144 s, Sonnet 64 s).
- Opus writes the richer stories for about twice the cost: per planning turn about $0.24 against $0.10, per follow-up about $0.15 against $0.07 (list prices, after the caching change). A full adventure (one plan, about eight follow-ups) comes to roughly $0.80–1.40 on Opus and $0.40–0.65 on Sonnet: the low end with minutes between turns (the walkthrough above), the high end with replanning and turns in quick succession.
- Flash-Lite stays about ten times cheaper, and its stories are thinner.
- Next (since done and rejected; see "Split" and "Decision" above): the planner/guide split (a Claude model plans and replans, a cheaper model guides the walk), once Jan has picked the combinations to try. It needs a full walkthrough per combination (arrival, answers at each stop, the finale, a replan), because the guide delivers the story.
