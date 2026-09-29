"""The plan evaluator against Jan's labeled fixtures, each test breaking one rule."""

from datetime import timedelta

from adventure.validation import evaluate_plan
from fixtures import load_cameras, load_scenario
from schemas import AdventurePlan, AdventureState

CAMERAS = {c.checkpoint_id: c for c in load_cameras().checkpoints}
BASE = load_scenario("constrained_route")  # deadline 15:45; 27 min of legs, 13 of dwell, 5 of contingency
DEADLINE = BASE.request.deadline
ON_TIME = DEADLINE - timedelta(minutes=50)


def evaluate(plan=None, *, start_at=ON_TIME, allow_synthetic=True, **kwargs):
    return evaluate_plan(plan or BASE.plan, now=start_at, start_at=start_at, camera_lookup=CAMERAS.get,
                         allow_synthetic=allow_synthetic, **kwargs)


def edit(plan, change):
    """A modified copy of a plan: `change` edits its JSON form in place."""
    data = plan.model_dump(mode="json")
    change(data)
    return AdventurePlan.model_validate(data)


def codes(evaluation):
    return [v.code for v in evaluation.report.violations]


def checkpoint(data, checkpoint_id):
    return next(c for c in data["checkpoints"] if c["checkpoint_id"] == checkpoint_id)


# --- A plan that fits ---


def test_the_fixture_plan_passes_when_it_fits_the_deadline():
    evaluation = evaluate()

    assert evaluation.report.ok, codes(evaluation)
    assert evaluation.report.estimated_total_minutes == 40.0  # 27 travel + 13 dwell, contingency apart
    assert evaluation.slack_minutes == 5.0
    assert [row["checkpoint_id"] for row in evaluation.timeline] == ["stop_1", "stop_2", "stop_3"]
    assert evaluation.timeline[0]["arrive_at"] == (ON_TIME + timedelta(minutes=8)).isoformat()


# --- Time ---


def test_a_late_start_misses_the_deadline_by_a_stated_amount_and_suggests_a_cut():
    evaluation = evaluate(start_at=DEADLINE - timedelta(minutes=40))

    assert codes(evaluation) == ["DEADLINE_EXCEEDED"]
    assert "runs 5 minutes past" in evaluation.report.violations[0].message
    assert evaluation.suggestions[0].startswith("Dropping optional stop_")


def test_the_stated_total_must_match_and_contingency_must_cover_ten_percent():
    def understate(data):
        data["estimated_total_minutes"] = 30
        data["contingency_minutes"] = 2

    evaluation = evaluate(edit(BASE.plan, understate))

    assert set(codes(evaluation)) == {"ESTIMATE_MISMATCH", "CONTINGENCY_TOO_SMALL"}


def test_a_missed_train_is_stale_and_an_early_arrival_waits_for_the_timed_departure():
    def ride_to_camera(minutes_after_start):
        def change(data):
            leg = next(leg for leg in data["legs"] if leg["leg_id"] == "leg_3")
            leg["actual_modes"] = ["walk", "transit"]
            leg["depart_at"] = (ON_TIME + timedelta(minutes=minutes_after_start)).isoformat()
            data["request"]["allowed_modes"] = ["walk", "transit"]
        return edit(BASE.plan, change)

    # The plan is ready to leave stop_2 at +22 minutes (8 + 5 dwell + 5 + 4 dwell).
    missed = evaluate(ride_to_camera(10))
    waited = evaluate(ride_to_camera(26))

    assert "STALE_ROUTE" in codes(missed)
    assert "ESTIMATE_MISMATCH" in codes(waited)  # the 4-minute wait makes the route 44 minutes, not 40
    assert waited.report.estimated_total_minutes == 44.0


def test_transit_looked_up_long_ago_must_be_refreshed():
    def old_ride(data):
        leg = data["legs"][0]
        leg["actual_modes"] = ["walk", "transit"]
        leg["retrieved_at"] = (ON_TIME - timedelta(hours=3)).isoformat()
        data["request"]["allowed_modes"] = ["walk", "transit"]

    assert "STALE_ROUTE" in codes(evaluate(edit(BASE.plan, old_ride)))


# --- The request ---


def test_a_mode_the_user_did_not_allow_is_flagged():
    def ride(data):
        data["legs"][1]["actual_modes"] = ["walk", "transit"]  # the request allows walking only

    assert codes(evaluate(edit(BASE.plan, ride))) == ["MODE_NOT_ALLOWED"]


def test_required_stop_missing_and_too_short_a_dwell():
    def unrequire(data):
        checkpoint(data, "stop_1")["required_by_user"] = False

    def rush(data):
        checkpoint(data, "stop_1")["dwell_minutes"] = 2
        data["estimated_total_minutes"] = 37

    assert "REQUIRED_STOP_MISSING" in codes(evaluate(edit(BASE.plan, unrequire)))
    assert codes(evaluate(edit(BASE.plan, rush))) == ["DWELL_TOO_SHORT"]


