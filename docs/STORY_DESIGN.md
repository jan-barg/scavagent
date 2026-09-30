# Story design v2

Status: agreed by Jan and Kyle on September 29, 2026; written up by Claude. Implemented in Kyle's workstream (`agent.py`, `adventure/`) on `kyle/story-design-v2`, with the additive `schemas.py` fields below approved by Jan. [Implementation notes](#implementation-notes) record the choices the spec left open.

## Why

Two live runs on the deployed site passed the current evaluator and were still disappointing. Their accepted drafts, research, and transcripts are in [fixtures/story_regressions/](../fixtures/story_regressions/).

| | Jan: music history, 25 min, 72nd & West End | Kyle: The Strokes, 96th & 2nd to FiDi in 2 h |
|---|---|---|
| Stops | 1 (11 of 25 min used) | 1 (73-minute walk: transit was down) |
| Opening | A one-line hook; no role, handler, or mission | Same |
| Characters | "Agent Vance" first speaks in the finale; "The Sound Archivist" never appears | "Julian Casablancas (Anonymous Voice on the Wire)": a real, living musician cast as a character |
| The stop | "Describe one architectural detail" (any answer accepted) | Same, at a bank building with no sourced link to The Strokes |
| Payoff | The tape "was in this building all along" | A "combination code" is simply handed over, then "used" off-screen |
| Links | Built its own Wikipedia URL; 8 identical rejected drafts | Same URL mistake in chat |

The research was good (Jan's had the exact date and four song titles of Buddy Holly's last session at the Pythian Temple). The story used none of it.

## Principles

1. **Immersive opening.** The first reply is a briefing: who the user is, who they work with and how that person reaches them, what is at stake, and how a stop works.
2. **Characters are introduced before they act,** and every stop says which characters appear in it. Every cast member appears; nobody appears from nowhere.
3. **Every stop earns a clue, and the clues add up.** Each stop gives the user a concrete clue (a number, a word, a name, a direction) that later steps use. The finale is solved from the clues the user gathered.
4. **Every stop ties to the theme through sourced facts,** in the story's voice, not as a plain description of the building. If research finds no real connection, say so honestly instead of inventing one.
5. **Codes, keys, and passwords are earned.** They come from a puzzle the user solves or a clue they found, and they are used later in the story. Nothing is "handed over" or "used" off-screen.
6. **Real people are history, never characters.** Anyone in the research, the user's request, or the theme (band members, architects, artists) may appear only as sourced fact, never as a voice, tipster, suspect, or "someone who sounds like" them.
7. **Enough story for the time.** Use the time the user gives: at least two stops from about 20 minutes, three or more from about an hour, unless the user asked for fewer or the constraints don't allow it (then say why).

## What changes in the plan data (additive, `schemas.py`)

All new fields are **optional with defaults**, so sessions already stored in Firestore keep loading. The evaluator, not the schema, requires them for new plans.

- `Story.briefing: str | None` — the opening scene in second person, about 3–6 sentences: role, handler and channel, stakes, how a stop works.
- `Story.cast: list[Character | str]` — keep accepting plain strings (old plans). New plans use `Character`:
  - `name: str`, `role: str` (e.g. "handler", "rival", "informant"), `contact: str` (how they reach the user: radio, phone, telegram…), `introduced_in: "briefing" | <checkpoint_id>`.
