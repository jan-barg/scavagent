# Frontend redesign: Street Blade, Lou, and the live log

Status: waiting for greenlight (September 30, 2026). Written by Claude, in the frontend session on `jan/frontend-redesign`, for Jan's main agent to review before implementation. Nothing is implemented yet.

- Clickable mockup (the approved design): https://claude.ai/artifact/CNi2h9BNEpM9Ux9eTcbcq4
- Phase 1 proposal (the alternatives, and why polling rather than streaming): https://claude.ai/artifact/EZdaiJ6mic6ju26FUa1TWi

## Decisions so far

| Decision | Status |
|---|---|
| Look: "Street Blade", second pass (the mockup above), cleaned up with Taste Skill v2 | Jan approved |
| Guide persona named **Lou**, a New York night dispatcher | Jan approved |
| Live tool-call log, by polling a new `GET /progress` | Jan approved |
| Ignore the October 2 feature freeze for this work | Jan's call. Aim to land before the walk test (October 3–4) |
| The Lou voice edit to Kyle's `agent.py` (diff below) | **Needs greenlight**, and Kyle should hear about it |
| New `GET /adventure` for the current-stop sign and the case board | **Needs greenlight** (a contract addition) |
| One Sonnet run of `scripts/guiding_checks.py` on a local server before merge, about $1–2 | **Needs Jan's go** |

## Why

1. **Jan missed the briefing in a live run.** It is the first paragraph of the longest reply, and the page scrolls to the *bottom* of a new reply, so on a phone the briefing starts off-screen.
2. **Nothing shows where you are.** The current stop exists only in the scrollback.
3. **Planning looks frozen.** `/chat` is one blocking POST, so for 40–70 s the page shows only "Following the thread…", and tool calls appear only with the final reply.
4. **Fact and fiction look the same,** and the voice is default Sonnet.
5. **The current look is generic:** cream paper, a Georgia headline, and an orange accent.

## The design

### Layout

- **Phone (the main target).**
  - A header with the wordmark. It shows "Lou is planning" only while a reply is pending.
  - Under it, **the sign**: the current stop as an NYC street-name sign (green, one inner border, mixed case). It shows "Stop 2 of 3", the stop's name and address, and a route strip (stops done, current, still to come). Tapping it opens the **case board** as a bottom sheet.
  - Then the thread and the composer.
- **Desktop (1024 px and wider).** The case board becomes a permanent right-hand column (372 px) with the sign at its top. The chat column keeps a reading width.

### The sign by adventure status

| Status | The sign shows |
|---|---|
| No adventure | Nothing |
| Proposed | "Briefing ready", "First stop: …", the stop count and minutes |
| Active | "Stop N of M", the stop name, its address |
| Completed | "Case closed", the story's solution, "All M stops done" |

### The case board (read-only)

It holds the full briefing, the route, the clues earned, and saved photos.

- **Route:** later stops show as "Stop 3, revealed when you get there", because a stop's name can give away where a clue points.
- **No actions.** It has no buttons that act, so the product rule against checkpoint buttons and quick replies holds.

### In the thread

| Element | Treatment | Where it comes from |
|---|---|---|
| Your messages | Dark bubble, right | As now |
| Lou's text | Plain text, left, no bubble or role label | Reply text outside blockquotes |
| Briefing | Green panel headed "Your briefing", with the handler and channel below it | A blockquote starting `> **Briefing:**` (prompt edit) |
| Character lines | Tinted green-grey panel with a radio icon, name, and channel | A blockquote starting `> **Name (channel):**` (prompt edit) |
| Directions | Numbered list with circle markers | "walking directions are a short numbered list" (prompt edit) |
| Clues | Bold inline; also listed on the board | Reply text; `StoryBeat.clue` of revealed beats |
| Photos | `/media/{id}` image, caption below, and the existing "tell me whether you're visible" note | Image Markdown, as now |
| Finale case file | Raised panel with the solution, photo, clues, and sourced facts | A `### Case file` heading (prompt edit) |
| Tools used | "Used N tools" toggle, then each call with a check or an x, a label, and a short subject. "+N more" beyond 12; a failed plan check shows as failed. | `tool_calls`, as now |
| Live log | While pending: each tool appears as it starts and ticks off as it finishes. Concurrent lookups are grouped ("Reading up on 3 places"). There's an elapsed timer and a skeleton for the reply to come. | `GET /progress` |
| Errors and retries | Plain status line above the composer. Existing strings are kept word for word, including "That message wasn't accepted. Edit it and send again." | As now |

