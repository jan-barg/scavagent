# Scavagent's tools

Scavagent is one tool-calling agent (the loop in `app.py`, the instructions in `agent.py`). The model decides what to call; the tools look things up, do the arithmetic, and keep progress on the server. Every tool returns the shared result shape from `schemas.py`, `{"ok", "data", "error", "warnings", "freshness"}`, and the `/chat` response lists every call with its `name`, `args`, and `result`.

| Group | Tools | Owner |
|---|---|---|
| Planning and checking | `evaluate_adventure_plan` (original), `save_adventure_plan`, `get_next_directions` | Kyle |
| Places and research | `geocode_place`, `find_places`, `research_place` | Kyle |
| Travel | `get_route`, `get_walking_times`, `get_transit_arrivals` | Kyle |
| History | `find_filming_records` | Kyle |
| Progress and photos | `get_adventure_state`, `update_adventure_state`, `find_camera_checkpoints` (original), `capture_camera_checkpoint` | Jan |

Tools marked as session tools receive the server's session context; the model never names a session.

## `evaluate_adventure_plan`: the plan evaluator (Kyle's original tool)

The model proposes an adventure as a draft: where it starts, what the user asked for, the story, and the stops in order, each with an activity and a story beat. The story follows [STORY_DESIGN.md](STORY_DESIGN.md): a briefing, a cast of invented characters (a handler introduced in the briefing with a contact channel), and at each stop a beat naming its characters, the clue the user earns there, and the earlier clues it `uses`; a `chat_puzzle` carries its exact `solution`, a stop in a themed plan carries a `theme_link` to its own claims, and the finale in `chat_beats` builds on the stop clues. The tool builds the draft into a real plan, then checks it. A plan becomes active only after it passes (`state.save_plan` refuses anything else).

**Building the plan** (`adventure/drafts.py`). Each stop's place and evidence come from what `find_places` and `research_place` actually returned, so the model cannot cite a fact the tools never produced. Every leg is routed on foot or by subway/bus from the planned departure time, and the stops get stable ids. A revision keeps the completed stops, the story, and every clue already revealed. It moves clues from dropped or skipped stops into chat and routes the rest from the user's current location.

**Checking it** (`adventure/validation.py`). The evaluator rebuilds the timeline from the legs, the wait for any train, and the time at each stop, then reports concrete violations:

| Area | Checks (violation codes) |
|---|---|
| Time | Finish plus contingency against the deadline (`DEADLINE_EXCEEDED`) or budget (`DURATION_EXCEEDED`); contingency of at least 10% of travel and dwell (`CONTINGENCY_TOO_SMALL`); the stated total matches the legs (`ESTIMATE_MISMATCH`) |
| The user's request | Each required place is a stop within 150 m of where the user said (`REQUIRED_STOP_MISSING`, `REQUIRED_MISMARKED`), in their order (`REQUIRED_ORDER`), inside its hours (`WINDOW_MISSED`), with enough time (`DWELL_TOO_SHORT`); only allowed modes (`MODE_NOT_ALLOWED`) |
| Route data | Legs connect the visiting order (`ROUTE_GAP`, `EMPTY_ROUTE`); transit timings neither stale nor already missed (`STALE_ROUTE`); no synthetic fixture data in a live plan (`SYNTHETIC_DATA`) |
| Physical honesty | A physical task only with physical-feature evidence about that stop's own place (`UNSUPPORTED_PHYSICAL_TASK`, `EVIDENCE_ELSEWHERE`); no invented object for the user to find, and no invented person handing them one (`INVENTED_PROP`); camera stops only at enabled, field-verified positions near the stop (`CAMERA_UNAVAILABLE`, `CAMERA_ELSEWHERE`) |
| Story | Every optional stop moves the story (`STOP_WITHOUT_STORY`); beats tied to the right stop and used once (`BEAT_MISMATCH`, `BEAT_REUSED`, `ORPHAN_BEAT`); no clue stranded at a skipped stop (`CLUE_STRANDED`); no real person in the fiction: an architect from the sources acting in the plot or cast (stating what they built is fine), a cast member sharing a name with someone the sources or the user name, or a voice "sounding like" someone (`REAL_PERSON_IN_FICTION`); a premise and a solution (`MISSING_PREMISE`, `MISSING_SOLUTION`); a puzzle has hints (`MISSING_HINTS`); links only from the claims' `source_urls`, saying so when a draft repeats a rejected one (`UNSOURCED_LINK`) |
| Story design ([STORY_DESIGN.md](STORY_DESIGN.md); a new plan, and what a revision adds) | A briefing (`MISSING_BRIEFING`) with a handler who has a contact channel (`HANDLER_MISSING`); characters introduced in the briefing or in an earlier or the same stop's beat (`CAST_UNINTRODUCED`), and every cast member in some beat (`CAST_UNUSED`); a clue at every stop (`CLUE_MISSING`) that a later beat uses (`CLUE_UNUSED`), never before the user has it (`CLUE_OUT_OF_ORDER`); a finale built on at least two stop clues, or one with one stop (`SOLUTION_UNEARNED`); codes, keys, combinations, passwords, and coordinates only from solved `chat_puzzle`s: at such a stop, or in a beat that builds on their clues (`OBJECT_UNEARNED`); with a stated theme, each chosen stop linked through its own claims (`THEME_UNLINKED`); at least 2 stops from 20 available minutes and 3 from 60, unless the user asked for fewer or their own time limit leaves no room, and 2 when the user gave no limit (`TOO_FEW_STOPS`) |
| Revisions | Supersedes the active plan (`REVISION_LINEAGE`); completed stops unchanged (`COMPLETED_CHANGED`); revealed clues kept word for word (`REVEALED_BEAT_DROPPED`, `REVEALED_BEAT_CHANGED`); a required stop dropped only with the user's waiver (`REQUIRED_STOP_DROPPED`) |

