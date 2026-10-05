# Lou's tools

Lou is one tool-calling agent. The loop is in `app.py`, the instructions are in `agent.py`, and the model decides which tool to call and when. The tools look things up, do the time arithmetic, and keep each user's progress on the server, so the agent never invents a fact or a travel time, and the server, not the model, remembers what the user has done. The `/chat` response lists every call with its `name`, `args`, and `result`, and the page shows each call as it runs.

## The tools

| Tool                        | What it does                                     | External data                                         |
| --------------------------- | ------------------------------------------------ | ----------------------------------------------------- |
| `evaluate_adventure_plan`   | Builds a draft into a routed plan and checks it. | OpenStreetMap routing, Google Routes, NYC DOT cameras |
| `find_camera_checkpoints`   | Finds sidewalk spots a traffic camera can see.   | NYC DOT cameras, the calibrated catalogue             |
| `capture_camera_checkpoint` | Saves the camera's live still of the user.       | NYC DOT cameras                                       |
| `geocode_place`             | Turns a typed place into coordinates.            | OpenStreetMap, NYC GeoSearch                          |
| `find_places`               | Lists candidate stops nearby.                    | Wikipedia, NYC Landmarks Preservation Commission      |
| `research_place`            | Collects sourced facts about one place.          | Wikipedia, NYC Landmarks Preservation Commission      |
| `get_route`                 | Times a walk, subway, or bus ride between stops. | OpenStreetMap routing, Google Routes                  |
| `get_transit_arrivals`      | Next trains at a station, with service alerts.   | MTA GTFS-realtime                                     |
| `get_next_directions`       | The way to the current stop.                     | OpenStreetMap routing, Google Routes                  |
| `save_adventure_plan`       | Stores a plan that passed the evaluator.         | None                                                  |
| `get_adventure_state`       | Reads the user's progress.                       | None                                                  |
| `update_adventure_state`    | Records one change to progress.                  | None                                                  |

Tools that read or change a session get it from the server; the model never names one.

## How tools report results and errors

Every tool returns the same shape (`schemas.py`): `ok`, `data`, `error`, `warnings`, and `freshness`. Warnings say what a result can't promise, such as a distance that isn't a walking route. Freshness says whether data is live, scheduled, or a static reference, and when it was fetched.

A failure reaches the model as a short error it can act on, never a stack trace. Each error has a code (`INVALID_ARGUMENT`, `NO_MATCH`, `UPSTREAM_UNAVAILABLE`, `STALE_DATA`, `OUTSIDE_COVERAGE`, `PLAN_INFEASIBLE`, `MISSING_EVIDENCE`, `STATE_VERSION_CONFLICT`, `UNKNOWN_TOOL`, or `INTERNAL_ERROR`), says whether a retry can help, and names the next step. Completing the second stop before the first gets:

```json
{"ok": false, "data": null, "warnings": [], "freshness": null,
 "error": {"code": "INVALID_ARGUMENT", "message": "stop_2 is not the current checkpoint (stop_1).",
           "retryable": false, "next_step": "Complete or skip stop_1 first."}}
```

The harness (`tools.run_tool` and the loop in `app.py`) handles the model's own mistakes the same way. An unknown tool gets the list of real ones. A wrong argument gets the closest correct name ("Did you mean 'text'?"). Malformed JSON, and a tool that would start after the turn's 4-minute limit, also come back as errors. Any other exception becomes `INTERNAL_ERROR` with only its type, so request details and credentials stay in the server log.

## `evaluate_adventure_plan`: the plan evaluator

The agent never shows a plan it wrote freehand. It sends this tool a draft: where the user starts, what they asked for (time, destination, required stops, modes, theme, a camera stop), the story, and the stops in order. The tool builds a real plan from it, checks it, and returns each violation with its fix. Only a passing plan can be saved.

### Building the plan

`adventure/drafts.py` takes each stop's place and facts from what the research tools actually returned, so the model can't cite a fact it never got. It routes every leg on foot, or by subway and bus with the wait for the train. A revision keeps completed stops and told clues, moves clues from dropped stops into chat, and routes the rest from where the user is. The story fields follow [STORY_DESIGN.md](STORY_DESIGN.md): a briefing, a cast with a handler, a beat and a clue at each stop, and a finale built on the clues.

### What it checks

`adventure/validation.py` rebuilds the timeline from the legs, the train waits, and the time at each stop. It calls no model.

