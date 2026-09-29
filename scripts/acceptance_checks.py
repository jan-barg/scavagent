"""Run the README's three grader queries against a running Scavagent and check what must hold.

    uv run python scripts/acceptance_checks.py [BASE_URL] [--out transcript.json]

BASE_URL defaults to http://127.0.0.1:8000 (start the app with `uv run app.py`). The checks use the
live model and live data, so wording varies; they test invariants: the right tools ran, a plan
passed the evaluator and was saved, the user's limits held, nothing was waived for the user, and the
saved story follows docs/STORY_DESIGN.md (a briefing, characters introduced before they act, every
stop's clue used later, and theme links for a themed request). Exit status 1 if any check fails.
"""

import json
import math
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

QUERY_1 = "I'm at Central Park West and West 86th Street. Give me a 1960s spy adventure."
QUERY_2 = ("I'm at Central Park West and West 86th Street. I have 45 minutes, need to finish at West 72nd Street and "
           "Broadway, and must pass West 81st Street and Columbus Avenue. Walking only, architecture theme, and include "
           "a camera stop if one fits.")
QUERY_3 = "Skip the next optional stop. I have only 15 minutes left, and I still need to reach my destination."
COLUMBUS_81 = (40.78326, -73.97455)

results, transcript = [], []


def main():
    args = sys.argv[1:]
    out = None
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    base = (args[0] if args else "http://127.0.0.1:8000").rstrip("/")

    first = chat(base, str(uuid.uuid4()), QUERY_1)
    check("Q1 answered without a model error", bool(first["response"]) and "Model call failed" not in first["response"])
    check("Q1 resolved the start with geocode_place", ran_ok(first, "geocode_place"))
    check("Q1 researched at least one place", ran_ok(first, "research_place"))
    check("Q1 plan passed evaluate_adventure_plan", passing(first) is not None)
    check("Q1 plan saved", ran_ok(first, "save_adventure_plan"))
    if passing(first):
        check_story("Q1", passing(first), themed=True)  # "a 1960s spy adventure" states a theme

    session = str(uuid.uuid4())
    asked_at = datetime.now(timezone.utc)
    second = chat(base, session, QUERY_2)
    good = passing(second)
    check("Q2 geocoded start, required stop, and destination", count_ok(second, "geocode_place") >= 3)
    check("Q2 plan passed evaluate_adventure_plan", good is not None)
    if good:
        data, draft = good["result"]["data"], good["args"].get("draft", good["args"])
        check("Q2 walks every leg (walking only)", all(leg["modes"] == ["walk"] for leg in data["plan"]["legs"]))
        required = [s for s in draft.get("required_stops") or [] if near(s, COLUMBUS_81)]
        check("Q2 required corner given to the evaluator", bool(required))
        check("Q2 adds a themed stop beyond the required corner", any(not s["required"] for s in data["plan"]["stops"]))
        finish = datetime.fromisoformat(data["finish_at"])
        check("Q2 finishes with contingency inside 45 minutes",
              finish + timedelta(minutes=data["contingency_minutes"]) <= asked_at + timedelta(minutes=46))
        check_story("Q2", good, themed=True)
    check("Q2 looked for a camera stop", any(c["name"] == "find_camera_checkpoints" for c in second["tool_calls"]))
    no_camera = all(not c["result"]["ok"] for c in second["tool_calls"] if c["name"] == "find_camera_checkpoints")
    check("Q2 says when no camera stop fits", not no_camera or "camera" in second["response"].lower())
    check("Q2 plan saved", ran_ok(second, "save_adventure_plan"))

    # Reach the first stop and answer it, so the follow-up happens mid-adventure.
    chat(base, session, "Great, let's go.")
    chat(base, session, "I'm here now.")
    chat(base, session, "I see tall stone buildings with carved trim around the windows and a big cornice. I can't "
                        "work out the puzzle, though; tell me the answer and let's keep going.")
    asked_at = datetime.now(timezone.utc)
    third = chat(base, session, QUERY_3)
    drafts = [c["args"].get("draft", c["args"]) for c in third["tool_calls"] if c["name"] == "evaluate_adventure_plan"]
    deadlines = [parse(d.get("deadline")) or (asked_at + timedelta(minutes=float(d["duration_minutes"]))
                                             if d.get("duration_minutes") else None) for d in drafts]
    rechecked = any(d and abs((d - asked_at).total_seconds() / 60 - 15) <= 3 for d in deadlines)
    only_destination_left = any(c["name"] == "get_next_directions" and c["result"]["ok"]
                                and c["result"]["data"].get("to_id") == "destination" for c in third["tool_calls"])
    check("Q3 timed the rest against 15 minutes", rechecked or (only_destination_left and "15" in third["response"]))
    waived = [c for c in third["tool_calls"] if c["name"] == "update_adventure_state" and c["args"].get("user_waived_required")]
    check("Q3 did not waive a required stop on the user's behalf", not waived)
    check("Q3 answered", bool(third["response"]) and "Model call failed" not in third["response"])
    finished = [c for c in third["tool_calls"] if c["name"] == "update_adventure_state"
                and c["args"].get("operation") == "finish_adventure"]
    check("Q3 did not finish before the user reached the destination", not finished)

    width = max(len(name) for name, _ in results)
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}")
    if out:
        with open(out, "w") as f:
            json.dump(transcript, f, indent=2)
        print(f"transcript: {out}")
    sys.exit(0 if all(ok for _, ok in results) else 1)


