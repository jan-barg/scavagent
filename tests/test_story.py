"""Story design v2 (docs/STORY_DESIGN.md): the draft fields, one failing draft per new check, the two
live runs that the old evaluator accepted, and the design's worked example, with routing faked.

The worked example is Jan's music-history run redone: two stops on the Upper West Side, researched
from that run's real evidence (fixtures/story_regressions/), each earning a clue the finale uses.
"""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import state
from adventure.agent_tools import evaluate_adventure_plan, save_adventure_plan
from fake_http import FakeHTTP
from integrations import research, routes
from schemas import AdventurePlan, Character, Story
from state import MemoryStore, SessionRecord, ToolContext

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
JAN_RUN = json.loads((FIXTURES / "story_regressions" / "jan_music_history_2026-09-29.json").read_text())
KYLE_RUN = json.loads((FIXTURES / "story_regressions" / "kyle_strokes_fidi_2026-09-29.json").read_text())
VALHALLA = "valhalla1.openstreetmap.de"
NOW = datetime(2026, 9, 29, 16, 6, tzinfo=timezone.utc)  # 12:06 PM in New York, as in Jan's run

BEACON, PYTHIAN, ANSONIA = "wiki:6449248", "wiki:23291465", "lpc:LP-00285"
BRIEFING = ("You're a freelance tape tracker. Mara Quill, archivist for a small record label, radios you: someone "
            "lifted the lost reel from Buddy Holly's final 1958 session and left a trail through old Upper West Side "
            "music landmarks. Each stop, message me when you're there; I'll brief you, and what you find unlocks the "
            "next lead. Two stops, about 20 minutes.")
MARA = {"name": "Mara Quill", "role": "handler", "contact": "radio", "introduced_in": "briefing"}


def worked_example():
    """docs/STORY_DESIGN.md's worked example as a draft: it must pass."""
    return {
        "kind": "new",
        "start": {"place_text": "West End Avenue & West 72nd Street", "lat": 40.7798127, "lng": -73.9844806},
        "duration_minutes": 25, "contingency_minutes": 3, "theme": "music history",
        "user_stated": ["duration_minutes", "theme"], "user_request": JAN_RUN["user_message"],
        "story": {"premise": "Someone lifted the lost reel from Buddy Holly's final 1958 session.",
                  "briefing": BRIEFING, "cast": [dict(MARA)],
                  "solution": "The thief, signing as Roxy, stashed the reel in locker 1021, and Mara's team recovers it."},
        "stops": [
            {"place_id": BEACON, "dwell_minutes": 5,
             "theme_link": {"claim_ids": [f"{BEACON}#08b3251e"],
                            "why": "A showman's 1929 movie palace is a concert hall now, and our thief took his name."},
             "activity": {"type": "chat_puzzle", "solution": "Roxy",
                          "prompt": "Mara: \"The thief signs as the nickname of the man who built this theatre. Who?\"",
                          "answer_rule": "Roxy, in any spelling.", "hints": ["He was Samuel Rothafel."],
                          "fallback": "Mara reads it off the old playbill: Roxy."},
             "beat": {"summary": "Mara: \"Roxy. Our thief knows his history.\"", "characters": ["Mara Quill"],
                      "clue": "the alias Roxy"}},
            {"place_id": PYTHIAN, "dwell_minutes": 5,
             "theme_link": {"claim_ids": [f"{PYTHIAN}#c2a33f19", f"{PYTHIAN}#257f0808"],
                            "why": "Decca cut Rock Around the Clock upstairs in 1954, and Holly's last session in 1958."},
             "activity": {"type": "chat_puzzle", "solution": "1021",
                          "prompt": "Mara: \"Roxy's locker number is the month and day of Holly's last session here.\"",
                          "answer_rule": "1021, from October 21.", "hints": ["Month, then day."],
                          "fallback": "Mara works it out: October 21, so 1021."},
             "beat": {"summary": "Mara: \"Locker 1021. That's where Roxy stashed it.\"", "characters": ["Mara Quill"],
                      "clue": "locker 1021", "uses": ["stop_1"]}},
        ],
        "chat_beats": [{"summary": "Mara: \"Locker 1021, under Roxy. We have the reel.\"", "characters": ["Mara Quill"],
                        "uses": ["stop_1", "stop_2"]}],
    }


def walking(*minutes):
    return (200, {"trip": {"legs": [{"summary": {"time": m * 60, "length": m * 0.08}, "maneuvers": [
        {"type": 1, "instruction": "Walk south on Broadway.", "street_names": ["Broadway"]}]} for m in minutes]}})


