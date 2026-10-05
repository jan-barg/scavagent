# Lou

Lou turns a walk through New York City into a short mystery. Tell it where you're starting, and if you like, how long you have, where you need to end up, a stop you must pass, and a theme. Lou researches real places on your way, times the route, writes a story whose clues you earn at each stop, and guides you there one stop at a time. At some corners, a city traffic camera takes your souvenir photo.

Try it: https://scavagent-b57mvtutma-ue.a.run.app

<p><img src="docs/screenshots/redesign/phone-3-briefing.png" width="220" alt="The briefing on a phone"> <img src="docs/screenshots/redesign/phone-2-planning-live-log.png" width="220" alt="The live tool log while Lou plans"> <img src="docs/screenshots/redesign/phone-5-clue-and-directions.png" width="220" alt="A clue earned, and directions to the next stop"></p>

Built by Jan Barganowski and Kyle Coletta for Agentic AI for Data Science (IEOR4570) at Columbia University, October 2026.

## Example prompts

1. "I'm at Central Park West and West 86th Street. Give me a 1960s spy adventure."
2. "I'm at Central Park West and West 86th Street. I have 45 minutes, need to finish at West 72nd Street and Broadway, and must pass West 81st Street and Columbus Avenue. Walking only, architecture theme, and include a camera stop if one fits."
3. "I'm in FiDi and need to be in Morningside Park in 3 hours. I'm a big fan of the band The Strokes. Build me a trip with relevant stops to the band, and make sure it goes by a traffic camera at least once."

## How it works

Tell Lou where you are, where you want to go, how long you have to get there, and any potential themes or special interests you'd like to focus on. Lou then finds real places nearby and gathers sourced facts about them from Wikipedia and NYC landmark records, routes the journey, creates a story with a briefing, a handler who radios in, and a clue at each stop. Before you see the plan, the plan evaluator checks it, making sure the times add up, every fact has a source, no real person is cast in the fiction, and the clues lead to the solution. A plan that fails goes back to Lou to fix.

Planning takes a minute or two, and the page shows each tool as it runs. Once an adventure is planned, start making your way towards the first stop. You can prompt Lou for the next part of the story by confirming you've arrived. Additionally, if you no longer have as much time as you used to, you can tell Lou to skip the next stop.

On the walk, a green street sign at the top of the page shows the current stop. Tap it for the case board: your briefing, the route, the clues you've earned, and your photos. To start over, press Abandon trip under the message box. After you confirm, it clears this conversation and opens a fresh page. Every reply lists the tools it used, and the `/chat` response returns them as `tool_calls`, each with its `name`, `args`, and `result`.

<img src="docs/screenshots/redesign/desktop-1-at-a-stop.png" width="720" alt="Lou on a computer, with the case board as a column">

Lastly, if your route includes a camera stop, Lou will get a screengrab of the traffic camera's view once you confirm your arrival. Lou then sends you the screengrab so you can confirm you're in it, then keep moving!

## Tools

| Group               | Tools                                                                  |
| ------------------- | ---------------------------------------------------------------------- |
| Plan evaluator      | `evaluate_adventure_plan`                                              |
| Camera souvenirs    | `find_camera_checkpoints`, `capture_camera_checkpoint`                 |
| Places and research | `geocode_place`, `find_places`, `research_place`                       |
| Routes and transit  | `get_route`, `get_transit_arrivals`, `get_next_directions`             |
| Progress            | `save_adventure_plan`, `get_adventure_state`, `update_adventure_state` |

What each tool does, where its data comes from, and how errors reach the model:

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

## How it runs

The server is FastAPI with a LiteLLM tool-calling loop (`app.py`), and the page is a single HTML file (`index.html`). It runs on Google Cloud Run, which rebuilds and redeploys on every push to `main`. Sessions are stored in Firestore and camera photos in Cloud Storage. The model is Claude Sonnet 5.5 through Anthropic's API, with the key kept in Secret Manager; if Claude can't answer, Gemini 3.5 Flash-Lite runs as a fallback model. Sonnet vs Gemini model comparison can be found here: [docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md).


## More

- [docs/TOOLS.md](docs/TOOLS.md): every tool in detail.
- [docs/STORY_DESIGN.md](docs/STORY_DESIGN.md): what makes a good adventure, and how one plays.
- [docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md): the seven models we compared.
- [docs/CONTRIBUTIONS.md](docs/CONTRIBUTIONS.md): who built what.
