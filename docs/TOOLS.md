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

The agent never presents a plan it wrote freehand. It sends a **draft** to this tool: where the user starts, what they asked for (time, destination, required stops, modes, theme, a camera stop), the story, and the stops in visiting order. The tool builds the draft into a real plan, checks it, and returns concrete violations with the fix for each. Only a passing plan can be saved (`save_adventure_plan`, and `state.save_plan` refuses anything else).

**Building the plan** (`adventure/drafts.py`):

- Each stop's place and evidence come from what `find_places` and `research_place` actually returned, so the model cannot cite a fact the tools never produced.
- Every leg is routed on foot, or by subway and bus with the wait for the train, from the planned departure.
- The stops, beats, and legs get stable ids.
- A revision keeps the completed stops, the story, and every clue already revealed. It moves clues from dropped or skipped stops into chat, and routes the rest from where the user is now.

The story fields follow [STORY_DESIGN.md](STORY_DESIGN.md):

- a briefing, and a cast of invented characters with a handler who has a contact channel;
- at each stop, a beat naming its characters, the clue the user earns, and the earlier clues it `uses`;
- a `solution` for each puzzle, and a `theme_link` to the stop's own claims;
- a finale that builds on the clues.

**Checking it** (`adventure/validation.py`). The evaluator rebuilds the timeline from the legs, the waits for trains, and the time at each stop. It runs no model and makes no network call, except to search the route for a camera position the user asked for. It checks:

| Area | Checks (violation codes) |
|---|---|
| Time | The finish plus contingency meets the deadline (`DEADLINE_EXCEEDED`) or budget (`DURATION_EXCEEDED`). Contingency is at least 10% of travel and dwell (`CONTINGENCY_TOO_SMALL`). The stated total matches the legs (`ESTIMATE_MISMATCH`). |
| The user's request | Each required place is a stop within 150 m of where the user said (`REQUIRED_STOP_MISSING`, `REQUIRED_MISMARKED`), in their order (`REQUIRED_ORDER`), inside its hours (`WINDOW_MISSED`), and with enough time (`DWELL_TOO_SHORT`). Legs use only the allowed modes (`MODE_NOT_ALLOWED`). A camera stop the user asked for is included when a verified position near the route fits the free time: the evaluator searches for one itself rather than trusting "none fits" (`CAMERA_STOP_MISSING`). |
| Route data | The legs connect the visiting order (`ROUTE_GAP`, `EMPTY_ROUTE`). Transit timings are neither stale nor already missed (`STALE_ROUTE`). A live plan has no synthetic fixture data (`SYNTHETIC_DATA`). |
| Physical honesty | A physical task needs physical-feature evidence about that stop's own place (`UNSUPPORTED_PHYSICAL_TASK`, `EVIDENCE_ELSEWHERE`). No object is invented for the user to find, and no person is invented to hand them one (`INVENTED_PROP`). Camera stops are only at enabled, verified positions near the stop (`CAMERA_UNAVAILABLE`, `CAMERA_ELSEWHERE`). |
| Sources and people | Links come only from the claims' `source_urls`, and the message says when a draft repeats a rejected link (`UNSOURCED_LINK`). No real person is in the fiction (`REAL_PERSON_IN_FICTION`). That covers an architect from the sources acting in the plot or cast (stating what they built is fine), a cast member sharing a name with someone the sources or the user name, and a voice "sounding like" someone. |
| Story | Every optional stop moves the story (`STOP_WITHOUT_STORY`). Beats are tied to the right stop and used once (`BEAT_MISMATCH`, `BEAT_REUSED`, `ORPHAN_BEAT`). No clue is stranded at a skipped stop (`CLUE_STRANDED`). There is a premise and a solution (`MISSING_PREMISE`, `MISSING_SOLUTION`), and each puzzle has hints (`MISSING_HINTS`). |
| Story design (a new plan, and what a revision adds) | There is a briefing (`MISSING_BRIEFING`) with a handler who has a contact channel (`HANDLER_MISSING`). Characters are introduced in the briefing or in an earlier or the same stop's beat (`CAST_UNINTRODUCED`), and every cast member appears in some beat (`CAST_UNUSED`). No character has the guide's name, Lou, as a whole word (`CAST_NAMED_LIKE_GUIDE`). Every stop has a clue (`CLUE_MISSING`), in words, not a bare number (`CLUE_BARE_NUMBER`), that a later beat uses (`CLUE_UNUSED`), never before the user has it (`CLUE_OUT_OF_ORDER`). The finale builds on at least two stop clues, or on one when there is one stop (`SOLUTION_UNEARNED`). Codes, keys, combinations, passwords, and coordinates come only from solved puzzles (`OBJECT_UNEARNED`). A puzzle never names its own answer in its prompt, its hints, or its theme link's sentence (`ANSWER_IN_PROMPT`). With a stated theme, each chosen stop is linked through its own claims (`THEME_UNLINKED`). |
| Enough stops | When the user gives a time limit, the plan has at least 2 stops from 20 minutes, 3 from 60, and 4 from 100, unless the user asked for fewer or their own limit leaves no room. With no limit, it has 2 (`TOO_FEW_STOPS`). |
| Revisions | A revision supersedes the active plan (`REVISION_LINEAGE`), and completed stops stay unchanged (`COMPLETED_CHANGED`). Revealed clues are kept word for word (`REVEALED_BEAT_DROPPED`, `REVEALED_BEAT_CHANGED`). A required stop is dropped only with the user's waiver (`REQUIRED_STOP_DROPPED`). |