@pytest.fixture(autouse=True)
def researched():
    """What find_places and research_place returned in the live runs."""
    research._remembered.clear()
    for place in [*JAN_RUN["research_evidence"].values(), *KYLE_RUN["research_evidence"].values()]:
        research._remember(place["place_id"], name=place["name"], point=place["point"], address=place["address"],
                           evidence=place)


def session(now=NOW):
    return ToolContext(record=SessionRecord.new("s"), store=MemoryStore(), now=lambda: now)


def evaluate(plan_draft, *walk_minutes, ctx=None):
    with FakeHTTP({VALHALLA: [walking(*walk_minutes)]}):
        return evaluate_adventure_plan(ctx or session(), plan_draft)


def codes(result):
    assert result["ok"], result
    return [v["code"] for v in result["data"]["violations"]]


def messages(result):
    return " ".join(v["message"] for v in result["data"]["violations"])


def changed(change):
    plan_draft = worked_example()
    change(plan_draft)
    return plan_draft


# --- The schema ---


def test_plans_stored_before_story_v2_still_load():
    stored = json.loads((FIXTURES / "constrained_route.json").read_text())["plan"]  # no story v2 fields at all
    plan = AdventurePlan.model_validate(stored)

    assert plan.story.briefing is None and plan.story.cast == ["The Architect (fictional)", "The Rival (fictional)"]
    assert plan.story.beats[0].characters == [] and plan.story.beats[0].clue is None
    assert plan.checkpoints[0].theme_link is None and plan.checkpoints[0].activity.solution is None
    assert AdventurePlan.model_validate(plan.model_dump(mode="json")) == plan

    record = SessionRecord.new("old")
    record.plans[plan.plan_id] = plan
    reloaded = SessionRecord.model_validate_json(record.model_dump_json())
    assert reloaded.plans[plan.plan_id].story.cast[0] == "The Architect (fictional)"


def test_a_cast_mixes_old_names_and_new_characters():
    story = Story(premise="p", solution="s", cast=["Agent Vance", MARA])

    assert story.cast == ["Agent Vance", Character(**MARA)]
    assert Story.model_validate(story.model_dump(mode="json")) == story


# --- The worked example ---


def test_the_worked_example_passes_and_keeps_its_story_fields():
    ctx = session()
    result = evaluate(worked_example(), 5, 6, ctx=ctx)

    assert result["data"]["passes"], result["data"]["violations"]
    assert "briefing" in result["data"]["next_step"]
    assert save_adventure_plan(ctx, result["data"]["draft_id"])["ok"]
    plan = state.active_plan(ctx.record)
    assert plan.story.briefing == BRIEFING and plan.story.cast == [Character(**MARA)]
    stop_1, stop_2, finale = plan.story.beats
    assert (stop_1.clue, stop_2.uses, finale.uses) == ("the alias Roxy", ["beat_1"], ["beat_1", "beat_2"])
    assert plan.checkpoints[1].theme_link.claim_ids == [f"{PYTHIAN}#c2a33f19", f"{PYTHIAN}#257f0808"]
    assert plan.checkpoints[1].activity.solution == "1021"


# --- One draft per rule ---


def test_a_story_without_a_briefing_fails():
    result = evaluate(changed(lambda d: d["story"].pop("briefing")), 5, 6)

    assert codes(result) == ["MISSING_BRIEFING"]
    assert "second person" in messages(result)


def test_a_handler_needs_a_contact_channel():
    result = evaluate(changed(lambda d: d["story"]["cast"][0].pop("contact")), 5, 6)

    assert codes(result) == ["HANDLER_MISSING"]


@pytest.mark.parametrize("change, detail", [
    (lambda d: d["stops"][1]["beat"]["characters"].append("Detective Rook"), "not in story.cast"),
    (lambda d: (d["story"]["cast"].append({"name": "Detective Rook", "role": "rival", "introduced_in": "stop_2"}),
                d["stops"][0]["beat"]["characters"].append("Detective Rook"),
                d["stops"][1]["beat"]["characters"].append("Detective Rook")), "introduced only at stop_2"),
    (lambda d: (d["story"]["cast"].append({"name": "Viktor", "role": "double agent", "introduced_in": "stop_1"}),
                d["chat_beats"][0]["characters"].append("Viktor")), "that stop's beat does not list them"),
    (lambda d: d["story"].update(briefing=BRIEFING.replace("Mara Quill", "Your contact")), "never names them"),
])
def test_characters_are_introduced_before_they_act(change, detail):
    result = evaluate(changed(change), 5, 6)

    assert codes(result) == ["CAST_UNINTRODUCED"]
    assert detail in messages(result)