- `StoryBeat.characters: list[str] = []` — names of the cast members in this beat (Jan and Kyle's "each checkpoint says which characters appear").
- `StoryBeat.clue: str | None` — what the user now holds after this beat ("locker 1021", "the alias Roxy").
- `StoryBeat.uses: list[Id] = []` — earlier beats whose clues this beat builds on. The finale beat uses the stop clues.
- `Checkpoint.theme_link: ThemeLink | None` — `claim_ids: list[str]` (claims at this stop's place) and `why: str` (one sentence, in the story's voice, on how this place ties to the theme).
- `Activity.solution: str | None` — for a `chat_puzzle`, the exact answer (hidden until solved). A clue that comes from the user's answer must match it.

Kyle's `adventure/drafts.py` accepts these in the draft (`story.briefing`, `story.cast` objects, per-stop `characters`, `clue`, `uses`, `theme_link`, `activity.solution`), and the `evaluate_adventure_plan` tool schema documents them.

## How a run plays

1. **Opening reply** (after `save_adventure_plan`): the briefing, then the number of stops, about how long it takes, the first stop, and "Ready?". On "ready": directions to stop 1.
2. **At a stop:**
   - On arrival, a named character from `characters` makes contact through their channel.
   - The scene says why this place matters to the mission, using the `theme_link` fact in the story's voice. For example: "Decca cut 'Rock Around the Clock' upstairs in 1954, and our thief knew it."
   - Then the activity.
3. **An answer:** the guide confirms and names the clue plainly ("Got it: the locker number is 1021, so hold on to that"), says where it points next, and gives directions.
4. **Observations still have a place,** but they must count. Either the observation is itself the clue (only with `verified_feature` evidence, e.g. a counted feature), or it is a short step before a `chat_puzzle` whose answer is the clue. The current "recognition sign" example (observation only proves you're there) goes away.
5. **Finale:** the characters bring the clues together ("Locker 1021, under the name Roxy…"), and the solution follows from them. Then the case file, as now.

## What the evaluator checks (`adventure/validation.py`, new codes)

| Code | Fails when |
|---|---|
| `MISSING_BRIEFING` | `story.briefing` is missing or under ~200 characters |
| `HANDLER_MISSING` | no cast member introduced in the briefing with a `contact` channel |
| `CAST_UNINTRODUCED` | a beat lists a character not introduced in the briefing or at an earlier-or-same stop |
| `CAST_UNUSED` | a cast member never appears in any beat |
| `CLUE_MISSING` | a stop beat has no `clue` |
| `CLUE_UNUSED` | a stop clue is never in a later beat's `uses` (including the finale's) |
| `SOLUTION_UNEARNED` | the finale beat uses no clues, or fewer than `min(2, number of stops)` |
| `OBJECT_UNEARNED` | a beat, finale, or solution mentions a code, key, combination, password, or coordinates that is not the `clue` of an earlier stop beat whose activity the user solves (`chat_puzzle` with `solution`) |
| `THEME_UNLINKED` | the user stated a theme and a stop has no `theme_link`, or its `claim_ids` are not claims at that stop's place |
| `REAL_PERSON_IN_FICTION` (extended) | a cast name matches a person named in the research claims **or in the user's request/theme text** (today it only checks architects) |
| `TOO_FEW_STOPS` | fewer than 2 stops with ≥20 available minutes, or fewer than 3 with ≥60, unless `user_stated` includes a stop count or the violations say nothing else fits |
| `UNSOURCED_LINK` (clarified) | as now; also say "the same link was rejected before" when a draft repeats it |

Keep every current check. Suggestions should say exactly what to add ("give stop_2 a clue that the finale uses").

## Prompt changes (`agent.py`)

- Step 8 (reply after saving): lead with the briefing, then stops, time, first stop, and "Ready?".
- Replace the "recognition sign" example with one where the answer becomes a clue, and add a short worked example (below).
- Guiding: on arrival, name the character from the beat and deliver the theme-linked scene; after a correct answer, state the clue explicitly and where it points.
- Links in chat replies too: only exact `source_urls` from claims (the evaluator cannot see replies).
- Real people: the user's idols can be the subject of the adventure (their venues, records, history) but never characters or voices.
- Honest theme fallback: if no sourced place ties to a real-subject theme, say so in the briefing and offer the nearest honest version ("no documented Strokes sites on this route, so this is a downtown rock-history mystery").

## Research and theme grounding

- In Kyle's run, all three `find_places` searches ("music rock historic landmark", "landmark historic building") returned nothing, and the plan fell back to a bank building. Check how `find_places` handles multi-word queries. Try the theme's key terms separately ("Strokes", "rock", "music venue"), then broaden.
- Prefer places whose claims mention the theme; research 2–4 of them with the theme as `focus`.
- With the Routes API now enabled (September 29), long trips should ride between stops instead of walking 73 minutes.

## Worked example (Jan's run, redone)

- **Briefing:** "You're a freelance tape tracker. Mara Quill, archivist for a small record label, radios you: someone lifted the lost reel from Buddy Holly's final 1958 session and left a trail through old Upper West Side music landmarks. Each stop, message me when you're there; I'll brief you, and what you find unlocks the next lead. Two stops, about 20 minutes."
- **Stop 1, Beacon Theatre** (Broadway at 74th, a few minutes from the start)
  - *Theme link:* the 1929 movie palace built by Samuel "Roxy" Rothafel, now a concert venue.
  - *Puzzle:* "The thief signs as the nickname of the man who built this theatre."
  - *Clue:* the alias "Roxy".
- **Stop 2, Pythian Temple** (West 70th, a short walk south)
  - *Theme link:* Decca's studio there recorded "Rock Around the Clock" (1954) and Holly's String Sessions (October 21, 1958).
  - *Puzzle:* "Roxy's locker number is the month and day of Holly's last session here."
  - *Clue:* locker 1021.
- **Finale:** Mara: "Locker 1021, under Roxy. We have the reel." The solution uses both clues. Every fact is sourced, both characters are introduced in the briefing, and nothing physical is invented at the real addresses.

## Implementation plan (Kyle's agent)

1. `schemas.py`: add the optional fields above (Jan-approved; record the edit in `docs/SHARED_FILE_EDITS.md`), keeping old plans loadable. Add a test that a stored plan without them still loads.
2. `adventure/drafts.py` and the `evaluate_adventure_plan` schema: accept and pass through the new draft fields.
3. `adventure/validation.py`: the checks above, one test per code that fails on a draft breaking just that rule.
4. Regression fixtures: the two accepted drafts in `fixtures/story_regressions/` must now fail (expected: `MISSING_BRIEFING`, `CLUE_*`, `SOLUTION_UNEARNED`, `TOO_FEW_STOPS`, plus `REAL_PERSON_IN_FICTION` and `OBJECT_UNEARNED` for Kyle's). The worked example should pass (seed its research from the Jan fixture's evidence).
5. `agent.py`: the prompt changes above.
6. `scripts/acceptance_checks.py`: add story checks on the saved plan's draft (briefing present, no unintroduced characters, every stop clue used, theme links present for themed queries).
7. Run the full suite with the network blocked (it passes offline today), then the acceptance script against a local server and the deployed URL.

## Model

Jan wants a Claude model instead of Gemini. Claude Sonnet 5 on Vertex AI is reachable in `agentic-ai-msds`, but its quota is still 0 until Jan's quota request is granted. Claude (Jan's agent) will wire `SCAVAGENT_MODEL` and check tool calling once it is. Keep the prompt model-neutral and run the acceptance checks on whichever model is configured. These design changes are needed whichever model runs them.