With the violations it returns:

- the timeline, with arrival at each stop in New York time;
- the finish time and the slack against the limit;
- notes, for example "no verified camera position is near this route", which the reply should repeat;
- when the plan runs long, which optional stops to drop or swap, and roughly how much each saves.

`kind: "check"` re-times the adventure under way from now without changing it, for example against "I only have 15 minutes".

**What the model sees.** Each message names the rule and the fix. From live runs:

- `REQUIRED_MISMARKED`: "stop_1 (American Museum of Natural History) is marked required but is not at a place the user required, 270 m from West 81st Street and Columbus Avenue. Keep it as an optional stop (required_by_user false), and put the required stop at the user's place itself."
- `CLUE_BARE_NUMBER`: "stop_2's clue is only a number ("22"). Make it something the story needs, stated in words: a name, an alibi, a place, or a number as what it is ("locker 1021"), not digits to add up."
- `OBJECT_UNEARNED`: "beat_3 (chat) mentions "combination", but it builds on no puzzle the user solves. Give stop_1 and stop_2 each its puzzle's exact answer as activity.solution, and state that answer in the stop's clue."

**Why it is original.** It isn't a wrapper around an API. It turns the product's rules into checks with concrete, fixable violations: real places, honest time, fiction kept apart from fact, no promises about physical things nobody verified, a story whose clues add up, and no lost progress when plans change. Several rules came from live runs where a plan passed and still disappointed ([STORY_DESIGN.md](STORY_DESIGN.md) records them).

**Tests and limits.**

- `tests/test_validation.py` breaks one rule at a time against labeled fixtures.
- `tests/test_story.py` does the same for the story design, starting from the design's worked example. It also replays the two live runs in `fixtures/story_regressions/` that an earlier version accepted.
- `tests/test_planning_tools.py` covers drafts, revisions, saving, and camera requests.

The evaluator checks arithmetic, references, and the rules above. It can't certify that a quoted source is true, and it doesn't see the agent's chat replies, so the instructions cover those.

## `find_camera_checkpoints` and `capture_camera_checkpoint`: camera souvenirs (Jan's original tool)

New York's Department of Transportation publishes live stills from its traffic cameras. These two tools turn some of those cameras into a souvenir photographer. Lou sends the user to a spot on the sidewalk where a camera can see them. When the user says they're in position, Lou saves that camera's live still, asks whether they can see themselves, and shows the photo again in the finale's case file.

**The spots** (`data/camera_catalogue.json`). A camera's mounting point is useless to a pedestrian: it's on a pole above the road. So the catalogue holds standing positions instead: 151 spots on 102 Manhattan cameras (Upper East and West Side, and Midtown). Each spot has:

- where to stand: coordinates, an address, a landmark, and the side of the street;
- what to tell the user ("Stand beside the tall pole near the curb. Face south toward the intersection.");
- what the camera sees: notes on the view, the box in the frame where a person appears (`person_region`), and how visible they'll be;
- its verification: `field_verified` (someone stood there and saw themselves) or `image_verified` (the spot was matched on a live still against map imagery, but nobody has stood there yet). The date is recorded, and only `enabled` spots are offered.

**How a spot is made** (`scripts/calibrate_camera.py`, `scripts/camera_geometry.py`).