- **New replies open at their top.** The page scrolls the new reply's first line into view, not its last. This alone fixes the missed briefing. Your own messages still scroll to the bottom.
- **Replies that ignore the new formatting still render.** The Gemini fallback and older history come out as plain paragraphs. The sign and the board don't depend on reply text.

### Empty state

"Tell me where you're starting." Then: "I'm Lou. I turn a walk through New York into a short mystery, built from real places and sourced history." After that come three route steps:
1. **Say where you are** (a time limit, finish, or theme is optional).
2. **Get your briefing** (research and timing take about a minute).
3. **Walk and solve** (check in, answer your handler, collect clues).

Last is one typed example as plain text, not a chip, with a note that location is optional and every reply lists its tools. This covers the grading requirement to "make clear what the agent is and how to use it".

### System

| Part | Choice |
|---|---|
| Color | One accent, sign green `#0B5A3A`. Ink `#16201C`, ground `#EEF1EF`, handler tint `#E1EAE5`, and a semantic fail red `#A3301D` (failed checks and lost replies only). No pure black or white. Dark theme follows `prefers-color-scheme`, with a matching token set. |
| Type | Overpass 800 for signs and headings (it descends from Highway Gothic) and Atkinson Hyperlegible for everything read. Both are self-hosted under `/static/fonts` (OFL, latin subset, about 120 KB), with system fonts as the fallback. No serif. |
| Shape | Surfaces 14 px, anything inside a surface 8 px, signs 6 px; route stops and Send are circles |
| Icons | Phosphor, bold weight (MIT), as an inline SVG sprite |
| Motion | Only for a change of state: a step starting or finishing, a new message, the board opening. All of it turns off under `prefers-reduced-motion`. |
| Taste Skill v2 | No em dashes on the page, at most one middle dot per line, no uppercase micro-labels, no decorative dots, and a single accent |

## Server changes

### `app.py` (Jan's)

- **Progress hooks.** `run_agent(..., progress=None)` makes these calls:
  - `progress.thinking()` before each model call;
  - `progress.started(i, name, args)` before a tool;
  - `progress.finished(i, result)` after it.
  - `i` is the call's position in this turn's `tool_calls`, so the log and the final trace have the same order.
  - `lookups_at_once` marks all jobs in a round as started with the same `round` number. Each future's done-callback marks its own step finished, which is how concurrent lookups resolve one by one.
- **The reporter.** `chat()` creates a `ProgressReporter` only when there is a `client_message_id`. It is bound to the session, the message, and the claim token.
  - It keeps the steps in memory, and a writer thread flushes them to the claim at most once a second.
  - A write failure is logged and never fails the turn.
  - The writer stops before `release`.
- **`GET /progress?session_id&client_message_id`** returns:
  ```json
  {"session_id": "...", "client_message_id": "...", "state": "running", "started_at": "...", "phase": "tools",
   "steps": [{"i": 0, "round": 1, "name": "geocode_place", "subject": "72nd and West End", "status": "ok"}]}
  ```
  - `state`: `running` while a claim is live; `done` once `record.replies` has the id (the page can fetch the reply at once instead of waiting out the 5 s retry); `unknown` when there is neither (not started yet, or an expired claim).
  - `phase`: `thinking`, `tools`, or null.
  - `status`: `running`, `ok`, or `failed`. A step is `failed` when `result.ok` is false, or when `evaluate_adventure_plan` returns `passes: false`, the same rule the page uses today.
  - Invalid ids get 400, as in `/history`.
- **`subject` comes from a whitelist,** truncated to 60 characters:

  | Tool | Subject |
  |---|---|
  | `geocode_place` | `text` |
  | `find_places` | `query` |
  | `research_place` | The place name from its result, once it finishes |
  | `get_transit_arrivals` | Line and station |
  | `find_filming_records` | `street` |
  | `get_weather` | `location` |
  | Everything else | None |

  `evaluate_adventure_plan`, `save_adventure_plan`, and the state tools never expose arguments, because they contain solutions, answer rules, and hints. The full trace stays in `/chat`.
