"""Walk one adventure through the turns where guiding goes wrong, and check what must hold.

    uv run python scripts/guiding_checks.py [BASE_URL] [--out transcript.json]

BASE_URL defaults to http://127.0.0.1:8000. After planning a themed walk, the user arrives, asks for
a hint, guesses wrong, answers right, reaches the next stop, skips it, and says only 15 minutes are
left. The checks read the tool traces: no answer given away in a hint or after a wrong guess, no
checkpoint completed before the user answers it, at most one resolved per message, a skip recorded
as a skip with its clue still told, and the time left re-checked. The answer and clue come from
get_adventure_state, which the instructions have the model call every turn. Exit status 1 if any
check fails.
"""

import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

from acceptance_checks import chat, check, passing, ran_ok, results, transcript  # scripts/ is on sys.path

PLAN = "I'm at West End Avenue and West 72nd Street. I have 45 minutes for a music history mystery."
RESOLVING = ("complete_checkpoint", "skip_checkpoint", "block_checkpoint")


def main():
    args = sys.argv[1:]
    out = None
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    base = (args[0] if args else "http://127.0.0.1:8000").rstrip("/")
    session = str(uuid.uuid4())

    plan = chat(base, session, PLAN)
    check("Plan passed and saved", passing(plan) is not None and ran_ok(plan, "save_adventure_plan"))

    begin = chat(base, session, "Ready, let's go.")
    check("Start: the adventure starts and nothing is resolved", updates(begin, "start_adventure") and not resolved(begin))

    arrive = chat(base, session, "I'm here.")
    first = state(arrive) or state(begin)
    stop = (first or {}).get("current_checkpoint") or {}
    activity = stop.get("activity") or {}
    answer = activity.get("solution") if activity.get("type") == "chat_puzzle" else None
    beat = (first or {}).get("next_beat") or {}
    check("Arrival: arriving is not an answer", not resolved(arrive))

    hint = chat(base, session, "Can I get a hint?")
    check("Hint: nothing is resolved", not resolved(hint))
    check("Hint: the answer is not given away", not mentions(hint["response"], answer))

    if answer:  # an observation accepts any honest description, so a wrong guess only exists for a puzzle
        wrong = chat(base, session, "My guess: banana.")
        check("Wrong answer: nothing is resolved", not resolved(wrong))
        check("Wrong answer: the answer is not given away", not mentions(wrong["response"], answer))

    right = chat(base, session, f"I think it's {answer}." if answer else "I see a carved stone doorway and tall arched windows.")
    completed = [c["args"].get("checkpoint_id") for c in updates(right, "complete_checkpoint")]
    check("Right answer: exactly the current stop is completed", completed == [stop.get("checkpoint_id")])
    check("Right answer: its beat is revealed", bool(updates(right, "reveal_beat")))
    check("Right answer: the clue is named", mentions_clue(right["response"], beat.get("clue"), answer))
    check("Right answer: directions to the next stop", ran_ok(right, "get_next_directions"))

    arrive_next = chat(base, session, "I'm at the next stop.")
    second = state(arrive_next) or state(right)
    stop_2 = (second or {}).get("current_checkpoint") or {}
    beat_2 = (second or {}).get("next_beat") or {}
    check("Next arrival: arriving is not an answer", not resolved(arrive_next))

    skip = chat(base, session, "This one's too hard. I'd like to skip it.")
    skipped = [c["args"].get("checkpoint_id") for c in updates(skip, "skip_checkpoint")]
    check("Skip: recorded as a skip of the current stop", skipped == [stop_2.get("checkpoint_id")])
    check("Skip: nothing is completed", not updates(skip, "complete_checkpoint"))
    check("Skip: its clue still reaches the user", not beat_2.get("clue") or bool(updates(skip, "reveal_beat"))
          or mentions_clue(skip["response"], beat_2.get("clue"), None))

    asked_at = datetime.now(timezone.utc)
    short = chat(base, session, "Actually, I only have 15 minutes left.")
    check("15 minutes: the rest is re-timed against it", retimed(short, asked_at))
    check("15 minutes: nothing is resolved or finished on the user's behalf",
          not resolved(short) and not updates(short, "finish_adventure"))

    check("At most one checkpoint resolved per message",
          all(len(resolved(entry["reply"])) <= 1 for entry in transcript if entry["session_id"] == session))

    width = max(len(name) for name, _ in results)
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}")
    if out:
        with open(out, "w") as f:
            json.dump(transcript, f, indent=2)
        print(f"transcript: {out}")
    sys.exit(0 if all(ok for _, ok in results) else 1)


def updates(reply, operation):
    """Successful update_adventure_state calls with this operation."""
    return [c for c in reply["tool_calls"] if c["name"] == "update_adventure_state" and c["result"]["ok"]
            and c["args"].get("operation") == operation]


def resolved(reply):
    return [c for operation in RESOLVING for c in updates(reply, operation)]


def state(reply):
    """The last get_adventure_state result in the reply, if the model called it."""
    found = [c["result"]["data"] for c in reply["tool_calls"] if c["name"] == "get_adventure_state" and c["result"]["ok"]]
    return found[-1] if found else None


def mentions(text, answer):
    """Whether the answer appears in the text as whole words, ignoring case."""
    if not answer:
        return False
    return re.search(rf"(?<![\w]){re.escape(answer.strip())}(?![\w])", text or "", re.I) is not None


def mentions_clue(text, clue, answer):
    """Whether the reply names the clue: its answer, or most of its words."""
    if mentions(text, answer):
        return True
    words = [w for w in re.findall(r"[A-Za-z0-9']+", clue or "") if len(w) >= 3]
    return bool(words) and sum(mentions(text, w) for w in words) >= max(1, len(words) // 2)


def retimed(reply, asked_at):
    """An evaluate_adventure_plan call timed against about 15 minutes from now."""
    for call in reply["tool_calls"]:
        if call["name"] != "evaluate_adventure_plan":
            continue
        draft = call["args"].get("draft", call["args"])
        minutes = draft.get("duration_minutes") or call["args"].get("duration_minutes")
        if minutes is not None and abs(float(str(minutes).split()[0]) - 15) <= 3:
            return True
        deadline = draft.get("deadline")
        if deadline:
            moment = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                from zoneinfo import ZoneInfo

                moment = moment.replace(tzinfo=ZoneInfo("America/New_York"))
            if abs((moment - asked_at) - timedelta(minutes=15)) <= timedelta(minutes=3):
                return True
    return False


if __name__ == "__main__":
    main()