def test_every_cast_member_appears_in_a_beat():
    def idle_rival(d):
        d["story"]["cast"].append({"name": "Otto Blum", "role": "rival", "introduced_in": "briefing"})
        d["story"]["briefing"] += " Watch for Otto Blum, a collector who wants the reel too."

    assert codes(evaluate(changed(idle_rival), 5, 6)) == ["CAST_UNUSED"]


def test_every_stop_earns_a_clue():
    result = evaluate(changed(lambda d: d["stops"][0]["beat"].pop("clue")), 5, 6)

    assert codes(result) == ["CLUE_MISSING", "SOLUTION_UNEARNED"]  # the finale then builds on one clue, not two
    assert "stop_1's beat gives the user no clue" in messages(result)


def test_a_clue_nothing_later_uses_fails():
    def third_stop(d):  # the Ansonia's clue is never used; the finale still builds on two others
        d.pop("duration_minutes")
        d["user_stated"] = ["theme"]
        d["stops"].append({"place_id": ANSONIA, "dwell_minutes": 3,
                           "theme_link": {"claim_ids": [research.remembered_place(ANSONIA)["evidence"]["claims"][0]["claim_id"]],
                                          "why": "Musicians lived at the Ansonia; the thief rented a room."},
                           "activity": {"type": "user_observation", "prompt": "Describe the towers.",
                                        "answer_rule": "Any honest description.", "fallback": "Continue."},
                           "beat": {"summary": "Mara: \"He kept a room here.\"", "characters": ["Mara Quill"],
                                    "clue": "room 7 at the Ansonia"}})

    result = evaluate(changed(third_stop), 5, 6, 4)

    assert codes(result) == ["CLUE_UNUSED"]
    assert "List stop_3 in the uses" in messages(result)


def test_a_beat_cannot_use_a_clue_the_user_gets_later():
    result = evaluate(changed(lambda d: d["stops"][0]["beat"].update(uses=["stop_2"])), 5, 6)

    assert codes(result) == ["CLUE_OUT_OF_ORDER"]


def test_the_finale_builds_on_the_clues():
    thin = evaluate(changed(lambda d: d["chat_beats"][0].update(uses=["stop_2"])), 5, 6)
    missing = evaluate(changed(lambda d: d.pop("chat_beats")), 5, 6)

    assert codes(thin) == ["SOLUTION_UNEARNED"]
    assert "needs at least 2" in messages(thin)
    assert "SOLUTION_UNEARNED" in codes(missing) and "no finale" in messages(missing)


def test_a_code_must_come_from_a_solved_puzzle():
    def observed_not_solved(d):  # stop_2 becomes an observation whose beat simply announces a code
        d["stops"][1]["activity"] = {"type": "user_observation", "prompt": "Describe the facade.",
                                     "answer_rule": "Any honest description.", "fallback": "Continue."}
        d["stops"][1]["beat"].update(summary="Mara: \"The locker code is 1021.\"", uses=[])

    def slipped_in_chat(d):  # a chat beat that builds on nothing hands over a combination
        d["chat_beats"].insert(0, {"summary": "Mara: \"A source slipped us the safe combination.\"",
                                   "characters": ["Mara Quill"]})

    def built_from_clues(d):  # the finale builds on both solved puzzles, so it can call them a code
        d["chat_beats"][0]["summary"] = "Mara: \"Roxy's code is locker 1021. We have the reel.\""

    def answers_left_out(d):  # a live run: puzzles with clues but no solution, and a finale "vault combination"
        built_from_clues(d)
        for stop in d["stops"]:
            stop["activity"].pop("solution")

    for change, word in [(observed_not_solved, '"code"'), (slipped_in_chat, '"combination"')]:
        result = evaluate(changed(change), 5, 6)
        assert codes(result) == ["OBJECT_UNEARNED"], change.__name__
        assert word in messages(result)
    assert evaluate(changed(built_from_clues), 5, 6)["data"]["passes"]
    unsolved = evaluate(changed(answers_left_out), 5, 6)
    assert codes(unsolved) == ["OBJECT_UNEARNED"]
    assert "Give stop_1 and stop_2 each its puzzle's exact answer as activity.solution" in messages(unsolved)