def chat(base, session, message):
    reply = requests.post(f"{base}/chat", timeout=300, json={
        "message": message, "session_id": session, "client_message_id": uuid.uuid4().hex}).json()
    transcript.append({"session_id": session, "message": message, "reply": reply})
    print(f"> {message[:70]}\n  {len(reply.get('tool_calls', []))} tool calls: "
          f"{', '.join(c['name'] for c in reply.get('tool_calls', []))}")
    return reply


def check(name, ok):
    results.append((name, bool(ok)))


def check_story(label, call, themed):
    """docs/STORY_DESIGN.md, checked on the draft of the plan that passed."""
    draft = call["args"].get("draft", call["args"])
    story, stops = draft.get("story") or {}, draft.get("stops") or []
    briefing = story.get("briefing") or ""
    # The ids the draft builder gives: stops in order, beats numbered over stops with a beat, then chat beats.
    beats, number = [], 0
    for i, stop in enumerate(stops, 1):
        if stop.get("beat"):
            number += 1
            beats.append((f"stop_{i}", f"beat_{number}", stop["beat"]))
    for beat in draft.get("chat_beats") or []:
        number += 1
        beats.append(("chat", f"beat_{number}", beat))
    order = [f"stop_{i}" for i in range(1, len(stops) + 1)]
    cast = {c["name"].lower(): c for c in story.get("cast") or [] if isinstance(c, dict) and c.get("name")}

    def introduced(name, where):
        member = cast.get(name.lower()) or next((c for n, c in cast.items() if name.lower() in n.split()), None)
        if member is None:
            return False
        at = member.get("introduced_in") or "briefing"
        if at == "briefing":
            return any(word.lower() in briefing.lower() for word in member["name"].split() if len(word) >= 3)
        return at in order and (where == "chat" or order.index(at) <= order.index(where))

    used = {ref for _, _, beat in beats for ref in beat.get("uses") or []}
    check(f"{label} story opens with a briefing", len(briefing.strip()) >= 200)
    check(f"{label} introduces every character before they act",
          all(introduced(name, where) for where, _, beat in beats for name in beat.get("characters") or []))
    check(f"{label} uses every stop's clue later", all(
        beat.get("clue") and (where in used or beat_id in used) for where, beat_id, beat in beats if where != "chat"))
    if themed:
        check(f"{label} ties each chosen stop to the theme", all(
            (stop.get("theme_link") or {}).get("claim_ids") for stop in stops
            if not stop.get("required_by_user") and not stop.get("camera_checkpoint_id") and not stop.get("keep")))


def ran_ok(reply, tool):
    return any(c["name"] == tool and c["result"]["ok"] for c in reply["tool_calls"])


def count_ok(reply, tool):
    return sum(1 for c in reply["tool_calls"] if c["name"] == tool and c["result"]["ok"])


def passing(reply):
    return next((c for c in reversed(reply["tool_calls"]) if c["name"] == "evaluate_adventure_plan"
                 and c["result"]["ok"] and c["result"]["data"].get("passes")), None)


def near(spot, point, meters=150):
    try:
        lat, lng = float(spot["lat"]), float(spot["lng"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.dist((lat * 111_320, lng * 84_390), (point[0] * 111_320, point[1] * 84_390)) <= meters


def parse(value):
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        from zoneinfo import ZoneInfo

        moment = moment.replace(tzinfo=ZoneInfo("America/New_York"))
    return moment


if __name__ == "__main__":
    main()