| Area | Checks (violation codes) |
|---|---|
| Time | The finish plus a reserve meets the deadline (`DEADLINE_EXCEEDED`) or time budget (`DURATION_EXCEEDED`). The reserve is at least 10% of travel and stop time (`CONTINGENCY_TOO_SMALL`), and the stated total matches the legs (`ESTIMATE_MISMATCH`). |
| The user's request | Each required place is a stop within 150 m of where the user said (`REQUIRED_STOP_MISSING`, `REQUIRED_MISMARKED`), in their order (`REQUIRED_ORDER`), during its hours (`WINDOW_MISSED`), with enough time there (`DWELL_TOO_SHORT`). Legs use only allowed modes (`MODE_NOT_ALLOWED`). A requested camera stop is included when a verified spot near the route fits the free time; the evaluator searches for one itself (`CAMERA_STOP_MISSING`). |
| Route data | The legs connect the stops in order (`ROUTE_GAP`, `EMPTY_ROUTE`), transit times are neither stale nor already missed (`STALE_ROUTE`), and a live plan has no synthetic test data (`SYNTHETIC_DATA`). |
| Physical honesty | A physical task needs evidence about that stop's own place (`UNSUPPORTED_PHYSICAL_TASK`, `EVIDENCE_ELSEWHERE`). No object or helper is invented for the user to find (`INVENTED_PROP`). Camera stops use only enabled, verified spots near the stop (`CAMERA_UNAVAILABLE`, `CAMERA_ELSEWHERE`). |
| Sources and people | Links come only from the claims' sources, and a repeated rejected link is called out (`UNSOURCED_LINK`). No real person appears in the fiction (`REAL_PERSON_IN_FICTION`): not an architect from the sources acting in the plot, not someone the sources or the user name, not a voice "sounding like" someone. |
| Story | Every optional stop moves the story (`STOP_WITHOUT_STORY`). Beats belong to the right stop and are used once (`BEAT_MISMATCH`, `BEAT_REUSED`, `ORPHAN_BEAT`), and no clue is stranded at a skipped stop (`CLUE_STRANDED`). There is a premise and a solution (`MISSING_PREMISE`, `MISSING_SOLUTION`), and each puzzle has hints (`MISSING_HINTS`). |
| Story design (new plans, and what a revision adds) | A briefing (`MISSING_BRIEFING`) with a handler who has a contact channel (`HANDLER_MISSING`). Characters are introduced before they act (`CAST_UNINTRODUCED`), every one appears (`CAST_UNUSED`), and none is named Lou (`CAST_NAMED_LIKE_GUIDE`). Every stop has a clue (`CLUE_MISSING`), in words rather than a bare number (`CLUE_BARE_NUMBER`), that a later beat uses (`CLUE_UNUSED`) only after the user has it (`CLUE_OUT_OF_ORDER`). The finale builds on the stop clues (`SOLUTION_UNEARNED`). Codes, keys, and passwords come only from solved puzzles (`OBJECT_UNEARNED`), and no puzzle gives away its own answer (`ANSWER_IN_PROMPT`). With a stated theme, each stop links to it through its own sourced facts (`THEME_UNLINKED`). |
| Enough stops | With a time limit from the user, at least 2 stops from 20 minutes, 3 from 60, and 4 from 100, unless the user asked for fewer or there's no room. Without one, 2 (`TOO_FEW_STOPS`). |
| Revisions | A revision replaces the active plan (`REVISION_LINEAGE`), leaves completed stops alone (`COMPLETED_CHANGED`), keeps told clues word for word (`REVEALED_BEAT_DROPPED`, `REVEALED_BEAT_CHANGED`), and drops a required stop only with the user's waiver (`REQUIRED_STOP_DROPPED`). |

It returns the timeline in New York time, the slack against the limit, notes for the reply ("no verified camera position is near this route"), and, when a plan runs long, which optional stops to drop and what each saves. `kind: "check"` re-times an adventure under way, for example after "I only have 15 minutes".

### What the model sees

Each message names the rule and the fix. Two from live runs:

- `REQUIRED_MISMARKED`: "stop_1 (American Museum of Natural History) is marked required but is not at a place the user required, 270 m from West 81st Street and Columbus Avenue. Keep it as an optional stop (required_by_user false), and put the required stop at the user's place itself."
- `CLUE_BARE_NUMBER`: "stop_2's clue is only a number ("22"). Make it something the story needs, stated in words: a name, an alibi, a place, or a number as what it is ("locker 1021"), not digits to add up."

### Tests and limits

`tests/test_validation.py` and `tests/test_story.py` break one rule at a time, and `tests/test_story.py` replays two live drafts an earlier version wrongly accepted. `tests/test_planning_tools.py` covers drafts, revisions, saving, and camera requests. The evaluator can't confirm that a source is true, and it doesn't see the agent's chat replies; the instructions cover those.

## `find_camera_checkpoints` and `capture_camera_checkpoint`: camera souvenirs

New York's Department of Transportation publishes live stills from its traffic cameras. These tools turn some of them into a souvenir photographer: Lou sends the user to a spot a camera can see, saves the still when they're in position, asks whether they can see themselves, and shows the photo again in the finale.

### The spots

A camera's mounting point is no use to someone on the sidewalk, so `data/camera_catalogue.json` holds standing positions: 151 spots on 102 Manhattan cameras. Each has coordinates, an address, the side of the street, directions for the user ("Stand beside the tall pole near the curb. Face south toward the intersection."), and where in the frame a person shows up. `field_verified` means someone stood there and saw themselves; `image_verified` means the spot was matched on a live still against map imagery but nobody has stood there yet.

### How the spots were made

