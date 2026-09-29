import copy

import pytest
from pydantic import ValidationError

from fixtures import SCENARIOS, load_cameras, load_scenario
from schemas import (
    Activity,
    AdventurePlan,
    AdventureState,
    CameraCheckpoint,
    ChatRequest,
    LocationContext,
    ToolResult,
    tool_error,
    tool_ok,
)


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenarios_are_valid_and_labeled_synthetic(name):
    fixture = load_scenario(name)
    assert fixture.provenance.kind == "synthetic_fixture"
    assert fixture.provenance.field_verified is False
    assert all(leg.source == "synthetic_fixture" for leg in fixture.plan.legs)


def test_camera_fixture_is_not_presented_as_field_verified():
    cameras = load_cameras()
    assert all(c.verification_status == "synthetic_fixture" for c in cameras.checkpoints)
    assert all(p.provenance == "synthetic_fixture" for p in cameras.photos)


def test_revision_preserves_completed_stops_and_reveals():
    fixture = load_scenario("revision_after_skip")
    before, after = fixture.state, fixture.revised_state
    old_plan, new_plan = fixture.plan, fixture.revised_plan

    assert new_plan.supersedes_plan_id == old_plan.plan_id
    assert set(before.completed_ids) <= set(after.completed_ids)
    assert set(before.revealed_beat_ids) <= set(after.revealed_beat_ids)
    old_stops = {c.checkpoint_id: c for c in old_plan.checkpoints}
    new_stops = {c.checkpoint_id: c for c in new_plan.checkpoints}
    for done in before.completed_ids:
        assert new_stops[done] == old_stops[done]
    # A required stop is never dropped silently.
    assert {c.checkpoint_id for c in old_plan.checkpoints if c.required_by_user} <= set(new_stops)


def test_constrained_fixture_fits_its_deadline():
    plan = load_scenario("constrained_route").plan
    travel = sum(leg.duration_minutes for leg in plan.legs)
    dwell = sum(c.dwell_minutes for c in plan.checkpoints)
    assert travel + dwell == plan.estimated_total_minutes
    elapsed = (plan.request.deadline - plan.request.start.observed_at).total_seconds() / 60
    assert plan.estimated_total_minutes + plan.contingency_minutes <= elapsed


def test_tool_results_pair_ok_with_error():
    assert tool_ok({"x": 1})["error"] is None
    failed = tool_error("NO_MATCH", "nothing", retryable=False, next_step="try another query")
    assert failed["ok"] is False and failed["error"]["code"] == "NO_MATCH"
    with pytest.raises(ValidationError):
        ToolResult(ok=True, error=failed["error"])
    with pytest.raises(ValidationError):
        ToolResult(ok=False)


def test_location_needs_a_position_and_timezone():
    with pytest.raises(ValidationError):
        LocationContext(source="user", observed_at="2026-09-28T15:00:00-04:00")
    with pytest.raises(ValidationError):
        LocationContext(source="browser", place_text="somewhere", observed_at="2026-09-28T15:00:00-04:00")
    with pytest.raises(ValidationError):  # Naive timestamps are ambiguous
        LocationContext(source="user", place_text="W 86th St", observed_at="2026-09-28T15:00:00")


def test_challenges_need_fallbacks_and_physical_tasks_need_evidence():
    base = {"type": "chat_puzzle", "prompt": "p", "answer_rule": "a", "fallback": "f"}
    Activity(**base)
    with pytest.raises(ValidationError):
        Activity(**{**base, "fallback": ""})
    with pytest.raises(ValidationError):
        Activity(**{**base, "type": "verified_feature"})


def test_plan_rejects_invented_evidence_references():
    plan = load_scenario("constrained_route").plan.model_dump(mode="json")
    bad = copy.deepcopy(plan)
    bad["checkpoints"][1]["activity"]["evidence_ids"] = ["claim_the_model_made_up"]
    with pytest.raises(ValidationError, match="unknown evidence"):
        AdventurePlan.model_validate(bad)

    bad = copy.deepcopy(plan)
    bad["legs"][0]["to_id"] = "stop_99"
    with pytest.raises(ValidationError, match="unknown stops"):
        AdventurePlan.model_validate(bad)

    bad = copy.deepcopy(plan)
    bad["places"][1]["claims"][0]["source_urls"] = []  # "According to a source" with no source
    with pytest.raises(ValidationError, match="cites no URL"):
        AdventurePlan.model_validate(bad)


def test_plan_rejects_ambiguous_stops_and_camera_stops_without_a_camera():
    plan = load_scenario("constrained_route").plan.model_dump(mode="json")
    bad = copy.deepcopy(plan)
    bad["checkpoints"].append(copy.deepcopy(bad["checkpoints"][0]))
    with pytest.raises(ValidationError, match="unique"):
        AdventurePlan.model_validate(bad)

    bad = copy.deepcopy(plan)
    next(c for c in bad["checkpoints"] if c["activity"]["type"] == "camera_capture")["camera_checkpoint_id"] = None
    with pytest.raises(ValidationError, match="without a camera checkpoint"):
        AdventurePlan.model_validate(bad)


def test_state_outcomes_are_exclusive():
    with pytest.raises(ValidationError):
        AdventureState(completed_ids=["stop_1"], skipped_ids=["stop_1"])
    with pytest.raises(ValidationError):
        AdventureState(status="active")  # No plan to be active on


def test_field_verified_camera_needs_a_verification_date():
    camera = load_cameras().checkpoints[0].model_dump(mode="json")
    with pytest.raises(ValidationError):
        CameraCheckpoint.model_validate({**camera, "verification_status": "field_verified"})


def test_chat_request_stays_compatible_with_starter_clients():
    request = ChatRequest.model_validate({"message": "hi", "session_id": None, "extra": 1})
    assert request.location is None and request.client_message_id is None