def test_a_required_stop_outside_its_window_is_flagged():
    def close_early(data):
        data["request"]["required_stops"][0]["window_end"] = (ON_TIME + timedelta(minutes=10)).isoformat()

    evaluation = evaluate(edit(BASE.plan, close_early))

    assert codes(evaluation) == ["WINDOW_MISSED"]
    assert evaluation.report.violations[0].checkpoint_id == "stop_1"


def test_a_missing_leg_is_a_route_gap():
    def drop_leg(data):
        data["legs"] = [leg for leg in data["legs"] if leg["leg_id"] != "leg_2"]

    evaluation = evaluate(edit(BASE.plan, drop_leg))

    assert "ROUTE_GAP" in codes(evaluation)
    assert "No route leg from stop_1 to stop_2" in evaluation.report.violations[0].message


def test_synthetic_routes_and_cameras_are_rejected_in_a_live_plan():
    evaluation = evaluate(allow_synthetic=False)

    assert codes(evaluation).count("SYNTHETIC_DATA") == 4
    assert "CAMERA_UNAVAILABLE" in codes(evaluation)


# --- Activities ---


def test_a_physical_task_needs_physical_feature_evidence_from_its_own_place():
    def historical(data):
        data["places"][1]["claims"][0]["kind"] = "historical"

    def elsewhere(data):
        claim = data["places"][1]["claims"].pop(0)
        data["places"][0]["claims"].append(claim)

    assert codes(evaluate(edit(BASE.plan, historical))) == ["UNSUPPORTED_PHYSICAL_TASK"]
    assert "EVIDENCE_ELSEWHERE" in codes(evaluate(edit(BASE.plan, elsewhere)))


def test_physical_requirements_without_evidence_are_flagged_even_on_an_observation():
    def plaque(data):
        checkpoint(data, "stop_1")["activity"]["physical_requirements"] = ["the bronze plaque by the door"]

    assert codes(evaluate(edit(BASE.plan, plaque))) == ["UNSUPPORTED_PHYSICAL_TASK"]


def test_a_puzzle_needs_hints():
    def puzzle(data):
        activity = checkpoint(data, "stop_2")["activity"]
        activity.update(type="chat_puzzle", evidence_ids=[], physical_requirements=[], hints=[])

    assert codes(evaluate(edit(BASE.plan, puzzle))) == ["MISSING_HINTS"]


def test_a_camera_stop_must_be_near_its_standing_position():
    def move_camera_stop(data):
        place = next(p for p in data["places"] if p["place_id"] == "fixture_place_camera")
        place["point"] = {"lat": 40.7700, "lng": -73.9800}  # about a kilometer south

    assert "CAMERA_ELSEWHERE" in codes(evaluate(edit(BASE.plan, move_camera_stop)))


# --- Story ---


def test_story_beats_must_be_tied_to_the_stop_that_reveals_them_and_used_once():
    def reuse(data):
        checkpoint(data, "stop_3")["story_beat_id"] = "beat_2"

    def untie(data):
        checkpoint(data, "stop_2")["story_beat_id"] = None

    reused = codes(evaluate(edit(BASE.plan, reuse)))
    assert "BEAT_REUSED" in reused and "BEAT_MISMATCH" in reused
    assert codes(evaluate(edit(BASE.plan, untie))) == ["STOP_WITHOUT_STORY"]


# --- Revisions ---

REVISION = load_scenario("revision_after_skip")  # stop_1 done, beat_1 revealed, 15 minutes left


def evaluate_revision(plan=None, *, state=None, **kwargs):
    start = REVISION.revised_plan.request.deadline - timedelta(minutes=15)
    return evaluate_plan(plan or REVISION.revised_plan, now=start, previous=REVISION.plan,
                         state=state or REVISION.state, camera_lookup=CAMERAS.get, allow_synthetic=True, **kwargs)


def test_the_fixture_revision_passes():
    evaluation = evaluate_revision()

    assert evaluation.report.ok, codes(evaluation)
    assert [row["checkpoint_id"] for row in evaluation.timeline] == ["stop_4"]  # stop_1 is already done
    assert evaluation.notes[0].startswith("stop_4")  # an unresearched corner: fiction and observation only


def test_a_revision_keeps_completed_stops_and_what_the_user_was_told():
    def change_completed(data):
        checkpoint(data, "stop_1")["dwell_minutes"] = 9

    def drop_revealed(data):
        data["story"]["beats"] = [b for b in data["story"]["beats"] if b["beat_id"] != "beat_1"]
        checkpoint(data, "stop_1")["story_beat_id"] = None

    def rewrite_revealed(data):
        data["story"]["beats"][0]["summary"] = "A different clue than the one the user heard."

    assert "COMPLETED_CHANGED" in codes(evaluate_revision(edit(REVISION.revised_plan, change_completed)))
    assert "REVEALED_BEAT_DROPPED" in codes(evaluate_revision(edit(REVISION.revised_plan, drop_revealed)))
    assert "REVEALED_BEAT_CHANGED" in codes(evaluate_revision(edit(REVISION.revised_plan, rewrite_revealed)))


