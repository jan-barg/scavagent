"""evaluate_adventure_plan and save_adventure_plan against a memory store, with routing faked."""

from datetime import datetime, timedelta, timezone

import pytest

import state
from adventure import agent_tools
from adventure.agent_tools import evaluate_adventure_plan, save_adventure_plan
from fake_http import FakeHTTP
from integrations import research
from schemas import Claim, LatLng, PlaceEvidence
from state import MemoryStore, SessionRecord, ToolContext

VALHALLA = "valhalla1.openstreetmap.de"
NOW = datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc)  # 3:00 PM in New York
START = {"place_text": "Central Park West & West 86th Street", "lat": 40.7853, "lng": -73.9693}


def evidence(place_id, name, lat, lng, *claims):
    return PlaceEvidence(
        place_id=place_id, name=name, point=LatLng(lat=lat, lng=lng), checked_at=NOW,
        claims=[Claim(claim_id=f"{place_id}#{i}", text=text, kind=kind, basis="source",
                      source_urls=["https://en.wikipedia.org/w/index.php?oldid=1"], checked_at=NOW)
                for i, (kind, text) in enumerate(claims)],
    ).model_dump(mode="json")


EL_DORADO = evidence("wiki:9238071", "The El Dorado", 40.78833, -73.9675,
                     ("architectural", "It was designed by Margon & Holder with Emery Roth in the Art Deco style."))
BERESFORD = evidence("wiki:5667636", "The Beresford", 40.7825, -73.97194,
                     ("historical", "The Beresford replaced the Hotel Beresford in 1929."))


def walking(*minutes):
    return (200, {"trip": {"legs": [{"summary": {"time": m * 60, "length": m * 0.08}, "maneuvers": [
        {"type": 1, "instruction": "Walk north on Central Park West.", "street_names": ["Central Park West"]}]}
        for m in minutes]}})


def activity(**changes):
    return {"type": "chat_puzzle", "prompt": "A coded telegram waits for you here: CPW-4-2.",
            "answer_rule": "Accept any answer naming the fourth building.", "hints": ["Count from the corner."],
            "fallback": "Reveal the answer and continue.", **changes}


def draft(**changes):
    return {
        "kind": "new", "start": START, "allowed_modes": ["walk"], "user_stated": ["allowed_modes"],
        "story": {"premise": "It is 1964. A courier has vanished.", "cast": ["The Courier"], "solution": "The doorman did it."},
        "stops": [
            {"place_id": "wiki:9238071", "dwell_minutes": 5, "activity": activity(),
             "beat": {"summary": "A torn ticket stub.", "reveals": "The courier took the A train."}},
            {"place_id": "wiki:5667636", "dwell_minutes": 5, "activity": activity(),
             "beat": {"summary": "A matchbook.", "reveals": "The doorman smokes."}},
        ],
        "chat_beats": [{"summary": "The finale.", "reveals": "The doorman did it."}],
        **changes,
    }


@pytest.fixture(autouse=True)
def known_places():
    agent_tools.reset_for_tests()
    research._remembered.clear()
    for place in (EL_DORADO, BERESFORD):
        research._remember(place["place_id"], name=place["name"], point=place["point"], address=None, evidence=place)


def session(session_id="s1"):
    return ToolContext(record=SessionRecord.new(session_id), store=MemoryStore(), now=lambda: NOW)


def evaluate(ctx, plan_draft, *legs):
    with FakeHTTP({VALHALLA: [walking(*legs)]}):
        return evaluate_adventure_plan(ctx, plan_draft)


# --- New plans ---


def test_a_new_draft_is_routed_checked_and_saved_as_proposed():
    ctx = session()
    result = evaluate(ctx, draft(), 5, 9)

    assert result["ok"], result
    data = result["data"]
    assert data["passes"], data["violations"]
    assert [s["name"] for s in data["plan"]["stops"]] == ["The El Dorado", "The Beresford"]
    assert data["estimated_total_minutes"] == 24.0  # 5 + 9 minutes of walking and two 5-minute stops
    assert data["timeline"][0]["arrive_local"] == "3:05 PM"

    saved = save_adventure_plan(ctx, data["draft_id"])
    assert saved["ok"] and saved["data"]["status"] == "proposed"
    plan = state.active_plan(ctx.record)
    assert plan.validation.ok and plan.request.defaulted_fields == ["theme"]
    assert [b.checkpoint_id for b in plan.story.beats] == ["stop_1", "stop_2", None]


