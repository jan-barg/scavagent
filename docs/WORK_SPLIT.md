# Work split and development phases

Team: Jan Barganowski and Kyle Coletta. Due October 7, 2026. This is the working allocation; the humans can reassign it. Product details are in [PLAN.md](PLAN.md), shared interfaces in [CONTRACTS.md](CONTRACTS.md), and actual progress in [STATUS.md](STATUS.md).


### Ownership

Give each person a coherent part of the experience that includes both backend behavior and a substantive original tool. These assignments can be swapped if the team's preferences differ.

| Area | Jan: conversation, progress, and cameras | Kyle: research, planning, and adaptation |
|---|---|---|
| User-facing behavior | Chat presentation, foreground location, check-ins, media, final photo collection | Preference interpretation, route proposal, story, challenges, hints, revised plans |
| Backend ownership | HTTP/session handling, durable state operations, asset storage, deployment | Agent reasoning flow, evidence handling, place selection, plan evaluation |
| Integrations | Curated camera finder and camera capture | Maps/routing, web research, MTA, filming lookup with freshness handling |
| Original tool | `find_camera_checkpoints` | `evaluate_adventure_plan` |
| Validation responsibility | Session separation, reload/resume, location fallbacks, photos remaining correct later | Time feasibility, physical evidence, clue consistency, skipped/blocked stops |

Both teammates perform the camera fieldwork and walk-test the integrated experience. Jan leads deployment and setup documentation; Kyle leads grader examples and tool documentation. Both review the full submission and can explain the other person's main design choices.

Suggested file boundaries, to agree before coding: Jan owns `app.py`, the frontend, state/storage modules, camera integration, camera catalogue, and deployment configuration. Kyle owns agent behavior/prompts, research/routing/transit/filming integrations, and adventure validation. Shared schemas and the tool registry have one agreed maintainer; propose Jan as integration owner, with both reviewing interface changes. This describes future organization and does not imply those modules already exist.

### The shared contract

Spend approximately an hour together agreeing on the information each side passes to the other. A short schema file and one example of each record are enough to start:

- `AdventureRequest`: starting place and optional constraints/preferences, with defaults distinguished from user instructions.
- `AdventurePlan`: stable stop IDs, ordered legs, estimated times, source references, challenge payloads, narrative beats, and camera checkpoint references.
- `AdventureState`: plan version, current/completed/skipped stops, revealed clues, user choices, latest location, and saved image references.
- `CameraCheckpoint`: standing location, positioning instructions, reference-view information, verification status, and camera ID.
- Tool results: a common success/error structure with actionable errors and source/freshness metadata when relevant.

Kyle's planning logic proposes a plan or a revision. Jan's state layer validates and persists permitted changes. The backend supplies the session identity. Neither side independently maintains a second copy of progress. Photos travel through the plan as checkpoint/asset IDs rather than raw image data.

Maintain three small development examples: a short plan with only a start, a constrained route with a required stop and destination, and a revision after a skip. These let each person work before the other's integration is ready. Temporary fixtures must be clearly labeled and replaced with real tool results before the corresponding milestone is accepted.

### Phase 1 — Shared foundation and a complete small example, Sep 28–29

**Together:** agree on the contracts, choose a pilot area, check routing/search credentials, and define a short example conversation from request to ending. Keep the starter's current model configurable while the final model choice remains open.

**Jan:** deploy the starter to Cloud Run with GitHub continuous deployment, preserve the required `/chat` shape, implement session restoration/persistence, and display a retrieved camera image in the chat. Calibrate the first usable pedestrian camera position with Kyle.

**Kyle:** connect the agent to the agreed state structure, implement the basic progression through a tiny development hunt, and prove that research and routing services return usable data. Use labeled fixture stop content only while the real planner is being built.

**Exit condition:** in the deployed app, a user can start the small example, type a check-in, capture a camera still when physically positioned, continue, finish, and reload without losing progress. A remote developer can test progression with clearly labeled fixtures; this is not proof of physical presence. The goal is to connect all major components immediately.

### Phase 2 — Dynamic planning and grounded stories, Sep 30–Oct 1

**Jan:** build the calibrated camera catalogue and original checkpoint finder; aim for at least three usable viewpoints before expanding. Complete image storage and retrieval so the finale uses the actual saved stills. Support the plan's message types in the chat.

**Kyle:** implement candidate discovery, source-backed research, travel/dwell budgeting, stop ordering, story generation, challenge fallbacks, and the original plan evaluator. Required stops and the endpoint are constraints; time and theme remain optional inputs. Permit only plans that pass the evaluator to become active.

