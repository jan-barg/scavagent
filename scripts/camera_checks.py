"""Play grader query 2 through its camera stop and the finish, and check what must hold.

    uv run python scripts/camera_checks.py [BASE_URL] [--out transcript.json]

BASE_URL defaults to http://127.0.0.1:8000. The user asks for query 2 (which includes "a camera stop
if one fits"), starts, and moves through the stops before the camera by asking for each answer. At
the camera stop they say they are in position, then that they cannot see themselves, then that they
have reached the destination. The checks read the tool traces and replies: a camera stop in the plan,
the image-verified disclosure, a real capture shown with a question about visibility, the "not
visible" answer recorded with a retake offered, and the photo in the finale. Exit status 1 if any
check fails.
"""

import re
import sys
import uuid

from acceptance_checks import (  # scripts/ is on sys.path
    QUERY_2, arguments, chat, check, passing, ran_ok, report, state)

MAX_STOPS_BEFORE_CAMERA = 4
VERIFICATION_WORDS = re.compile(r"camera image|matched|not (?:been )?tested in person|not (?:yet )?(?:been )?field|in person", re.I)


def main():
    base, out = arguments(sys.argv[1:])
    session = str(uuid.uuid4())

    plan = chat(base, session, QUERY_2)
    good = passing(plan)
    stops = good["result"]["data"]["plan"]["stops"] if good else []
    camera = next((s for s in stops if s["activity"] == "camera_capture"), None)
    check("Plan passed and saved", good is not None and ran_ok(plan, "save_adventure_plan"))
    check("Plan includes a camera stop", camera is not None)
    if camera is None:
        notes = good["result"]["data"].get("notes", []) if good else []
        print("No camera stop; evaluator notes:", [n for n in notes if "camera" in n])
        return report(out)

    chat(base, session, "Great, let's go.")
    arrival = None
    for _ in range(MAX_STOPS_BEFORE_CAMERA):
        arrival = chat(base, session, "I'm here now.")
        current = (state(arrival) or {}).get("current_checkpoint") or {}
        if current.get("checkpoint_id") == camera["checkpoint_id"]:
            break
        chat(base, session, "I can't work out this one; tell me the answer and let's keep going.")
    at_camera = ((state(arrival) or {}).get("current_checkpoint") or {}).get("checkpoint_id") == camera["checkpoint_id"]
    check("Reached the camera stop", at_camera)
    if not at_camera:
        return report(out)
    shot = chat(base, session, "I'm in position.")
    captures = [c for c in shot["tool_calls"] if c["name"] == "capture_camera_checkpoint"]
    captured = [c for c in captures if c["result"]["ok"]]
    media = [c["result"]["data"]["photo"]["media_url"] for c in captured]
    image_verified = any(c["result"]["data"].get("verification_status") == "image_verified" for c in captured)
    check("The image-only verification is disclosed (on arrival or with the photo)",
          not image_verified or bool(VERIFICATION_WORDS.search(arrival["response"] + " " + shot["response"])))
    check("In position: one capture", len(captures) == 1)
    check("In position: the photo is shown", any(m and m in shot["response"] for m in media) if captured else
          "retake" in shot["response"].lower() or "try again" in shot["response"].lower())
    check("In position: asks whether they can see themselves",
          bool(re.search(r"see yourself|spot yourself|find yourself|can you see", shot["response"], re.I)))

    unseen = chat(base, session, "I can't see myself in it.")
    recorded = [c for c in unseen["tool_calls"] if c["name"] == "update_adventure_state" and c["result"]["ok"]
                and c["args"].get("operation") == "set_photo_visibility"]
    check("Not visible: recorded as user_reported_not_visible",
          any(c["args"].get("visibility") == "user_reported_not_visible" for c in recorded))
    check("Not visible: a retake or a nearby step is offered",
          bool(re.search(r"retake|try again|another (?:shot|photo)|step", unseen["response"], re.I)))

    done = chat(base, session, "I've reached the destination.")
    operations = [c["args"].get("operation") for c in done["tool_calls"] if c["name"] == "update_adventure_state"
                  and c["result"]["ok"]]
    check("Destination: arrival recorded, then finished", "reach_destination" in operations and "finish_adventure" in operations)
    check("Finale: the saved photo is shown again", any(m and m in done["response"] for m in media))
    return report(out)


if __name__ == "__main__":
    main()
