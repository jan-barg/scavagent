# Camera fieldwork: calibrating standing positions

A camera checkpoint is offered only once a standing position is verified, in one of two ways:

- **`image_verified`** (the main route since September 29): a teammate finds a public sidewalk spot in a live still, matches it to satellite or Street View imagery, and writes the coordinates and instructions. Nobody has stood there, so the agent tells visitors so and offers a retake.
- **`field_verified`**: someone stood at the spot and saw themselves in the still (the field protocol below). A field visit upgrades an image-verified spot.

`scripts/calibrate_camera.py` turns either kind of notes into a catalogue entry.

## Image route: the camera spot workbench

1. All online Manhattan street cameras were pulled on September 29, 2026 (about 3:20 PM, 308 stills) into `agent-handoffs/camera-evidence/` outside Git, and screened from the stills: 259 show a usable pedestrian area (`keep` or `maybe`), 49 do not (tunnels, bridge roadways, highways, a camera aimed at the sky). The screening notes are model judgments from the images; the person marking a spot decides.
2. The private workbench page (https://claude.ai/artifact/CiTKGvwL2hYBNcsiJVEAoc) shows each kept camera's still. Marking a spot: drag or tap where a standing person appears, paste the Google Maps coordinates of that spot, and write the address, landmark, side of street, positioning instructions and what the view shows. Spots save to the page's database.
3. Claude exports the saved spots (`ArtifactData list spots` with `out_dir`) and imports them:

```bash
uv run python -m scripts.calibrate_camera import-spots SPOTS_DIR --evidence EVIDENCE_DIR --write
```

`EVIDENCE_DIR` is the `camera-evidence` folder in Jan's `Agents/agent-handoffs/` (the workbench's `still_file` paths are relative to it). `import-spots` gives each spot a checkpoint id such as `img_central_park_west_at_86_st_a`, dates it by the still's retrieval time, records the still's SHA-256 and the workbench spot id in `field_log`, skips spots already imported, and refuses incomplete spots rather than filling them in.

## Field route

### What the candidate cameras show (checked September 29, 2026, about 1:30 PM)

All six cameras below were online and returned a 352×240 JPEG with the local time burned into the top of the frame. The frame changes about every 2 seconds, so a still fetched after "ready" is at most a couple of seconds old. People on the nearer sidewalks appear roughly 10–25 pixels tall: recognizable by clothing and position, not by face.

Tap a link to see the camera's current still; reload the page to get a new one.

| # | Camera (DOT name) | What the view shows | Still |
|---|---|---|---|
| 1 | Central Park West @ 86 St | Faces west along W 86th St across CPW. The corner sidewalk under the building at the left edge, the crosswalk over 86th, M86 buses turning | [still](https://webcams.nyctmc.org/api/cameras/8a6bc417-4877-4ebe-8052-88c1b261baf1/image) |
| 2 | Amsterdam @ 72 St | Broadway at W 72nd St: the old subway control house on the right, the shops' sidewalk on the left, busy crosswalks. Grader query 2 ends here | [still](https://webcams.nyctmc.org/api/cameras/0bcfbc92-d455-4f62-846a-32afbefa3b4b/image) |
| 3 | Central Park West @ 77 St | Faces south on CPW. The west sidewalk in front of the New-York Historical Society's steps, park wall on the left | [still](https://webcams.nyctmc.org/api/cameras/90c74649-458d-4b5f-8f58-9ab77f03f4ad/image) |
| 4 | Central Park West @ 72 St | Inside the park at the W 72nd St entrance: the drive with pedicabs and the paths along both sides, many people | [still](https://webcams.nyctmc.org/api/cameras/984ebbad-ca64-41d8-8008-63aaae316952/image) |
| 5 | Amsterdam @ 86 St | Faces south on Amsterdam. East sidewalk (dry cleaner) on the left, crosswalk at the bottom | [still](https://webcams.nyctmc.org/api/cameras/0b0cbcfb-7c14-4b94-8bac-be494472c396/image) |
| 6 | Amsterdam Ave @ 79 St | Faces along Amsterdam at W 79th St, sidewalks on both sides | [still](https://webcams.nyctmc.org/api/cameras/fb31c502-9b4a-4c19-9b26-5b84a1a39ec3/image) |

1, 4 and 5 are the candidates already in `data/camera_catalogue.json`. 2 and 3 were found on September 29 and sit on grader query 2's corridor (CPW/86 → Columbus/81 → Broadway/72). The descriptions come from the stills alone; directions and building names need confirming on site.

**Suggested walk (about 1.6 km):** 86 St (B/C) station → #1 at CPW/86 → south along CPW → #3 at 77th → #4 at 72nd → west on 72nd → #2 at Broadway (72 St 1/2/3 station). Add #5 or #6 if time allows. Daylight only.

### At each camera

1. **Pick a spot on the sidewalk** that looks visible in the still. Never the roadway, a bike lane, a crosswalk, or private steps: participants will stand there for half a minute.
2. **Check you are in frame.** Stand still, open the camera's still link, reload, and find yourself. Wearing something bright helps; raise an arm if unsure. A partner can instead watch the still (or run `watch`, below) and text you.
3. **Screenshot the still** in which you are visible. This is the evidence. The time overlay in the frame dates it.
4. **Record the position.** In Google Maps, long-press exactly where you stand (satellite view helps) and copy the coordinates it shows. If you use the blue GPS dot instead, note its accuracy. Never use the camera's own coordinates.
5. **Try 2–3 spots** if the first is small, blocked by buses or trees, or awkward to stand in. Keep the best one (or more than one, each as its own entry).
6. Optional: a phone photo of the spot, taken from where a participant arrives, for reference.

**Fallback:** if no sidewalk spot shows you clearly (camera repositioned, view blocked, you are a few pixels), note what you saw and move on to the next camera. A camera without a still showing you stays disabled.

### What to send back, per spot

```text
Camera: #1 CPW @ 86 St
Spot, in words a stranger could follow: e.g. SW corner of CPW and W 86th, on the sidewalk beside the signal pole
Coordinates: 40.xxxxx, -73.xxxxx (Maps pin / GPS ±__ m)
Side of street, and which way to face:
Landmark a participant can find:
Time on the overlay while you stood there, and what you wore:
Visible? How big and clear:
Problems (buses, shade, crowds, glare):
Screenshot(s): attached
```

### Turning notes into catalogue entries

Run from the repository root. Stills are saved under `.data/calibration/<camera id>/` (gitignored).

```bash
uv run python -m scripts.calibrate_camera still candidate_cpw_86                  # save the current still
uv run python -m scripts.calibrate_camera watch candidate_cpw_86 --minutes 30     # save every new frame during a visit
uv run python -m scripts.calibrate_camera template candidate_cpw_86 > notes.json  # empty notes form
uv run python -m scripts.calibrate_camera entry notes.json                        # check and print the entry
uv run python -m scripts.calibrate_camera entry notes.json --write                # add it to data/camera_catalogue.json
```

`entry` refuses notes without the participant's own confirmation that they were visible, without an existing evidence still or screenshot, without a verification time, or with coordinates that equal the camera mount or lie more than 250 m from it. It writes the entry as `field_verified` and `enabled`, and records the evidence files' SHA-256 hashes in the catalogue's `field_log` (the images themselves stay out of Git).
