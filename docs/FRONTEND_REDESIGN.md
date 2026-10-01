# Frontend redesign: Street Blade, Lou, and the live log

Status: implemented on `jan/frontend-redesign` (September 30, 2026), including the `agent.py` voice edit that Kyle's Claude greenlit with one wording change, checked with the three required Sonnet runs (see [Implementation notes](#implementation-notes)). The main agent's seven review changes are adopted ([Review changes](#review-changes)). Written by Claude, in the frontend session.

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
| Required before merge, after Kyle's greenlight: one Sonnet run on a local server each of `scripts/acceptance_checks.py`, `scripts/guiding_checks.py`, and `scripts/guiding_checks.py --scenario replan`. About $3–5. | **Needs Jan's go** (it costs money) |

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
| Abandoned | "Ended early", the story's solution, "N of M stops done" |

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
  - `i` is the call's position in this turn's `tool_calls`, so the log and the final trace have the same order. `lookups_at_once` sees only its own round, so `run_agent` passes it the round's offset (`len(tool_calls)` when the round starts), and the round's steps are numbered `offset + j`.
  - `lookups_at_once` marks all jobs in a round as started with the same `round` number. Each future's done-callback marks its own step finished, which is how concurrent lookups resolve one by one.
- **The reporter.** `chat()` creates a `ProgressReporter` only when there is a `client_message_id`. It is bound to the session, the message, and the claim token.
  - **Thread-safe.** Three threads touch it: the request thread (the hooks), the lookup pool (done-callbacks), and its own writer. One lock guards the steps. Hooks change them only under the lock, and the writer takes a snapshot under the lock and writes outside it.
  - **Never fatal.** Every hook body is wrapped, so an exception inside a hook is logged and swallowed and the turn continues. A failed write is logged the same way.
  - **Stopped before release, on both paths.** The writer thread is stopped (and joined, with a short timeout) in a `finally` that runs before `store.release`. That covers the normal release and the error path at `app.py:391` (`except Exception: store.release(...); raise`), so no write can land after the claim is gone.
  - The writer flushes at most once a second.
- **`GET /progress?session_id&client_message_id`** returns:
  ```json
  {"session_id": "...", "client_message_id": "...", "state": "running", "started_at": "...", "phase": "tools",
   "steps": [{"i": 0, "round": 1, "name": "geocode_place", "subject": "72nd and West End", "status": "ok"}]}
  ```
  - **Read order, so a poll is cheap.** The endpoint reads the claim first: one small document. A *live* claim answers `running` with its steps, and the session is never loaded. "Live" uses the same rule as `claim()` (`state._claim_is_live`: started less than `IN_FLIGHT_TIMEOUT` ago). Only when there is no live claim does it load the session record (up to 800 KB, `state.MAX_RECORD_BYTES`) to tell the other two states apart:
    - `done`: `record.replies` has the id. The page can fetch the reply at once instead of waiting out the 5 s retry.
    - `unknown`: no reply either. The turn hasn't started, or its claim expired because it crashed. An expired claim is never reported as `running`, even though it stays in storage until it is replaced.
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
  - The top-level `status` is `idle`, `proposed`, `active`, `completed`, or `abandoned`.
  - A stop's `status` is `done`, `skipped`, `blocked`, `current`, or `locked`. For an abandoned adventure, the stops that were never reached stay `locked`.
  - Names and addresses are sent only for stops that are resolved or current. While the plan is proposed, the first stop is also named, because the briefing reply names it.
  - `clues` come only from revealed beats. `solution` is sent only when the status is `completed` or `abandoned`: the adventure is over, so nothing is left to spoil. The sign shows "Ended early" for an abandoned adventure (see the sign table above).
  - It never sends answer rules, hints, activity prompts or solutions, unrevealed beats, or any cast member but the handler.
  - An unknown session gets 404, and a session with no plan gets `{"status": "idle"}`.
- **`/static`** is a `StaticFiles` mount for the fonts. `/` still serves `index.html`.
- **Licenses ship with the assets.**
  - `static/fonts/` holds the two font files plus `OFL-Overpass.txt` and `OFL-AtkinsonHyperlegible.txt`, the SIL Open Font License texts with each family's copyright line.
  - `static/licenses/phosphor-MIT.txt` holds Phosphor's MIT notice. `index.html` carries a one-line comment beside the inline icon sprite that points to it.
  - The "Sources and attribution" section of `docs/TOOLS.md` credits Overpass (OFL), Atkinson Hyperlegible (OFL, Braille Institute), and Phosphor Icons (MIT).

### `state.py` (Jan's)

- **New `Store` methods:** `set_progress(session_id, message_id, token, progress) -> bool` (writes only while that token holds the claim) and `progress(session_id, message_id) -> dict | None`.
- **Where each store keeps it:**
  - `MemoryStore`: next to the claim.
  - `SqliteStore`: a new `progress TEXT` column on `claims`, added with `ALTER TABLE` when missing, so existing `.data` databases keep working.
  - `FirestoreStore`: a `progress` field on the existing `…_claims` document. The write is a `transaction.update(doc, {"progress": ...})` inside a transaction that first reads the document and checks the token, never a `set`. A `set` would replace the whole claim, including `started_at`, and break its expiry.
- **`progress()` returns the progress only for a live claim,** by the same `_claim_is_live` rule as `claim()`, and `None` otherwise.
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
   - You work the desk and are never a character in the story. You handle what is real: directions, times, hints, \
   photos, how the game works, and sourced facts, except the stop's theme-link fact, which the character carries into the \
   scene in the story's voice, with its source link. Each adventure's invented handler and cast speak only inside \
   scenes, and you patch them through ("Patching your handler through."). Never give a character your name.
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
  - Ordering, including a concurrent round finishing out of order, and a lookup round after earlier tool calls in the same turn (step numbers offset, matching `tool_calls`).
  - **Load count:** polling a running turn never loads the session (a counting store wrapper asserts zero `load` calls). With no live claim, one load per poll decides `done` or `unknown`.
  - **Expired claim:** a claim older than `IN_FLIGHT_TIMEOUT` reads as `unknown` (or `done` if the reply is stored), never `running`.
  - **Thread safety:** hooks and done-callbacks fired from many threads while the writer snapshots never lose or corrupt a step.
  - **A failing hook doesn't fail the turn:** a reporter whose hooks raise still returns the normal `/chat` reply.
  - **The writer stops before release on both paths:** no progress write after release, either after a normal turn or when `run_turn` raises (the `app.py:391` path).
  - A write from a stale token is refused, for all three stores (memory, SQLite, and the Firestore fake in `tests/test_state.py`).
  - **Firestore uses an update, not a set:** after a progress write, the claim's `started_at` and `token` are unchanged and expiry still works. The fake records `update` against `set`.
  - The progress survives a new store instance ("another server") and is gone after release; `done` after the reply is stored.
  - `unknown` before a claim.
  - `/chat` unchanged: the response keys and `tool_calls` shape, both with and without `client_message_id`, and a 409 still a 409.
  - `/adventure` spoiler safety: no solution while proposed or active, no hints or answer rules, locked names.
  - `/adventure` for an **abandoned** adventure: `status: "abandoned"`, the solution present, unreached stops locked. Also for a completed one: the solution present.
  - SQLite migration of an old `claims` table.
- **Node:** the harness's fake DOM grows what the new UI needs. New tests cover:
  - the block renderer (briefing, character panel, lists, case file), still DOM-safe;
  - the poll starting with a pending turn and stopping on the reply;
  - a reload mid-turn polling the same ids;
  - backoff on 404, and no overlapping sends;
  - `done` shortening the wait;
  - new replies scrolling to their top;
  - the board rendering only what the server sent;
  - the sign for each status, including "Ended early" for an abandoned adventure.
- **Both suites stay green:** 432 pytest and 36 Node today. The PR shows that the new tests fail when each feature is removed.
- **Screenshots, with no model calls.** Phone and desktop screenshots come from the in-app browser against a local server with `SCAVAGENT_DEV_FIXTURES=1` and a scripted fake model (the script stays in the session scratchpad, not the repo).
- **Required Sonnet runs before merge,** after Kyle greenlights the `agent.py` edit and Jan gives the go (about $3–5 in total). The voice edit reformats every reply graders read, so each of these runs once against a local server with `SCAVAGENT_MODEL=anthropic/claude-sonnet-5-5`:
  - `scripts/acceptance_checks.py`;
  - `scripts/guiding_checks.py`;
  - `scripts/guiding_checks.py --scenario replan`.

  The PR reports the results and the transcripts' formatting: the briefing blockquote, character lines, numbered directions, and the case file.

## Docs in the same PR

- `docs/CONTRACTS.md`: the two endpoints and progress on claims.
- `README.md`: how to use the page (Lou, the sign, the case board, the live log).
- `docs/STATUS.md`: Jan's section.
- `docs/SHARED_FILE_EDITS.md`: the `agent.py` edit.
- `docs/TOOLS.md`: font and icon credits in "Sources and attribution".
- This file: kept as the design record.

## Risks

| Risk | Mitigation |
|---|---|
| The formatting depends on the model | The renderer falls back to paragraphs, and the sign and board come from state. The required Sonnet runs check the main model. |
| A prompt change just before the walk test | It is small and additive, and it doesn't merge until the three required Sonnet runs pass (about $3–5). |
| Firestore writes during a turn | A background thread under one lock, at most one write a second, token-checked `update` (never `set`). Failures are logged and never fatal, and the writer is stopped before release on both paths. |
| Polling cost | A running turn's poll reads only the small claim document. The session record is loaded only when no live claim exists. |
| Spoilers through `/progress` or `/adventure` | Whitelists, plus tests asserting that solutions, hints, and answer rules never appear |
| `index.html` grows from about 360 lines to about 900 | Still one file with one script, so the harness keeps working |
| Access | Both endpoints use the session id as the capability, like `/history` |

## Effort

About one working day in total:

| Work | Time |
|---|---|
| Server and pytest, including the review's concurrency and storage tests | 4–5 h |
| `index.html` and Node tests | 5–6 h |
| Prompt, docs, and licenses | about 1 h |
| Required Sonnet runs (after Kyle's greenlight and Jan's go) | about 1 h, $3–5 |
| Screenshots and PR | about 1 h |

With a greenlight tonight, the PR can be ready for review on the evening of October 1. The prompt edit merges only once Kyle has greenlit it and the Sonnet runs pass. If either comes late, the rest of the PR can merge first and the prompt edit can follow as its own small commit.

## Review changes

The main agent's review asked for seven changes. All are adopted:

| # | Requested | Where |
|---|---|---|
| 1 | `/progress` reads the claim first and loads the session only without a live claim; a test counts loads | Server, `/progress` read order; Tests, load count |
| 2 | An expired claim reads as `unknown`, by `claim()`'s liveness rule | Server, `/progress` and `progress()`; Tests, expired claim |
| 3 | A thread-safe reporter: one lock and a snapshot for the writer, wrapped hooks, the writer stopped in a `finally` before both release paths, step numbers offset for lookup rounds | Server, the reporter; Tests, thread safety, failing hook, stop before release, ordering |
| 4 | Firestore writes progress with a token-checked `update` in a transaction, never a `set` | `state.py`; Tests, update not set |
| 5 | `/adventure` handles `abandoned` ("Ended early"), with the solution for completed or abandoned | The sign table; `/adventure`; Tests (pytest and Node) |
| 6 | The Sonnet runs are required after Kyle's greenlight: acceptance, guiding, and replan, about $3–5 with Jan's go | Decisions; Tests; Risks; Effort |
| 7 | OFL files with the fonts, Phosphor's MIT notice, and credits in `docs/TOOLS.md` | Server, licenses; Docs |

## Implementation notes

Where the build differs from, or adds to, the plan above:

- **Steps carry `parallel`** as well as `round`, so the page groups only lookups that really ran at once ("Reading up on 3 places", "2 lookups at once").
- **Tool rows** read as a label and an outcome, with the subject below: "Place research done / Beacon Theatre", "Route check failed", "Route check passed", "Weather check unavailable", and "ran" when a verdict was trimmed from history. The middle dots are gone. "+N more" beyond 12 is unchanged.
- **The sign has one more state:** every stop resolved but the destination not yet reached: "Every stop done", "Finish: <destination>", "Tell me when you arrive".
- **The status line** is quiet when there is nothing to say. While a reply is pending it reads "Replies can take a minute or two. It keeps going if you lock your phone." These strings are kept word for word: "Still working on your plan…", "The reply didn’t arrive. Press Send to retry the same message.", "That message wasn’t accepted. Edit it and send again.", "Picking up your trail…", "Your trail is here. Pick up wherever you left off.", and "History couldn’t load. You can still send a message to reconnect."
- **A reloaded page** opens the last reply at its top. It does that again once the web fonts load, because they reflow the history.
- **One-paragraph replies** render inline, as before. Block layout (panels, lists, the case file) starts only when a reply has more than one block. Any reply without the new formatting still renders as plain paragraphs.
- **The `agent.py` voice edit** is in (`0ad1c66`, logged in `docs/SHARED_FILE_EDITS.md`). Kyle's Claude greenlit it with one change, adopted above: the stop's theme-link fact stays with the character in the arrival scene, as Guiding and `STORY_DESIGN.md` have it. The three required Sonnet runs passed on September 30 against a local server: acceptance 28/28, guiding 18/18, and replan 8/8. All 19 turns were answered by Sonnet 5.5, with no fallback. The replies used the briefing blockquote, characters' lines such as `> **Odalys Finch (radio):**`, and numbered directions, and the handler carried the sourced arrival fact with its link. The real sessions rendered as designed in the in-app browser.
- **`/adventure` after Kyle's review:**
  - The handler is the cast member introduced in the briefing with a non-empty contact (the `HANDLER_MISSING` test), preferring a role containing "handler". A pre-v2 cast of plain names has none.
  - The finale beat's clue waits until the adventure is over.
  - Theme links and beats' `uses` are never sent.

  Tests cover two briefed contacts, a pre-v2 plan, and the added spoilers.
- **Checks.** With the network blocked:
  - `uv run --frozen pytest -q` passes 461, 29 of them new;
  - `node --test tests/frontend.test.cjs` passes 49, 13 of them new.

  A mutation check planted 22 single removals of the new behavior (12 on the server, 10 in the page), and the tests caught all 22. The thread-safety test is a stress test, so it isn't part of that count.
- **Screenshots** are in `docs/screenshots/redesign/`. They came from a local server with a seeded in-memory store and a scripted stand-in for the model and slow tools, so no model calls were made. Each state was checked in the in-app browser. The PNG files were captured with headless Chrome over the DevTools protocol against the same server, because the in-app browser can't save files.

## Greenlight checklist

- [ ] The design and the scope above.
- [ ] Contract additions: `GET /progress`, `GET /adventure`, and progress on the claim record.
- [ ] The `agent.py` edit (Kyle's greenlight).
- [ ] Jan's go for the three required Sonnet runs (about $3–5).
- [ ] Anything to change before work starts.