def test_start_now_activates_and_a_draft_from_another_conversation_is_refused():
    ctx, other = session("s1"), session("s2")
    draft_id = evaluate(ctx, draft(), 5, 9)["data"]["draft_id"]

    assert save_adventure_plan(other, draft_id)["error"]["code"] == "INVALID_ARGUMENT"
    assert save_adventure_plan(ctx, "plan_nonexistent")["error"]["code"] == "INVALID_ARGUMENT"
    assert save_adventure_plan(ctx, draft_id, start_now=True)["data"]["status"] == "active"


def test_a_failing_draft_reports_violations_and_cannot_be_saved():
    ctx = session()
    tight = draft(deadline="2026-10-01T15:20:00-04:00", user_stated=["deadline", "allowed_modes"])
    result = evaluate(ctx, tight, 5, 9)

    data = result["data"]
    assert not data["passes"]
    assert [v["code"] for v in data["violations"]] == ["DEADLINE_EXCEEDED"]
    assert data["suggestions"] and "Do not present" in data["next_step"]
    assert save_adventure_plan(ctx, data["draft_id"])["error"]["code"] == "PLAN_INFEASIBLE"


def test_a_physical_task_citing_a_history_claim_fails():
    ctx = session()
    physical = draft()
    physical["stops"][1]["activity"] = activity(type="verified_feature", evidence_ids=["wiki:5667636#0"],
                                                physical_requirements=["the 1929 cornerstone"])
    result = evaluate(ctx, physical, 5, 9)

    assert [v["code"] for v in result["data"]["violations"]] == ["UNSUPPORTED_PHYSICAL_TASK"]


@pytest.mark.parametrize("change, message", [
    (lambda d: d["stops"][0].pop("place_id"), "Each stop needs"),
    (lambda d: d.pop("story"), "story needs a premise"),
    (lambda d: d["stops"][0]["activity"].pop("fallback"), "Stop stop_1 is invalid"),
    (lambda d: d["stops"][0]["activity"].update(evidence_ids=["wiki:9238071#made-up"]), "unknown evidence"),
    (lambda d: d.update(deadline="2026-10-01T14:00:00-04:00"), "already passed"),
])
def test_a_draft_that_cannot_become_a_plan_says_what_to_fix(change, message):
    bad = draft()
    change(bad)
    result = evaluate(session(), bad, 5, 9)

    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert message in result["error"]["message"]


def test_a_new_plan_while_one_is_under_way_needs_the_user_to_end_it_first():
    ctx = session()
    save_adventure_plan(ctx, evaluate(ctx, draft(), 5, 9)["data"]["draft_id"], start_now=True)

    second = evaluate(ctx, draft(), 5, 9)["data"]["draft_id"]
    assert "abandon" in save_adventure_plan(ctx, second)["error"]["next_step"]


# --- An adventure under way ---


def started_and_first_stop_done():
    ctx = session()
    save_adventure_plan(ctx, evaluate(ctx, draft(), 5, 9)["data"]["draft_id"], start_now=True)
    state.resolve_checkpoint(ctx, "stop_1", "completed")
    state.reveal_beat(ctx, "beat_1")
    return ctx


def test_a_revision_keeps_the_finished_stop_and_moves_the_dropped_stops_clue_into_chat():
    ctx = started_and_first_stop_done()
    old = state.active_plan(ctx.record)
    corner = {"place": {"name": "West 81st Street & Columbus Avenue", "lat": 40.78326, "lng": -73.97455},
              "dwell_minutes": 3, "activity": activity(), "beat": {"summary": "A footprint.", "reveals": "Size 11."}}
    result = evaluate(ctx, {"kind": "revision", "stops": [corner], "deadline": "2026-10-01T15:30:00-04:00"}, 7)

    data = result["data"]
    assert data["passes"], data["violations"]
    assert "no sourced claim" in data["notes"][0]  # the new corner carries fiction only
    assert save_adventure_plan(ctx, data["draft_id"])["ok"]
    plan = state.active_plan(ctx.record)
    assert plan.supersedes_plan_id == old.plan_id and plan.version == 2
    assert [c.checkpoint_id for c in plan.checkpoints] == ["stop_1", "stop_3"]
    beats = {b.beat_id: b.checkpoint_id for b in plan.story.beats}
    assert beats["beat_1"] == "stop_1" and beats["beat_2"] is None  # the Beresford's clue now comes in chat
    assert plan.legs[0].from_id == "current_location"


def test_only_a_pending_required_stop_can_be_waived():
    ctx = started_and_first_stop_done()
    result = evaluate(ctx, {"kind": "revision", "stops": [{"keep": "stop_2"}], "waived_required_ids": ["stop_2"]}, 9)

    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "cannot be waived" in result["error"]["message"]