All 308 online Manhattan street cameras were screened, and 259 were usable. On a private calibration workbench, Claude drafted spots from each still and the street map, and Jan approved 151 of them. `scripts/calibrate_camera.py` turns an approved pin into a catalogue entry, with the address and side of the street derived from OpenStreetMap (`scripts/camera_geometry.py`), and logs who verified it, when, and the SHA-256 of the still it was judged on.

### Finding and capturing

`find_camera_checkpoints` takes a point or a route and returns up to 3 enabled, verified spots within 800 m, ranked by distance from where the person stands. It fetches the current still for the 5 nearest at most, so a spot whose camera is offline is skipped rather than offered.

`capture_camera_checkpoint` works only at an enabled, verified spot. It fetches the still with time and size limits, follows no redirects, and checks that the bytes are the image type the server claims. The photo is saved at once, so it survives a turn that fails later, and it's served at `/media/{asset_id}`, where the unguessable id is the only key. A resent message reuses the photo instead of taking another.

### In planning and guiding

The evaluator accepts a camera stop only at an enabled, verified spot within 150 m (`CAMERA_UNAVAILABLE`, `CAMERA_ELSEWHERE`). When the user asks for a camera and the plan has none, it searches the route and requires a spot that fits the free time (`CAMERA_STOP_MISSING`). At the stop, `get_next_directions` gives the standing directions.

### What the model sees

- An image-verified spot: "This position was matched on the camera image, not tested in person. If they cannot find themselves, suggest a step toward the curb and offer a retake."
- After a capture: "Frame time is unknown; retrieval time is not exposure time. Ask the user whether they are visible. Reuse this saved media URL in the finale."

### Tests and limits

54 tests cover the finder, the capture, malformed DOT responses, photo reuse, importing spots, and street geometry (`tests/test_cameras.py`, `test_camera_tools.py`, `test_calibrate_camera.py`, `test_camera_geometry.py`). No spot is `field_verified` yet; that takes someone standing there. DOT stills have no exposure time, so the user's answer is the only proof they're in frame.

## Places and research

`geocode_place(text)` resolves cross streets through the node both streets share in OpenStreetMap, addresses through NYC GeoSearch, and landmarks through Nominatim. A cross street with several matches asks the user to confirm, and places outside the five boroughs get `OUTSIDE_COVERAGE`.

`find_places(lat, lng, radius_m, query)` returns nearby Wikipedia articles and NYC landmark designations, nearest first. Wikipedia's search needs every word of a query, so each key term is searched on its own, and places matching more terms come first.

`research_place(place_id, focus)` returns claims quoted from Wikipedia, each linked to the exact revision, and from Landmarks Preservation Commission records (architect, style, dates).

The public servers behind these are shared, so the adapters identify the app, keep Nominatim requests a second apart, and cache repeats.

## Routes and transit

`get_route(stops, modes, depart_at, transit_types)` returns one timed leg per pair of stops. Walking comes from Valhalla, with OSRM as a fallback. With transit allowed, a walk over 12 minutes is checked in Google Routes and ridden only if that saves at least 4 minutes; a transit leg names the line, direction, stations, and leave-by time, and counts the wait for the train.

`get_transit_arrivals(station, line, direction, lat, lng)` lists the next trains each way from the MTA's realtime feeds, with service alerts. A feed more than 10 minutes old is refused (`STALE_DATA`) rather than shown as live.

`get_next_directions()` gives the way to the current stop, or to the destination after the last one. It looks up stale transit legs again, and starts from the user's location when they're more than 200 m from the leg's start.

## Progress and saving

`save_adventure_plan(draft_id, start_now)` stores a draft that passed. A draft more than 10 minutes old is refused, since its times no longer start from now, and a new plan is never saved over an adventure under way.

`get_adventure_state()` returns the status, each stop's outcome, the current stop in full (prompt, answer rule, hints, fallback), the cast, the clues told and still to tell, the user's location, and photos. The solution appears only once every stop is done.

`update_adventure_state(operation, ...)` records one change: a stop completed, skipped, or blocked; a clue told; the destination reached; the adventure started, finished, or abandoned; or whether the user is visible in a photo. Stops are completed in order, and a required stop is skipped only with the user's waiver. Stops can't be skipped before the start; a change of plan then means a new plan. Finishing needs every stop resolved and the destination reached, and an outdated `expected_version` gets `STATE_VERSION_CONFLICT` instead of overwriting newer progress.

A development-only `load_dev_adventure` loads labeled test adventures; the deployed site never offers it.

## Sources and attribution

Wikipedia (CC BY-SA 4.0); map and route data © OpenStreetMap contributors (ODbL) via Overpass, Nominatim, Valhalla, and OSRM; Google Routes API; NYC Landmarks Preservation Commission via NYC Open Data; MTA Subway Stations via NY Open Data; MTA GTFS-realtime and service alert feeds; NYC Planning Labs GeoSearch. The page uses the Overpass and Atkinson Hyperlegible (Braille Institute) fonts under the SIL Open Font License (`static/fonts/OFL-*.txt`) and Phosphor Icons under the MIT License (`static/licenses/phosphor-MIT.txt`).