def test_with_no_puzzle_at_all_the_message_quotes_the_text_to_change():
    # A live Flash-Lite draft (camera stop and observation) kept "The code to secure the stolen blueprints" in its
    # finale for ten drafts: no puzzle could earn it, and the message did not say which text held the word.
    def no_puzzles(d):
        for stop in d["stops"]:
            stop["activity"] = {"type": "user_observation", "prompt": "Describe the facade.",
                                "answer_rule": "Any honest description.", "fallback": "Continue."}
        d["chat_beats"][0]["reveals"] = "The code to secure the stolen reel."

    result = evaluate(changed(no_puzzles), 5, 6)

    assert codes(result) == ["OBJECT_UNEARNED"]
    assert "reveals mentions \"code\" (\"The code to secure the stolen reel.\")" in messages(result)
    assert "No stop in this plan is a chat_puzzle" in messages(result)


@pytest.mark.parametrize("change, field", [
    # Jan's live run (issue #32) asked for "Tom" after naming the diner's founder, Tom Glikas.
    (lambda d: d["stops"][0]["activity"].update(prompt="Samuel 'Roxy' Rothafel built this theatre. His nickname?"),
     "prompt"),
    (lambda d: d["stops"][0]["activity"].update(hints=["It rhymes with foxy: Roxy."]), "hints"),
    (lambda d: d["stops"][0]["theme_link"].update(why="Roxy Rothafel's movie palace, and our thief took his name."),
     "theme_link's why"),
])
def test_a_puzzle_that_names_its_own_answer_fails(change, field):
    result = evaluate(changed(change), 5, 6)

    assert codes(result) == ["ANSWER_IN_PROMPT"]
    assert f"in its {field}," in messages(result)


def test_ordinary_senses_of_code_and_key_are_not_objects():
    def ordinary(d):
        d["stops"][0]["beat"]["summary"] = "Mara: \"A combination of luck and Morse code. That's the key to the mystery.\""

    assert evaluate(changed(ordinary), 5, 6)["data"]["passes"]


@pytest.mark.parametrize("change, detail", [
    (lambda d: d["stops"][0].pop("theme_link"), "has no theme_link"),
    (lambda d: d["stops"][0]["theme_link"].update(claim_ids=[f"{PYTHIAN}#257f0808"]), "did not return for"),
])
def test_a_themed_stop_ties_to_the_theme_through_its_own_claims(change, detail):
    result = evaluate(changed(change), 5, 6)

    assert codes(result) == ["THEME_UNLINKED"]
    assert detail in messages(result)


def test_without_a_stated_theme_no_link_is_needed():
    def no_theme(d):
        d["user_stated"] = ["duration_minutes"]
        d.pop("theme")
        for stop in d["stops"]:
            stop.pop("theme_link")

    assert evaluate(changed(no_theme), 5, 6)["data"]["passes"]


def roxy_as_the_thief(d):  # Roxy is Samuel "Roxy" Rothafel's nickname in the Beacon Theatre's claims
    d["story"]["cast"].append({"name": "Roxy", "role": "thief", "introduced_in": "stop_1"})
    d["stops"][0]["beat"]["characters"].append("Roxy")


def the_users_idol_as_handler(d):
    d["user_request"] = "I love Taylor Swift. Something musical, please."
    d["story"]["cast"][0]["name"] = "Agent Swift"
    d["story"]["briefing"] = BRIEFING.replace("Mara Quill", "Agent Swift")
    for beat in (d["stops"][0]["beat"], d["stops"][1]["beat"], d["chat_beats"][0]):
        beat["characters"] = ["Agent Swift"]


def a_sound_alike(d):
    d["story"]["premise"] = "A tipster who sounds just like Julian Casablancas wants the reel back."


@pytest.mark.parametrize("change, detail", [
    (roxy_as_the_thief, "named in the sources"),
    (the_users_idol_as_handler, "named in the user's request"),
    (a_sound_alike, "voice or look-alike of Julian Casablancas"),
])
def test_real_people_are_history_never_characters(change, detail):
    result = evaluate(changed(change), 5, 6)

    assert codes(result) == ["REAL_PERSON_IN_FICTION"]
    assert detail in messages(result)


def test_figures_of_speech_are_not_impersonation():
    def idioms(d):
        d["stops"][0]["beat"]["summary"] = "Mara: \"It looks like Mara Quill was right. Sounds like Roxy beat us here.\""

    assert evaluate(changed(idioms), 5, 6)["data"]["passes"]


