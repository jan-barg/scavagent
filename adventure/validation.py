"""The adventure plan evaluator, Scavagent's original planning tool (evaluate_adventure_plan).

It rebuilds a plan's timeline from its route legs and dwell times, then checks the plan against
the user's request and the product rules in docs/PLAN.md:

- time: the finish, plus contingency, against a deadline or time budget; enough contingency; the
  stated total (travel, waits, and dwell, without contingency) matching the computed one;
- the request: every required stop present, in the user's order, inside its time window, with at
  least the dwell the user needs; only the travel modes the user allows;
- route data: legs that connect the visiting order, transit timings that are neither stale nor
  already missed, no synthetic fixture data in a live plan;
- activities: a physical task only with physical-feature evidence from its own place, hints for a
  puzzle, a camera stop only at an enabled, field-verified position near the stop;
- story: every optional stop moves the story, beats tied to the right stop and used once, no clue
  stranded at a skipped stop;
- a revision: the plan it replaces, completed stops unchanged, revealed beats kept, unresolved
  required stops kept unless the user waived them.

It returns a schemas.ValidationReport of concrete violations and the timeline behind them. It
checks arithmetic and references; it cannot certify that a quoted source is true.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from schemas import (
    NYC_TIMEZONE,
    AdventurePlan,
    AdventureState,
    CameraCheckpoint,
    Checkpoint,
    ValidationReport,
    Violation,
)

SAME_PLACE_M = 150  # a required stop and a checkpoint this close are the same place
TRANSIT_STALE_MINUTES = 60  # re-route transit legs fetched longer ago than this
MISSED_DEPARTURE_GRACE_MINUTES = 2
ESTIMATE_TOLERANCE_MINUTES = 2
MIN_CONTINGENCY_MINUTES = 2
CONTINGENCY_SHARE = 0.10  # with a deadline or budget, keep at least 10% of travel and dwell in reserve

CameraLookup = Callable[[str], CameraCheckpoint | None]


@dataclass
class Evaluation:
    """The report, plus the numbers behind it for the model and the user."""

    report: ValidationReport
    timeline: list[dict] = field(default_factory=list)
    finish_at: datetime | None = None
    travel_minutes: float = 0.0
    dwell_minutes: float = 0.0
    contingency_minutes: float = 0.0
    slack_minutes: float | None = None
    suggestions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        local = ZoneInfo(NYC_TIMEZONE)
        return {
            "ok": self.report.ok,
            "violations": [v.model_dump(mode="json") for v in self.report.violations],
            "estimated_total_minutes": self.report.estimated_total_minutes,
            "travel_minutes": round(self.travel_minutes, 1),
            "dwell_minutes": round(self.dwell_minutes, 1),
            "contingency_minutes": self.contingency_minutes,
            "finish_at": self.finish_at.isoformat() if self.finish_at else None,
            "finish_local": self.finish_at.astimezone(local).strftime("%-I:%M %p") if self.finish_at else None,
            "slack_minutes": None if self.slack_minutes is None else round(self.slack_minutes, 1),
            "timeline": self.timeline,
            "suggestions": self.suggestions,
            "notes": self.notes,
        }


def evaluate_plan(
    plan: AdventurePlan,
    *,
    now: datetime,
    state: AdventureState | None = None,
    previous: AdventurePlan | None = None,
    start_at: datetime | None = None,
    waived_required_ids: list[str] | tuple = (),
    camera_lookup: CameraLookup | None = None,
    allow_synthetic: bool = False,
) -> Evaluation:
    """Evaluate a new plan, or with `previous` and `state`, a revision of the active one.

    `start_at` is when the remaining route begins: by default the first leg's departure for a
    new plan and `now` for a revision. `waived_required_ids` are required checkpoints the user
    explicitly dropped. `camera_lookup` returns the catalogue record for a camera checkpoint id.
    `allow_synthetic` accepts fixture routes and cameras (development only).
    """
    violations: list[Violation] = []

    def flag(code, message, checkpoint_id=None):
        violations.append(Violation(code=code, message=message, checkpoint_id=checkpoint_id))

    request = plan.request
    # Progress belongs to the plan being revised; a new plan starts clean even if ids repeat.
    state = state if previous is not None else None
    resolved = set()
    if state is not None:
        resolved = set(state.completed_ids) | set(state.skipped_ids) | set(state.blocked_ids)
    remaining = [c for c in plan.checkpoints if c.checkpoint_id not in resolved]
    places = {p.place_id: p for p in plan.places}
    beats = {b.beat_id: b for b in plan.story.beats}
    local = ZoneInfo(NYC_TIMEZONE)

    # --- Route: the legs must connect the visiting order ---
    origin = "current_location" if previous is not None else "start"
    order = [origin, *(c.checkpoint_id for c in remaining)]
    if request.destination is not None:
        order.append("destination")
    if len(order) < 2:
        flag("EMPTY_ROUTE", "The plan has no stop or destination left to go to.")
    pairs = list(zip(order, order[1:]))
    legs_by_pair = {}
    for leg in plan.legs:
        if (leg.from_id, leg.to_id) in legs_by_pair:
            flag("ROUTE_GAP", f"Two legs connect {leg.from_id} to {leg.to_id}.")
        legs_by_pair[(leg.from_id, leg.to_id)] = leg
    for leg in plan.legs:
        if (leg.from_id, leg.to_id) not in pairs:
            flag("ROUTE_GAP", f"Leg {leg.leg_id} ({leg.from_id} -> {leg.to_id}) is not part of the visiting order "
                              f"{' -> '.join(order)}.")
    for a, b in pairs:
        if (a, b) not in legs_by_pair:
            flag("ROUTE_GAP", f"No route leg from {a} to {b}; route that leg before evaluating.")

    # --- Timeline ---
    first_leg = legs_by_pair.get(pairs[0]) if pairs else None
    if start_at is None:
        start_at = now if previous is not None or first_leg is None or first_leg.depart_at is None else first_leg.depart_at
    allowed_modes = set(request.allowed_modes) | ({"walk"} if "transit" in request.allowed_modes else set())
    by_id = {c.checkpoint_id: c for c in remaining}
    clock, travel, dwell, timeline = start_at, 0.0, 0.0, []
    for a, b in pairs:
        leg = legs_by_pair.get((a, b))
        if leg is not None:
            rides = "transit" in leg.actual_modes
            if not set(leg.actual_modes) <= allowed_modes:
                flag("MODE_NOT_ALLOWED", f"Leg {leg.leg_id} uses {leg.actual_modes}, but the user allows only "
                                         f"{sorted(request.allowed_modes)}.", b if b in by_id else None)
            if leg.source == "synthetic_fixture" and not allow_synthetic:
                flag("SYNTHETIC_DATA", f"Leg {leg.leg_id} is a synthetic fixture, not a real route.")
            if rides and leg.retrieved_at < now - timedelta(minutes=TRANSIT_STALE_MINUTES):
                flag("STALE_ROUTE", f"Transit leg {leg.leg_id} was looked up at {_clock(leg.retrieved_at)}; "
                                    "refresh it before relying on its times.")
            if rides and leg.depart_at is not None:
                if clock > leg.depart_at + timedelta(minutes=MISSED_DEPARTURE_GRACE_MINUTES):
                    flag("STALE_ROUTE", f"Transit leg {leg.leg_id} was timed to leave at {_clock(leg.depart_at)}, "
                                        f"but the plan reaches {a} and is ready only at {_clock(clock)}; re-route it "
                                        "from that time.")
                clock = max(clock, leg.depart_at)  # early: wait for the timed departure
            clock += timedelta(minutes=leg.duration_minutes)
            travel += leg.duration_minutes
        checkpoint = by_id.get(b)
        if checkpoint is None:
            continue
        arrive = clock
        clock += timedelta(minutes=checkpoint.dwell_minutes)
        dwell += checkpoint.dwell_minutes
        timeline.append({"checkpoint_id": b, "arrive_at": arrive.isoformat(), "leave_at": clock.isoformat(),
                         "arrive_local": arrive.astimezone(local).strftime("%-I:%M %p")})
    finish = clock
    contingency = plan.contingency_minutes
    total = (finish - start_at).total_seconds() / 60  # travel, waits, and dwell; contingency is separate

    # --- Time budget ---
    slack = None
    if request.deadline is not None:
        slack = (request.deadline - finish).total_seconds() / 60 - contingency
        if slack < 0:
            flag("DEADLINE_EXCEEDED", f"The route finishes at {_clock(finish)} and, with {contingency:g} minutes of "
                                      f"contingency, runs {-slack:.0f} minutes past the {_clock(request.deadline)} deadline.")
    elif request.duration_minutes is not None:
        began = start_at
        if previous is not None:
            began = next((leg.depart_at for leg in previous.legs if leg.from_id == "start" and leg.depart_at),
                         previous.created_at)
        used = (finish - began).total_seconds() / 60 + contingency
        slack = request.duration_minutes - used
        if slack < 0:
            flag("DURATION_EXCEEDED", f"The adventure takes {used:.0f} minutes with contingency, {-slack:.0f} more "
                                      f"than the user's {request.duration_minutes}.")
    if request.deadline is not None or request.duration_minutes is not None:
        needed = max(MIN_CONTINGENCY_MINUTES, math.ceil(CONTINGENCY_SHARE * (travel + dwell)))
        if contingency < needed:
            flag("CONTINGENCY_TOO_SMALL", f"With a time limit, keep at least {needed} minutes of contingency "
                                          f"(the plan keeps {contingency:g}).")
    if abs(plan.estimated_total_minutes - total) > ESTIMATE_TOLERANCE_MINUTES:
        flag("ESTIMATE_MISMATCH", f"The plan states {plan.estimated_total_minutes:g} minutes, but its legs, waits, and "
                                  f"dwell add up to {total:.0f} (contingency counted separately).")

    # --- The user's required stops ---
    required = [c for c in plan.checkpoints if c.required_by_user]
    positions = {c.checkpoint_id: i for i, c in enumerate(plan.checkpoints)}
    arrivals = {row["checkpoint_id"]: datetime.fromisoformat(row["arrive_at"]) for row in timeline}
    matched = []
    for stop in request.required_stops:
        checkpoint = next((c for c in required if _same_place(stop.place, places.get(c.place_id))), None)
        if checkpoint is None:
            flag("REQUIRED_STOP_MISSING", f"The user's required stop {stop.place.place_text or stop.stop_id} is not a "
                                          "required checkpoint in the plan.")
            continue
        matched.append((stop, checkpoint))
        if checkpoint.dwell_minutes < stop.dwell_minutes:
            flag("DWELL_TOO_SHORT", f"The user needs {stop.dwell_minutes} minutes at {checkpoint.checkpoint_id}; the "
                                    f"plan allows {checkpoint.dwell_minutes}.", checkpoint.checkpoint_id)
        arrive = arrivals.get(checkpoint.checkpoint_id)
        if arrive and stop.window_start and arrive < stop.window_start:
            flag("WINDOW_MISSED", f"The plan reaches {checkpoint.checkpoint_id} at {_clock(arrive)}, before it opens at "
                                  f"{_clock(stop.window_start)}.", checkpoint.checkpoint_id)
        if arrive and stop.window_end and arrive + timedelta(minutes=checkpoint.dwell_minutes) > stop.window_end:
            flag("WINDOW_MISSED", f"The plan is at {checkpoint.checkpoint_id} until "
                                  f"{_clock(arrive + timedelta(minutes=checkpoint.dwell_minutes))}, after it closes at "
                                  f"{_clock(stop.window_end)}.", checkpoint.checkpoint_id)
    fixed = sorted((s.order_index, positions[c.checkpoint_id], c.checkpoint_id) for s, c in matched
                   if s.order_index is not None)
    if [p for _, p, _ in fixed] != sorted(p for _, p, _ in fixed):
        flag("REQUIRED_ORDER", "Required stops are not in the order the user fixed: "
                               f"{', '.join(i for _, _, i in fixed)}.")

    # --- Places and activities ---
    claims = {claim.claim_id: (place.place_id, claim) for place in plan.places for claim in place.claims}
    notes = []
    for checkpoint in remaining:
        place = places.get(checkpoint.place_id)
        _check_checkpoint(checkpoint, place, claims, camera_lookup, allow_synthetic, flag)
        if place and not any(c.basis in ("source", "field_verified") for c in place.claims):
            notes.append(f"{checkpoint.checkpoint_id} ({place.name}) has no sourced claim: present only fiction and "
                         "what the user observes there, no historical facts.")

    # --- Story ---
    story = plan.story
    if not story.premise.strip():
        flag("MISSING_PREMISE", "The story has no premise.")
    if not story.solution.strip():
        flag("MISSING_SOLUTION", "The story has no solution, so it cannot end.")
    checkpoint_ids = {c.checkpoint_id for c in plan.checkpoints}
    used = {}
    for checkpoint in plan.checkpoints:
        beat = beats.get(checkpoint.story_beat_id) if checkpoint.story_beat_id else None
        if beat is None:
            if not checkpoint.required_by_user and checkpoint in remaining:
                flag("STOP_WITHOUT_STORY", f"{checkpoint.checkpoint_id} does not move the story; give it a beat.",
                     checkpoint.checkpoint_id)
            continue
        if beat.checkpoint_id != checkpoint.checkpoint_id:
            flag("BEAT_MISMATCH", f"{checkpoint.checkpoint_id} reveals {beat.beat_id}, but that beat is tied to "
                                  f"{beat.checkpoint_id or 'chat'}.", checkpoint.checkpoint_id)
        if beat.beat_id in used:
            flag("BEAT_REUSED", f"{beat.beat_id} is revealed at both {used[beat.beat_id]} and {checkpoint.checkpoint_id}.",
                 checkpoint.checkpoint_id)
        used[beat.beat_id] = checkpoint.checkpoint_id
    revealed = set(state.revealed_beat_ids) if state else set()
    skipped = (set(state.skipped_ids) | set(state.blocked_ids)) if state else set()
    for beat in story.beats:
        if beat.checkpoint_id is not None and beat.checkpoint_id not in checkpoint_ids:
            flag("ORPHAN_BEAT", f"{beat.beat_id} is tied to {beat.checkpoint_id}, which is not in the plan; move it to "
                                "another stop or deliver it in chat (checkpoint_id null).")
        if beat.checkpoint_id in skipped and beat.beat_id not in revealed:
            flag("CLUE_STRANDED", f"{beat.beat_id} waits at {beat.checkpoint_id}, which the user skipped or could not "
                                  "reach; move it to another stop or deliver it in chat.", beat.checkpoint_id)

    # --- Revision rules ---
    if previous is not None:
        _check_revision(plan, previous, state, set(waived_required_ids), flag)

    report = ValidationReport(ok=not violations, evaluated_at=now, violations=violations,
                              estimated_total_minutes=round(total, 1))
    evaluation = Evaluation(report=report, timeline=timeline, finish_at=finish, travel_minutes=travel,
                            dwell_minutes=dwell, contingency_minutes=contingency, slack_minutes=slack, notes=notes)
    if slack is not None and slack < 0:
        evaluation.suggestions = _time_savers(remaining, legs_by_pair, order)
    return evaluation


def _check_checkpoint(checkpoint: Checkpoint, place, claims, camera_lookup, allow_synthetic, flag):
    cid, activity = checkpoint.checkpoint_id, checkpoint.activity
    if place is None:
        return  # AdventurePlan already rejects an unknown place
    if place.point is None:
        flag("PLACE_UNLOCATED", f"{place.name} has no coordinates, so it cannot be routed.", cid)

    if activity.physical_requirements or activity.type == "verified_feature":
        if not activity.evidence_ids:
            flag("UNSUPPORTED_PHYSICAL_TASK", f"{cid} asks about something physical ({'; '.join(activity.physical_requirements) or activity.type}) "
                                              "without evidence; make it a user observation or chat puzzle.", cid)
        for claim_id in activity.evidence_ids:
            owner, claim = claims.get(claim_id, (None, None))
            if claim is None:
                continue  # AdventurePlan already rejects unknown evidence
            if owner != checkpoint.place_id:
                flag("EVIDENCE_ELSEWHERE", f"Evidence {claim_id} is about {owner}, not this stop's place.", cid)
            elif claim.kind != "physical_feature":
                flag("UNSUPPORTED_PHYSICAL_TASK", f"Evidence {claim_id} is a {claim.kind} claim; it does not show the "
                                                  "feature is there today. Use a user observation or chat puzzle.", cid)
    if activity.type == "chat_puzzle" and not activity.hints:
        flag("MISSING_HINTS", f"The puzzle at {cid} has no hints; give at least one.", cid)
    if activity.type == "camera_capture":
        camera = camera_lookup(checkpoint.camera_checkpoint_id) if camera_lookup else None
        usable = camera is not None and camera.enabled and (
            camera.verification_status == "field_verified"
            or (allow_synthetic and camera.verification_status == "synthetic_fixture"))
        if not usable:
            flag("CAMERA_UNAVAILABLE", f"Camera checkpoint {checkpoint.camera_checkpoint_id} is unknown, disabled, or "
                                       "not field-verified; use a non-camera activity.", cid)
        elif place.point is not None and _meters(camera.stand_location.lat, camera.stand_location.lng,
                                                  place.point.lat, place.point.lng) > SAME_PLACE_M:
            flag("CAMERA_ELSEWHERE", f"The standing position for {camera.checkpoint_id} is more than {SAME_PLACE_M} m "
                                     f"from {place.name}.", cid)


def _check_revision(plan, previous, state, waived, flag):
    if plan.supersedes_plan_id != previous.plan_id:
        flag("REVISION_LINEAGE", f"A revision must supersede the active plan {previous.plan_id}.")
    if plan.version <= previous.version:
        flag("REVISION_LINEAGE", f"A revision needs a version above {previous.version}.")
    old = {c.checkpoint_id: c for c in previous.checkpoints}
    new = {c.checkpoint_id: c for c in plan.checkpoints}
    completed = set(state.completed_ids) if state else set()
    for cid in completed & set(old):
        if new.get(cid) != old[cid]:
            flag("COMPLETED_CHANGED", f"Completed stop {cid} must stay in the plan unchanged.", cid)
    resolved = completed | (set(state.skipped_ids) | set(state.blocked_ids) if state else set())
    for cid, checkpoint in old.items():
        if checkpoint.required_by_user and cid not in resolved and cid not in new and cid not in waived:
            flag("REQUIRED_STOP_DROPPED", f"Required stop {cid} was dropped without the user waiving it.", cid)
    old_beats = {b.beat_id: b for b in previous.story.beats}
    new_beats = {b.beat_id: b for b in plan.story.beats}
    for beat_id in (state.revealed_beat_ids if state else []):
        if beat_id not in new_beats:
            flag("REVEALED_BEAT_DROPPED", f"Revealed beat {beat_id} must stay in the story.")
        elif beat_id in old_beats and (new_beats[beat_id].summary, new_beats[beat_id].reveals) != (
                old_beats[beat_id].summary, old_beats[beat_id].reveals):
            flag("REVEALED_BEAT_CHANGED", f"Revealed beat {beat_id} changed; the user was already told it.")


def _time_savers(remaining, legs_by_pair, order):
    """Optional stops whose removal would save the most time, as upper bounds to confirm by re-routing."""
    savings = []
    for checkpoint in remaining:
        if checkpoint.required_by_user:
            continue
        i = order.index(checkpoint.checkpoint_id)
        into = legs_by_pair.get((order[i - 1], order[i]))
        out_of = legs_by_pair.get((order[i], order[i + 1])) if i + 1 < len(order) else None
        around = sum(leg.duration_minutes for leg in (into, out_of) if leg is not None)
        savings.append((checkpoint.dwell_minutes + around, checkpoint))
    savings.sort(key=lambda item: -item[0])
    return [f"Dropping optional {c.checkpoint_id} saves up to {minutes:.0f} minutes ({c.dwell_minutes} dwell plus its "
            "legs); re-route to confirm." for minutes, c in savings[:3]] or [
        "No optional stop is left to drop; shorten dwell times, allow transit, or ask the user which limit can change."]


def _same_place(location, place):
    if place is None:
        return False
    if location.point is not None and place.point is not None:
        return _meters(location.point.lat, location.point.lng, place.point.lat, place.point.lng) <= SAME_PLACE_M
    return bool(location.place_text) and location.place_text.strip().lower() == place.name.strip().lower()


def _meters(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def _clock(moment: datetime) -> str:
    return moment.astimezone(ZoneInfo(NYC_TIMEZONE)).strftime("%-I:%M %p")