## Implementation notes

September 29, branch `kyle/story-design-v2`.

- **Where it lives.** Schema fields in `schemas.py`. Draft fields in `adventure/drafts.py` and the `evaluate_adventure_plan` schema; a beat's `uses` names stops (`stop_1`, `stop_2`, … in visiting order), and the plan stores beat ids. Checks in `adventure/validation.py` (`_check_story_design`), the prompt in `agent.py`, and tests in `tests/test_story.py`.
- **Which plans are checked.** A new plan gets every check. A revision of a plan with a briefing gets them for the stops and beats it adds. A revision of an older plan, and a `check` that only re-times a saved plan, skip them, so sessions already under way keep working.
- **`THEME_UNLINKED`** skips stops the user required and camera stops: neither was chosen for the theme, and a camera position has no claims.
- **`TOO_FEW_STOPS`** reads "nothing else fits" as fewer than 10 unused minutes of a limit the user gave. With no time limit from the user (a budget the model chose does not count) it asks for 2 stops. A stop count the user gave is `"stop_count"` in `user_stated`. Since September 29 (night), a limit of 100 minutes or more asks for 4 stops: on Sonnet, the two-hour Strokes run got two.
- **`CLUE_BARE_NUMBER`** (September 29, night): a stop's clue that is only a number ("22", "1897-1929") fails. Live clues were often a building's year or floor count, "arithmetic on a number rather than something the story needs" (docs/MODEL_COMPARISON.md); the message and the prompt ask for a name, an alibi, a place, or a number as what it is ("locker 1021").
- **`OBJECT_UNEARNED`.** A stop's clue is earned when the user solves its `chat_puzzle` and the beat's `clue` states the `solution`. A beat may speak of a code, key, combination, password, or coordinates at such a stop, when it builds on an earned clue (its `uses`), or when an earlier earned clue names that kind of object ("the locker code 1021"). The story's solution counts as building on what the finale uses. (A first version required the clue to name the object; in a live run the model then rewrote its draft five times, so the rule now follows the clue chain.)
- **`REAL_PERSON_IN_FICTION`** also matches cast names against the surnames and nicknames of people named in the claims and in the user's words. The user's words come from a new optional draft field, `user_request`, plus earlier user messages; tools cannot see the current message. It flags a voice or look-alike of a full name ("sounding remarkably like Julian Casablancas") but not a figure of speech ("It looks like Mara Quill was right").
- **Real architects** from the LPC records may be stated as fact in a beat ("Emery Roth built this giant") but not act in the plot or join the cast. The first version flagged any mention, and in a live run it rejected three drafts whose handler stated exactly that sourced fact.
- **Required stops.** `REQUIRED_STOP_MISSING` and `REQUIRED_MISMARKED` now spell out the stop to add (name and coordinates) and how far a stand-in landmark is: in one live run the model marked the Natural History Museum, 275 m away, as the user's corner for seven drafts.
- **Codes beyond the table:** `CLUE_OUT_OF_ORDER`, for a stop beat that uses the clue of a later stop, and `ANSWER_IN_PROMPT` (issue #32), for a `chat_puzzle` whose `solution` appears in its prompt, its hints, or its stop's `theme_link.why`, which is said on arrival. `CAST_UNINTRODUCED` also fails a character introduced at a stop whose beat does not list them (a live run introduced "Viktor" at stop 1 and first showed him in the finale).
- **Guiding across turns.** `get_adventure_state` also returns the cast, the clues still to tell in chat, and the finale once the stops are done. This is a logged edit to Jan's `state.py`: the planning turn scrolls out of the model's 40-message window.
- **Research.** `find_places` searches each key term of a query separately, lists each candidate's `matched_terms`, and ranks the places matching more terms first. With no match nearby, it returns the nearest places with a warning.
- **Regression fixtures.** Both live drafts now fail with the listed codes, among others, and the worked example passes, seeded from Jan's run's research. The Strokes draft is replayed with transit available (faked in the test), since its 73-minute walk came from transit being down.