def test_enough_stops_for_the_time():
    def one_stop(d):
        d["stops"].pop()
        d["chat_beats"][0]["uses"] = ["stop_1"]
        d["story"]["solution"] = "The thief signs as Roxy."

    short = changed(one_stop)
    asked_for_one = changed(lambda d: (one_stop(d), d["user_stated"].append("stop_count")))

    result = evaluate(short, 5)
    assert codes(result) == ["TOO_FEW_STOPS"]
    assert "at least 2" in messages(result)
    assert "under 12 minutes: search find_places around Beacon Theatre (New York City) or the start" in messages(result)
    assert evaluate(asked_for_one, 5)["data"]["passes"]
    assert evaluate(short, 11)["data"]["passes"]  # an 11-minute walk there leaves no room for another stop


def test_two_hours_needs_four_stops():
    # A two-hour Strokes run on Sonnet had two stops, "thinner than Opus" (docs/MODEL_COMPARISON.md).
    two_hours = changed(lambda d: d.update(duration_minutes=120, contingency_minutes=3))

    result = evaluate(two_hours, 5, 6)

    assert codes(result) == ["TOO_FEW_STOPS"]
    assert "2 stop(s) for 120 available minutes" in messages(result) and "at least 4" in messages(result)


def test_a_clue_that_is_only_a_number_fails():
    # Live clues like "22" and "1908" were arithmetic, not something the story needed.
    for clue in ("22", "1897-1929", " 1021 "):
        result = evaluate(changed(lambda d: d["stops"][0]["beat"].update(clue=clue)), 5, 6)
        assert codes(result) == ["CLUE_BARE_NUMBER"], clue
    assert evaluate(changed(lambda d: d["stops"][0]["beat"].update(clue="locker 1021")), 5, 6)["data"]["passes"]


def test_a_time_budget_the_user_never_gave_does_not_excuse_one_stop():
    # A live run gave "a 1960s spy adventure" a 25-minute budget of its own and filled it with one far stop.
    def self_imposed(d):
        d["stops"].pop()
        d["chat_beats"][0]["uses"] = ["stop_1"]
        d["user_stated"] = ["theme"]

    result = evaluate(changed(self_imposed), 11)

    assert codes(result) == ["TOO_FEW_STOPS"]
    assert "no time limit from the user" in messages(result)


def test_a_link_rejected_before_says_so_and_offers_the_stops_source():
    built = changed(lambda d: d["stops"][0]["activity"].update(
        prompt="Read up first: https://en.wikipedia.org/wiki/Beacon_Theatre_(New_York_City). Who built it?"))
    ctx = session()

    first, second = evaluate(built, 5, 6, ctx=ctx), evaluate(built, 5, 6, ctx=ctx)

    assert codes(first) == codes(second) == ["UNSOURCED_LINK"]
    assert "rejected before" not in messages(first)
    assert messages(second).startswith("The same link was rejected before.")
    assert "https://en.wikipedia.org/w/index.php?oldid=1374986898" in messages(second)


# --- Drafts ---


def test_uses_name_stops_and_an_unknown_one_is_a_draft_error():
    by_number = evaluate(changed(lambda d: d["chat_beats"][0].update(uses=["1", "stop_2"])), 5, 6)
    one_string = evaluate(changed(lambda d: d["chat_beats"][0].update(uses="stop_1, stop_2")), 5, 6)
    unknown = evaluate(changed(lambda d: d["chat_beats"][0].update(uses=["stop_7"])), 5, 6)

    assert by_number["data"]["passes"] and one_string["data"]["passes"]
    assert unknown["error"]["code"] == "INVALID_ARGUMENT" and "stop_1, stop_2" in unknown["error"]["message"]


def test_a_cast_member_without_a_name_is_a_draft_error():
    result = evaluate(changed(lambda d: d["story"]["cast"].append({"role": "rival"})), 5, 6)

    assert result["error"]["code"] == "INVALID_ARGUMENT" and "needs a name" in result["error"]["message"]


# --- Revisions and re-timing ---