**Together:** integrate the finder into planning and compare two different themes from the same starting location. Ensure the agent actually uses tool results to decide what to include.

**Exit condition:** real external data produces a customized, feasible adventure with sourced facts, usable activities, and a camera stop when one fits. A user can start with location alone. The product path no longer depends on a fixed test hunt.

### Phase 3 — Running and changing real adventures, Oct 2–3

**Jan:** add permitted foreground location updates, freshness metadata, manual-location fallback, and reliable lock/unlock and reload recovery. Finish validated state changes, duplicate-message protection, photo retries, and the final case file. Location updates do not invoke the model.

**Kyle:** implement hints, flexible answer handling, skips, closed locations, changed deadlines, required-stop changes, and shortened endings. Preserve revealed clues while replacing remaining stops. Add MTA arrivals and refresh upcoming transit legs. Add filming lookup only with explicit date/coverage handling; stale permit data cannot establish a current set.

**Together:** run a complete live-data adventure in the deployed app and interrupt it with “skip this,” “I only have 15 minutes,” and a camera failure.

**Exit condition:** the full adventure remains coherent and useful after those changes. The next route fits the updated constraints, or the agent honestly explains why the constraints cannot all be met. Completed stops and photos survive replanning.

### Phase 4 — Field testing and stabilization, Oct 4–5

Both walk several generated hunts in the pilot area. Trade roles: each person tests the other's work as a user. Check actual travel and activity time, readable directions, camera positions, clue quality, and phone lock/unlock. Adjust the catalogue and planning assumptions based on observations.

Also test remotely: start-only input; denied GPS; two separate browser sessions; reload and app restart; an unavailable API; a stale camera frame; an impossible deadline; an optional stop declined; and a required stop that is unavailable. Verify a later failure does not erase earlier tool-call traces. Check that fictional claims never become purported historical evidence.

**Exit condition:** core grader examples and an actual outdoor adventure pass, no user-session leakage or lost progress remains, and each major unavailable-data case has a useful recovery. Prioritize fixes over adding more stops or integrations.

### Phase 5 — Optional soundtrack, only after Phase 4 passes

Spotify is the final feature addition. It may fit late on Oct 5 if the core is stable; otherwise leave it for after submission. Do not let it consume the release buffer.

Start with the smallest version: select tracks to fit the adventure's theme and approximate length, resolve them through a working Spotify integration, create the playlist under the chosen authorized account, and put the link in chat. The account-ownership and login approach still need an implementation decision and an access test. Keep playlist creation on demand and prevent duplicate playlists when a message is retried.

**Suggested split:** Kyle chooses soundtrack content and exposes the creation capability to the agent; Jan handles the authorized-account connection, stored playlist reference, and link presentation. The person with working developer access should lead the API spike. A Spotify failure returns an optional-feature error and leaves the adventure intact. Avoid player controls or precise music-to-location synchronization in this version.

**Exit condition:** a real playlist link works for the intended user and the whole adventure still works when Spotify is unavailable or unused.

### Phase 6 — Release and submission, Oct 6–7

Freeze features on Oct 6. Jan checks deployment, environment configuration, session persistence, and setup instructions from a fresh clone. Kyle finalizes the three grader queries, tool documentation, and originality explanations. Together verify every required root file, the `/chat` trace format, the actual deployment URL, and both Columbia UNIs/emails in `submission.json`.

On Oct 7, run the deployed grader examples again and submit the GitHub URL. Keep the deployed app available until grades are released. The release phase remains necessary even if Spotify is omitted.

### Collaboration routine and priorities

Use short feature branches and small pull requests into `main`; the other person reviews the interface and user-visible behavior. Merge a working increment daily, then exercise it in the deployed app. Keep incomplete integrations disabled until they work. Each day, spend roughly 10–15 minutes showing what now works and naming any dependency the other person needs to resolve.

Schema changes require agreement before merging. Do not both rewrite the shared entrypoint, registry, or data structures independently. When one person needs the other's unfinished output, use a fixture that matches the agreed schema and make its temporary status visible.

If time becomes tight, protect the full request-to-ending experience, real research/routing, two original tools, persistent chat, camera capture, replanning, and deployment. Reduce camera coverage and optional integrations first. Current filming discovery cannot be promised from the stale dataset. Spotify remains last. Phone calls, automatic recognition, ride booking, multi-phone synchronization, and continuous background tracking are outside this release proposal. Bathroom help, 311 context, public spaces, meeting in the middle, and more elaborate postcards remain valued follow-ups.