def test_check_retimes_the_adventure_under_way_against_a_new_deadline():
    ctx = started_and_first_stop_done()
    result = evaluate_adventure_plan(ctx, {"kind": "check", "deadline": "2026-10-01T15:08:00-04:00"})

    data = result["data"]
    assert data["from"] == "stop_1"
    assert [v["code"] for v in data["violations"]] == ["DEADLINE_EXCEEDED"]
    assert data["timeline"][0]["checkpoint_id"] == "stop_2"
    assert state.active_plan(ctx.record).request.deadline is None  # checking changes nothing


def test_a_revision_can_change_the_destination_and_add_a_required_stop():
    ctx = started_and_first_stop_done()
    pharmacy = {"place": {"name": "Pharmacy at Broadway & 84th", "lat": 40.7865, "lng": -73.9780},
                "required_by_user": True, "dwell_minutes": 5,
                "activity": activity(type="user_observation", hints=[]), "move_beat_id": "beat_2"}
    end = {"place_text": "Broadway & West 86th Street", "lat": 40.7883, "lng": -73.9765}
    result = evaluate(ctx, {"kind": "revision", "stops": [pharmacy], "destination": end}, 6, 3)

    assert result["data"]["passes"], result["data"]["violations"]
    save_adventure_plan(ctx, result["data"]["draft_id"])
    plan = state.active_plan(ctx.record)
    assert plan.request.destination.place_text == "Broadway & West 86th Street"
    assert [s.place.place_text for s in plan.request.required_stops] == ["Pharmacy at Broadway & 84th"]
    assert [leg.to_id for leg in plan.legs] == ["stop_3", "destination"]


def test_a_budget_the_agent_chose_is_recorded_as_a_default():
    ctx = session()
    save_adventure_plan(ctx, evaluate(ctx, draft(duration_minutes=40), 5, 9)["data"]["draft_id"])

    assert state.active_plan(ctx.record).request.defaulted_fields == ["duration_minutes", "theme"]


def test_a_stop_without_an_activity_gets_a_plain_instruction():
    bare = draft()
    bare["stops"][0].pop("activity")
    result = evaluate(session(), bare, 5, 9)

    assert "needs an activity with type, prompt, answer_rule, and fallback" in result["error"]["message"]


# --- Directions ---

GOOGLE = "routes.googleapis.com"


def test_directions_use_the_planned_walking_leg():
    ctx = session()
    save_adventure_plan(ctx, evaluate(ctx, draft(), 5, 9)["data"]["draft_id"], start_now=True)
    with FakeHTTP({}) as http:
        result = agent_tools.get_next_directions(ctx)

    data = result["data"]
    assert (data["to_id"], data["name"], data["minutes"], data["refreshed"]) == ("stop_1", "The El Dorado", 5.0, False)
    assert data["instructions"] == ["Walk north on Central Park West."]
    assert http.calls == []


def test_directions_start_from_the_user_when_they_have_wandered_off():
    from schemas import LocationContext

    ctx = session()
    save_adventure_plan(ctx, evaluate(ctx, draft(), 5, 9)["data"]["draft_id"], start_now=True)
    away = LocationContext(point=LatLng(lat=40.7790, lng=-73.9740), source="browser", observed_at=NOW, accuracy_m=15)
    state.update_location(ctx.record, away)
    with FakeHTTP({VALHALLA: [walking(12)]}) as http:
        result = agent_tools.get_next_directions(ctx)

    assert result["data"]["refreshed"] and result["data"]["minutes"] == 12.0
    route_request = http.calls[0]["json"]["locations"][0]
    assert (route_request["lat"], route_request["lon"]) == (40.7790, -73.9740)


def test_stale_transit_directions_are_looked_up_again(monkeypatch):
    from fake_http import google_transit_response
    from integrations import transit

    transit.reset_for_tests()
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    ctx = session()
    save_adventure_plan(ctx, evaluate(ctx, draft(allowed_modes=["walk", "transit"]), 5, 9)["data"]["draft_id"],
                        start_now=True)
    # Pretend the first leg was a subway ride looked up half an hour ago; Google needs a current time.
    later = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=5)
    ctx.now = lambda: later
    plan = state.active_plan(ctx.record)
    old_ride = plan.legs[0].model_copy(update={"actual_modes": ["walk", "transit"],
                                               "retrieved_at": later - timedelta(minutes=30)})
    ctx.record.plans[plan.plan_id] = plan.model_copy(update={"legs": [old_ride, *plan.legs[1:]]})
    answers = {VALHALLA: [walking(40)], GOOGLE: [(200, google_transit_response(later))]}
    with FakeHTTP(answers):
        result = agent_tools.get_next_directions(ctx)

    data = result["data"]
    assert data["refreshed"] and data["modes"] == ["walk", "transit"]
    assert any("Take the A train" in line for line in data["instructions"])