def test_a_revision_of_a_story_v2_plan_checks_the_stop_it_adds():
    ctx = session()
    save_adventure_plan(ctx, evaluate(worked_example(), 5, 6, ctx=ctx)["data"]["draft_id"], start_now=True)
    state.resolve_checkpoint(ctx, "stop_1", "completed")
    state.reveal_beat(ctx, "beat_1")
    ansonia = {"place_id": ANSONIA, "dwell_minutes": 3, "activity": {
        "type": "user_observation", "prompt": "Describe the towers.", "answer_rule": "Any.", "fallback": "Continue."},
        "beat": {"summary": "Mara: \"He kept a room here.\"", "characters": ["Mara Quill"]}}

    bare = evaluate({"kind": "revision", "stops": [ansonia]}, 4, ctx=ctx)
    ansonia["beat"]["clue"] = "room 7"
    ansonia["theme_link"] = {"claim_ids": [research.remembered_place(ANSONIA)["evidence"]["claims"][0]["claim_id"]],
                             "why": "Musicians lived at the Ansonia."}
    fixed = evaluate({"kind": "revision", "stops": [ansonia]}, 4, ctx=ctx)

    assert codes(bare) == ["CLUE_MISSING", "THEME_UNLINKED"]
    assert fixed["data"]["passes"], fixed["data"]["violations"]


def test_rechecking_a_saved_plan_does_not_apply_the_story_rules_again():
    ctx = session()
    save_adventure_plan(ctx, evaluate(worked_example(), 5, 6, ctx=ctx)["data"]["draft_id"])
    plan = state.active_plan(ctx.record)
    ctx.record.plans[plan.plan_id] = plan.model_copy(update={"story": plan.story.model_copy(update={"briefing": None})})

    result = evaluate({"kind": "check"}, ctx=ctx)

    assert result["data"]["passes"], result["data"]["violations"]


# --- Guiding: what get_adventure_state shows ---


def test_the_adventure_state_shows_the_cast_and_holds_the_finale_until_the_stops_are_done():
    ctx = session()
    save_adventure_plan(ctx, evaluate(worked_example(), 5, 6, ctx=ctx)["data"]["draft_id"], start_now=True)
    at_first_stop = state.state_summary(ctx.record)
    state.resolve_checkpoint(ctx, "stop_1", "completed")
    state.reveal_beat(ctx, "beat_1")
    state.resolve_checkpoint(ctx, "stop_2", "skipped")
    done = state.state_summary(ctx.record)

    assert at_first_stop["cast"] == [MARA] and at_first_stop["finale_if_finished"] is None
    assert at_first_stop["next_beat"]["characters"] == ["Mara Quill"]
    assert at_first_stop["current_checkpoint"]["theme_link"]["claim_ids"] == [f"{BEACON}#08b3251e"]
    assert at_first_stop["clues_to_tell_in_chat"] == []
    assert [b["clue"] for b in done["clues_to_tell_in_chat"]] == ["locker 1021"]  # from the stop they skipped
    assert done["finale_if_finished"]["uses"] == ["beat_1", "beat_2"]


# --- The live runs the old evaluator accepted ---


def test_jans_music_history_run_now_fails():
    result = evaluate(copy.deepcopy(JAN_RUN["accepted_draft"]["draft"]), 6)  # one stop, 11 of 25 minutes

    assert {"MISSING_BRIEFING", "CLUE_MISSING", "SOLUTION_UNEARNED", "TOO_FEW_STOPS"} <= set(codes(result))


def test_kyles_strokes_run_now_fails(monkeypatch):
    def subway(origin, destination, ready, types):  # the Routes API is on: long legs ride
        minutes = 30 if origin[0] > 40.75 else 18
        return {"has_transit": True, "leave_by": ready.isoformat(), "wait_minutes": 0.0, "duration_minutes": minutes,
                "distance_m": 6000, "segments": [], "fare": None, "instructions": ["Take the 6 downtown."]}

    monkeypatch.setattr(routes.transit, "transit_leg", subway)
    captured = datetime(2026, 9, 29, 16, 39, tzinfo=timezone.utc)  # 12:39 PM; the deadline is 2:38 PM
    result = evaluate(copy.deepcopy(KYLE_RUN["accepted_draft"]["draft"]), 60, 28, ctx=session(captured))

    found = set(codes(result))
    assert {"MISSING_BRIEFING", "CLUE_MISSING", "SOLUTION_UNEARNED", "TOO_FEW_STOPS",
            "REAL_PERSON_IN_FICTION", "OBJECT_UNEARNED"} <= found
    assert "Julian Casablancas" in messages(result)
    assert result["data"]["plan"]["legs"][0]["modes"] == ["walk", "transit"]


def test_the_worked_examples_theme_links_quote_jans_real_research():
    claims = {c["claim_id"]: c["text"] for place in JAN_RUN["research_evidence"].values() for c in place["claims"]}

    assert "Roxy" in claims[f"{BEACON}#08b3251e"]
    assert "October 21, 1958" in claims[f"{PYTHIAN}#257f0808"]