- **`GET /adventure?session_id`** is the board and the sign, read from `AdventureState` and the active plan:
  ```json
  {"status": "active", "briefing": "...", "handler": {"name": "...", "contact": "radio"},
   "stops": [{"n": 1, "name": "Beacon Theatre", "address": "2124 Broadway", "status": "done", "camera": false},
             {"n": 2, "name": "Pythian Temple", "address": "135 W 70th St", "status": "current", "camera": false},
             {"n": 3, "name": null, "address": null, "status": "locked", "camera": false}],
   "clues": [{"text": "The thief signs as Roxy.", "stop": 1}], "photos": [{"media_url": "/media/...", "visibility": "unconfirmed"}],
   "solution": null, "estimated_minutes": 38}
  ```
  - A stop's `status` is `done`, `skipped`, `blocked`, `current`, or `locked`.
  - Names and addresses are sent only for stops that are resolved or current. While the plan is proposed, the first stop is also named, because the briefing reply names it.
  - `clues` come only from revealed beats. `solution` is sent only when the status is `completed`.
  - It never sends answer rules, hints, activity prompts or solutions, unrevealed beats, or any cast member but the handler.
  - An unknown session gets 404, and a session with no plan gets `{"status": "idle"}`.
- **`/static`** is a `StaticFiles` mount for the fonts. `/` still serves `index.html`.

### `state.py` (Jan's)

- **New `Store` methods:** `set_progress(session_id, message_id, token, progress) -> bool` (writes only while that token holds the claim) and `progress(session_id, message_id) -> dict | None`.
- **Where each store keeps it:**
  - `MemoryStore`: next to the claim.
  - `SqliteStore`: a new `progress TEXT` column on `claims`, added with `ALTER TABLE` when missing, so existing `.data` databases keep working.
  - `FirestoreStore`: a `progress` field on the existing `…_claims` document, with the token checked in a transaction.
- **`release` is unchanged.** It deletes the claim, so the progress goes with it.

### Unchanged

- The `POST /chat` request and response: `response`, `session_id`, and `tool_calls` with `name`, `args`, `result`.
- The token-owned claims, 409 for a message still running, replay by `client_message_id`, the 240 s turn deadline, concurrent lookup rounds, and the Gemini fallback.
- A plain `/chat` without `client_message_id` (how graders call it) has no claim, writes no progress, and behaves exactly as today.

## Frontend changes (`index.html`, Jan's)

- **Rebuilt CSS and markup** from the mockup: tokens for both themes, the header, sign, board (sheet, or column on desktop), thread, and composer. It stays one file with one inline `<script>`, because `tests/frontend.test.cjs` extracts the first one. The element ids the tests use (`trail`, `messages`, `message`, `send`, `status`, `composer`, `welcome`) stay.
- **The renderer grows block-level Markdown:**
  - paragraphs;
  - blockquotes, which become the briefing or a character panel by their bold label;
  - ordered and unordered lists;
  - the `### Case file` section.
  
  It remains DOM-built, with no `innerHTML` for model text. Links (including balanced parentheses), bold, code, and `/media` images work as they do now.
- **Live log.** While `busy` with a pending message, the page polls `/progress` every 1.5 s, independent of the `/chat` request and its retries.
  - Each poll redraws the log from the full step list.
  - After a 404, repeated errors, or `unknown` for 10 s, it backs off to 5 s and shows the plain "working" line.
  - It never sends a message, blocks Send, or touches the pending-message storage.
  - On `done`, it cuts the current 5 s retry wait short.
  - On the reply, the log becomes that reply's "Used N tools" line.
- **Reload mid-turn.** `restore()` already resumes the pending turn with the same ids, and the poll starts with it. The log reappears with the steps done so far, from whichever instance answers.
- **Sign and board.** The page fetches `/adventure` on load and after each reply. If it fails, the sign stays hidden and the chat works as today.
- **Existing behavior is kept:**
  - the client-created session UUID;
  - the persisted pending message with `client_message_id`;
  - retries every 5 s for up to 4.5 minutes, and the busy guard;
  - reload reconciliation through `/history`;
  - 409 handling, and the permanent-4xx message;
  - DOM-safe Markdown and links;
  - `/media` photos;
  - foreground GPS with each message;
  - "+N more" beyond 12, and failed plan checks shown as failed.

## Prompt change (`agent.py`, Kyle's)

Three edits. Every existing rule stays word for word. The edit is logged in `docs/SHARED_FILE_EDITS.md` with the original text. Nothing in `adventure/` or the tests reads the prompt text (`tests/test_models.py` only compares the prompt with itself).

1. Opening line:
   ```diff
   - You are Scavagent, a guide for playful, real-world NYC adventures run entirely in chat. The user walks or \
   + You are Lou, the dispatcher at Scavagent, a guide for playful, real-world NYC adventures run entirely in chat. The user walks or \
   ```