1. All 308 online Manhattan street cameras were pulled and screened, and 259 were usable.
2. On a private calibration workbench, Claude drafted spots from each still and the street map, and Jan approved, edited, or skipped each one. He approved 151 spots and skipped 48 cameras.
3. `import-spots` turns an approved pin into a catalogue entry. It derives the address, the stop's name, and the side of the street from OpenStreetMap geometry.
4. Every spot gets a `field_log` record: who verified it, how, when, and the SHA-256 of the still it was judged on. The stills themselves stay outside Git.
5. A Codex review found 4 flawed spots. They were excluded with the reason recorded, re-drafted, and re-approved.

**Finding a spot** (`find_camera_checkpoints`). It takes a point or a route (2–50 coordinates) and returns up to 3 spots:

- only enabled, verified spots, within 800 m of the route by default, ranked by distance from the standing position, never from the camera;
- it checks at most the 5 nearest spots against DOT's live catalogue and fetches each one's current still, so a spot whose camera is offline or broken is skipped, not offered;
- each result carries its distance, an out-and-back detour estimate, the time the feed was checked, and a fallback ("skip the photo and continue with a non-camera activity");
- the warnings say what the tool can't promise: the distance isn't a walking route, a retrieved still doesn't prove the view still matches, and an `image_verified` spot must be disclosed to the user.

**Capturing** (`capture_camera_checkpoint`, a session tool):

- It runs only for an enabled, verified spot. It refuses disabled or unverified spots, and synthetic test fixtures can never be captured.
- It fetches the still with bounded timeouts and a size limit. It checks that the bytes really are a JPEG, PNG, or WebP image matching the declared type, and it never follows redirects.
- The photo is saved to the session at once (Cloud Storage when deployed), so it survives a turn that fails later. It is served at `/media/{asset_id}`, where the unguessable id is the only key.
- At most one capture per spot per message: if a turn dies after taking the photo and the message is sent again, the saved photo is reused, not retaken.
- The model never supplies URLs, storage, or session ids; the server binds them, and the finder rejects arguments meant only for the server.
- The result says what it can't know: DOT gives no exposure time, so retrieval time isn't treated as the moment of the photo, and the user is asked whether they're visible (`set_photo_visibility`).

**In planning and guiding.**

- The evaluator accepts a camera stop only at an enabled, verified spot within 150 m of the stop (`CAMERA_UNAVAILABLE`, `CAMERA_ELSEWHERE`).
- When the user asks for a camera and the plan has none, the evaluator searches the route itself and requires a spot whose detour fits the free time (`CAMERA_STOP_MISSING`). A camera that doesn't fit becomes a note instead.
- When a plan runs long, the camera stop is suggested for cutting last.
- At the stop, `get_next_directions` gives the standing instructions and the spot's verification status.

**What the model sees.** From the tool results:

- image-verified spot: "This position was matched on the camera image, not tested in person. If they cannot find themselves, suggest a step toward the curb and offer a retake."
- offline feeds: "None of the candidate feeds checked is currently usable." The next step is "Offer a non-camera activity; retry later if the user still wants a photo."
- after a capture: "Frame time is unknown; retrieval time is not exposure time. Ask the user whether they are visible. Reuse this saved media URL in the finale."

**Why it is original.** Public traffic-camera feeds exist, but nothing maps them to where a person should stand to be seen, or turns them into a photo of a user walking a route. The tool's value is that dataset of calibrated, verified standing spots, plus a capture that is honest about what it can't verify.

**Tests and limits.**

- 54 tests across four files:
  - `tests/test_cameras.py` covers the finder, the capture, and malformed DOT responses;
  - `tests/test_camera_tools.py` covers the session wrappers and once-per-message reuse;
  - `tests/test_calibrate_camera.py` covers import from approved notes;
  - `tests/test_camera_geometry.py` covers street geometry and how a spot is described.
- No spot is `field_verified` yet. That needs someone standing there, which is the walk test's job ([WALK_TEST.md](WALK_TEST.md)).
- DOT stills carry no exposure time, so the user's own answer is the only proof they're in frame.
- The spots cover parts of Manhattan only.

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

Wikipedia (CC BY-SA 4.0); map and route data © OpenStreetMap contributors (ODbL) via Overpass, Nominatim, Valhalla, and OSRM; Google Routes API; NYC Landmarks Preservation Commission and NYC Film Permits via NYC Open Data; MTA Subway Stations via NY Open Data; MTA GTFS-realtime and service alert feeds; NYC Planning Labs GeoSearch. The page uses the Overpass and Atkinson Hyperlegible (Braille Institute) fonts under the SIL Open Font License (`static/fonts/OFL-*.txt`) and Phosphor Icons under the MIT License (`static/licenses/phosphor-MIT.txt`).