It also returns the timeline (arrival at each stop, New York time), the finish time, slack against the limit, notes (e.g. a stop with no sourced facts should carry fiction only), and, when the plan runs long, which optional stops to drop and roughly how much each saves. `kind: "check"` re-times the adventure under way from now, for example against "I only have 15 minutes", without changing it.

**Why it is original.** It is not a wrapper around an API. It turns the product's rules (real places, honest time, invented but clearly fictional stories, no promises about physical things nobody verified, no lost progress when plans change) into checks with concrete, fixable violations. The model gets the specific rule it broke and what to change. The evaluator cannot certify that a quoted source is true; it checks arithmetic, references, and the rules above. The tests in `tests/test_validation.py` break one rule at a time against labeled fixtures; `tests/test_story.py` does the same for the story design, from the design's worked example, and replays the two live runs in `fixtures/story_regressions/` that the evaluator used to accept.

## Places and research

- **`geocode_place(text)`**: a cross street, address, or landmark typed by the user → `data.location` (a `LocationContext`) and `match_type`. Intersections use the node both streets share in OpenStreetMap (Overpass, with a mirror); addresses use NYC Planning Labs GeoSearch; landmarks use Nominatim. Several places matching one cross street come back flagged for the user to confirm; places outside the five boroughs are `OUTSIDE_COVERAGE`.
- **`find_places(lat, lng, radius_m, query)`**: candidate stops from Wikipedia articles with coordinates and NYC Landmarks Preservation Commission designations, nearest first, with designated landmarks kept even in dense blocks. Landmarks recorded only at a shared tax-lot center (the Met and the Arsenal share Central Park's) are skipped with a warning rather than placed wrongly. Wikipedia's search needs every word of a query, so `query` is split into key terms searched one at a time (a quoted phrase stays whole; "historic", "landmark", and the like are dropped when something more specific is given). Each candidate lists its `matched_terms`, candidates matching more terms come first, and when nothing nearby matches the nearest places come back with a warning.
- **`research_place(place_id, focus)`**: a `PlaceEvidence` record of claims quoted from Wikipedia (each linking the exact revision, and section for a focus such as "architecture") and transcribed from LPC records (architect, style, dates). No claim is marked as a physical feature visible today; that needs fieldwork.

## Travel

- **`get_route(stops, modes, depart_at, transit_types)`**: one `RouteLeg` per consecutive pair, timed from the departure plus time at each stop. Walking comes from FOSSGIS Valhalla (OSRM as fallback); legs start with a summary line ("Walk about 8 min heading southwest via Central Park West, then Columbus Avenue"), because Manhattan sidewalks are unnamed in OpenStreetMap. With transit allowed, a leg over 12 minutes on foot is looked up in Google Routes and rides only when it saves at least 4 minutes; transit legs name the line, direction, stations, departure and leave-by times, fare, and count the wait for the train.
- **`get_walking_times(origin, destinations)`**: walking minutes to up to 25 candidates, for ranking.
- **`get_transit_arrivals(station, line, direction, lat, lng)`**: live next trains per direction (Uptown/Downtown, with each train's terminal) from the MTA's GTFS-realtime feeds, plus the lines' active service alerts. Feeds older than 10 minutes are refused (`STALE_DATA`) rather than shown as live.
- **`get_next_directions()`**: the way to the current stop from the saved plan; stale transit legs are looked up again, and a user who has wandered more than 200 m gets directions from where they are.

## History

- **`find_filming_records(street, zip_code, date_from, date_to)`**: film permits that held parking on a street, with the data's coverage. On September 28, 2026 the newest permit started June 29, 2026, so requests for later dates are `STALE_DATA` and the tool never implies a current set.

## Configuration

| Variable | Use |
|---|---|
| `SCAVAGENT_ROUTES_PROJECT` | Google Cloud project billed for transit lookups made with user credentials (the Routes API must be enabled there). On Cloud Run the runtime service account's own project pays if this is unset. |
| `GOOGLE_MAPS_API_KEY` | Alternative to the above: an API key restricted to the Routes API. |
| `SCAVAGENT_ROUTES_DAILY_LIMIT` | Transit lookups per server process per day (default 500); Google's free tier is 10,000 a month. |

Walking, places, research, arrivals, and film permits need no keys. Public servers (Overpass, Nominatim, FOSSGIS) are shared and low-volume; the adapters identify the app, space Nominatim requests a second apart, and cache repeats.

## Sources and attribution

Wikipedia (CC BY-SA 4.0); map and route data © OpenStreetMap contributors (ODbL) via Overpass, Nominatim, Valhalla, and OSRM; Google Routes API; NYC Landmarks Preservation Commission and NYC Film Permits via NYC Open Data; MTA Subway Stations via NY Open Data; MTA GTFS-realtime and service alert feeds; NYC Planning Labs GeoSearch.
