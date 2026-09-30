# Walk test: one real adventure on a phone

One real adventure, start to finish, on the deployed site, before submission. It checks what the scripts can't: real walking times, directions that read right on the street, puzzles you can answer standing there, and the camera photo with a person in frame.

**Route:** grader query 2, which takes about 45 minutes. It runs from Central Park West & West 86th Street, past Columbus Avenue & West 81st Street, to Broadway & West 72nd Street. The camera stop is the image-verified viewpoint at Broadway & 72nd, next to the finish, so a clear photo there also upgrades that spot to `field_verified` ([CAMERA_FIELDWORK.md](CAMERA_FIELDWORK.md), "Field route").

**Roles:** one person walks and chats on their phone. The other walks along, takes notes, and at the camera watches the live still (the camera's still link) to confirm the walker is in frame.

## Before you go

- Daylight, and a charged phone with location allowed for the site.
- Open https://scavagent-b57mvtutma-ue.a.run.app in a fresh tab (a new session).
- Wear something bright for the camera.
- Have the notes table below open.

## On the walk

1. **Plan.** At CPW & 86th, send query 2 word for word: "I'm at Central Park West and West 86th Street. I have 45 minutes, need to finish at West 72nd Street and Broadway, and must pass West 81st Street and Columbus Avenue. Walking only, architecture theme, and include a camera stop if one fits."
   - Note how long it takes to answer.
   - Check that the briefing names your role and a handler with a channel, that every stop is on the way, and that the camera stop is in the plan.
2. **Ready.** Type "ready". The directions should match the street signs: street names, cross streets, and the heading.
3. **At each stop:**
   - Note the walking time against the plan's.
   - The handler should make contact and say why the place matters.
   - You should be able to answer the task from what the chat says and what you can see. It should never ask you to find an object.
   - Once in the walk, give a wrong answer, then ask for a hint: you should get a hint, not the answer.
   - A right answer should name the clue and where it points.
4. **Lock the phone** for two minutes between stops, then reopen the tab. The chat and progress should still be there.
5. **Change the plan once:** "Skip the next optional stop" or "I only have 15 minutes left". The rest should be re-timed, and the skipped stop's clue should still reach you.
6. **The camera:**
   - Stand where the instructions say. The chat should tell you the spot was matched on the camera image and has not been tested in person.
   - Type "I'm in position". Can you see yourself in the photo, and does your partner see you in the live still?
   - Answer honestly. If you're not visible, a retake or a step toward the curb should be offered.
   - Screenshot the still if you're visible: that is the evidence for `field_verified`.
7. **Finish.** At Broadway & 72nd, say you have arrived. The finale should bring the clues together, and the case file should show the photo.

## Notes

| Step | Expected | Observed | Time (planned / actual) | Issue? |
|---|---|---|---|---|
| Plan | Briefing, handler, camera stop, all stops on the way | | | |
| Directions to stop 1 | Match the street signs | | | |
| Stop 1 | Contact, theme link, answerable task, clue named | | | |
| Wrong answer and hint | Hint, not the answer | | | |
| Lock and reopen | Chat and progress intact | | | |
| Stop 2 or skip | Re-timed, clue still told | | | |
| Camera | Disclosure, photo, visible? | | | |
| Finale | Clues come together, photo shown | | | |

## After

- Put the results and any issues in [STATUS.md](STATUS.md), and open issues for anything broken.
- If the walker was visible at the camera, record the spot as `field_verified` with the screenshot (`scripts/calibrate_camera.py entry`, "field" notes; see [CAMERA_FIELDWORK.md](CAMERA_FIELDWORK.md)).