2. New section before `## Ground rules`:
   ```text
   ## Voice
   - You work the desk and are never a character in the story. You handle what is real: directions, times, sourced \
   facts, hints, photos, and how the game works. Each adventure's invented handler and cast speak only inside scenes, and \
   you patch them through ("Patching your handler through."). Never give a character your name.
   - Sound like a seasoned New York night dispatcher: dry, quick, and kind. Short sentences; street names, cross \
   streets, clock times, and minutes from the tools. At most one wry line per reply. No emoji, no gushing, no \
   "Great question".
   - Format for a phone: the briefing is one blockquote that starts "> **Briefing:**"; every line a character speaks \
   is a blockquote that starts with their name and channel in bold, e.g. "> **Name (radio):** ..."; walking \
   directions are a short numbered list; the finale's case file starts with the line "### Case file". Everything \
   outside a blockquote is you.
   - End with the one thing the user can type next ("Say 'here' when you're outside."). You are never on the street: \
   never claim to be somewhere or to see the user.
   ```
3. Planning step 8:
   ```diff
   - 8. Reply with the briefing first, as its own short paragraph, then the number of stops, ...
   + 8. Reply with the briefing first, as its own blockquote (see Voice), then the number of stops, ...
   ```

The honesty rules (sourced facts with their links, no invented physical props, real people never cast) are untouched. Lou handles the real side, and the handler speaks only inside scenes, which matches `docs/STORY_DESIGN.md`. The example handler's name is kept out of the voice rules, so the model doesn't reuse it.

## Tests

- **pytest, offline:**
  - `/progress` content: steps, whitelisted subjects, and no arguments from the plan check or state tools.
  - Ordering, including a concurrent round finishing out of order.
  - A write from a stale token is refused.
  - The progress survives a new store instance ("another server") and is gone after release; `done` after the reply is stored.
  - `unknown` before a claim.
  - `/chat` unchanged: the response keys and `tool_calls` shape, both with and without `client_message_id`, and a 409 still a 409.
  - `/adventure` spoiler safety: no solution before completion, no hints or answer rules, locked names.
  - SQLite migration of an old `claims` table.
- **Node:** the harness's fake DOM grows what the new UI needs. New tests cover:
  - the block renderer (briefing, character panel, lists, case file), still DOM-safe;
  - the poll starting with a pending turn and stopping on the reply;
  - a reload mid-turn polling the same ids;
  - backoff on 404, and no overlapping sends;
  - `done` shortening the wait;
  - new replies scrolling to their top;
  - the board rendering only what the server sent.
- **Both suites stay green:** 432 pytest and 36 Node today. The PR shows that the new tests fail when each feature is removed.
- **Screenshots, with no model calls.** Phone and desktop screenshots come from the in-app browser against a local server with `SCAVAGENT_DEV_FIXTURES=1` and a scripted fake model (the script stays in the session scratchpad, not the repo).

## Docs in the same PR

- `docs/CONTRACTS.md`: the two endpoints and progress on claims.
- `README.md`: how to use the page (Lou, the sign, the case board, the live log).
- `docs/STATUS.md`: Jan's section.
- `docs/SHARED_FILE_EDITS.md`: the `agent.py` edit.
- This file: kept as the design record.

## Risks

| Risk | Mitigation |
|---|---|
| The formatting depends on the model | The renderer falls back to paragraphs, and the sign and board come from state. The optional Sonnet check covers the main model. |
| A prompt change just before the walk test | It is small and additive. The Sonnet check (about $1–2) is optional, or the walk test is the first live check. |
| Firestore writes during a turn | A background thread, at most one write a second, failures logged and never fatal |
| Spoilers through `/progress` or `/adventure` | Whitelists, plus tests asserting that solutions, hints, and answer rules never appear |
| `index.html` grows from about 360 lines to about 900 | Still one file with one script, so the harness keeps working |
| Access | Both endpoints use the session id as the capability, like `/history` |

## Effort

About one working day in total:

| Work | Time |
|---|---|
| Server and pytest | 3–4 h |
| `index.html` and Node tests | 5–6 h |
| Prompt and docs | about 1 h |
| Screenshots and PR | about 1 h |

With a greenlight tonight, the PR can be ready for review on the evening of October 1. That leaves October 2 to review and merge, and Cloud Run deploys it before the walk test.

## Greenlight checklist

- [ ] The design and the scope above.
- [ ] Contract additions: `GET /progress`, `GET /adventure`, and progress on the claim record.
- [ ] The `agent.py` edit, with Kyle told.
- [ ] Optional: one Sonnet run of `scripts/guiding_checks.py` (about $1–2).
- [ ] Anything to change before work starts.