def test_a_revision_must_supersede_the_active_plan():
    def orphan(data):
        data["supersedes_plan_id"] = "some_other_plan"

    assert "REVISION_LINEAGE" in codes(evaluate_revision(edit(REVISION.revised_plan, orphan)))


def test_an_unresolved_required_stop_survives_unless_the_user_waives_it():
    nothing_done = AdventureState(status="active", active_plan_id=REVISION.plan.plan_id, current_checkpoint_id="stop_1")

    def without_stop_1(data):
        data["checkpoints"] = [c for c in data["checkpoints"] if c["checkpoint_id"] != "stop_1"]
        data["story"]["beats"] = [b for b in data["story"]["beats"] if b["beat_id"] != "beat_1"]
        data["places"] = [p for p in data["places"] if p["place_id"] != "fixture_place_required"]
        data["request"]["required_stops"] = []

    dropped = edit(REVISION.revised_plan, without_stop_1)
    assert "REQUIRED_STOP_DROPPED" in codes(evaluate_revision(dropped, state=nothing_done))
    assert "REQUIRED_STOP_DROPPED" not in codes(evaluate_revision(dropped, state=nothing_done, waived_required_ids=["stop_1"]))


def test_a_clue_left_at_a_skipped_stop_is_stranded():
    skipped = REVISION.state.model_copy(update={"skipped_ids": ["stop_2"]})

    def keep_stop_2(data):
        original = REVISION.plan.model_dump(mode="json")
        data["checkpoints"].insert(1, checkpoint(original, "stop_2"))
        data["places"].append(next(p for p in original["places"] if p["place_id"] == "fixture_place_facade"))
        data["story"]["beats"][1]["checkpoint_id"] = "stop_4"
        data["story"]["beats"].append({"beat_id": "beat_9", "checkpoint_id": "stop_2", "summary": "A clue", "reveals": "x"})
        checkpoint(data, "stop_2")["story_beat_id"] = "beat_9"

    assert "CLUE_STRANDED" in codes(evaluate_revision(edit(REVISION.revised_plan, keep_stop_2), state=skipped))


def test_a_new_plan_ignores_progress_from_an_earlier_adventure():
    earlier = AdventureState(completed_ids=["stop_1", "stop_2"])  # another plan's stops with the same ids

    evaluation = evaluate(state=earlier)

    assert [row["checkpoint_id"] for row in evaluation.timeline] == ["stop_1", "stop_2", "stop_3"]


def test_an_object_the_user_must_find_at_a_real_place_is_flagged_unless_evidenced():
    def chalk(data):
        checkpoint(data, "stop_1")["activity"]["prompt"] = "A contact left a chalk mark on the brickwork. Describe it."

    def found(data):
        data["story"]["beats"][2]["summary"] = "You spot the microfilm tucked behind the lamppost."

    def observe(data):
        checkpoint(data, "stop_1")["activity"]["prompt"] = "Describe one detail of the stonework; it is your recognition sign."

    assert codes(evaluate(edit(BASE.plan, chalk))) == ["INVENTED_PROP"]
    assert codes(evaluate(edit(BASE.plan, found))) == ["INVENTED_PROP"]
    assert evaluate(edit(BASE.plan, observe)).report.ok


def test_a_stop_marked_required_must_be_at_a_place_the_user_required():
    def overclaim(data):
        checkpoint(data, "stop_2")["required_by_user"] = True

    assert codes(evaluate(edit(BASE.plan, overclaim))) == ["REQUIRED_MISMARKED"]


def test_a_beat_where_someone_hands_the_user_something_is_flagged():
    def handoff(data):
        data["story"]["beats"][0]["summary"] = "At the corner, a hidden dispatch is handed to you by an anonymous source."

    assert codes(evaluate(edit(BASE.plan, handoff))) == ["INVENTED_PROP"]


def test_a_real_architect_from_the_sources_cannot_join_the_plot():
    lpc_claim = {"claim_id": "fixture_claim_lpc", "kind": "architectural", "basis": "source",
                 "text": "The LPC building database lists 1 Fixture Street: architect/builder Margon & Holder and Emery Roth; "
                         "primary style Art Deco; date 1929 - 1931.",
                 "source_urls": ["https://data.cityofnewyork.us/d/gpmc-yuvp"], "checked_at": ON_TIME.isoformat(),
                 "uncertainty": None}

    def plot(data):
        data["places"][1]["claims"].append(lpc_claim)
        data["story"]["beats"][1]["summary"] = "Emery Roth's ghost signals from the tower."

    def fact_only(data):
        data["places"][1]["claims"].append(lpc_claim)  # stating the fact elsewhere is fine

    assert codes(evaluate(edit(BASE.plan, plot))) == ["REAL_PERSON_IN_FICTION"]
    assert evaluate(edit(BASE.plan, fact_only)).report.ok
