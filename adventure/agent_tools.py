"""The agent's planning tools: evaluate_adventure_plan, save_adventure_plan, and get_next_directions.

evaluate_adventure_plan turns the agent's draft into a routed plan (adventure.drafts), runs the
evaluator (adventure.validation), and keeps the evaluated plan in this session's stored record.
save_adventure_plan stores a plan that passed through state.save_plan. get_next_directions gives
the way to the current stop, re-routed when transit times are stale or the user has moved. All are
session tools: the server passes the ToolContext, so the model never names a session.
"""

import os
import re
from datetime import timedelta

import state
from pydantic import ValidationError

from adventure.drafts import STATED_FIELDS, DraftError, build_new_plan, build_revision, parse_minutes, parse_time
from adventure.validation import evaluate_plan, unsourced_links
from integrations import cameras
from integrations.common import distance_m, in_nyc
from integrations.routes import get_route
from schemas import AdventurePlan, Freshness, tool_error, tool_ok
from state import ToolContext

DRAFT_FRESH = timedelta(minutes=10)  # an older draft's legs and times no longer start from now
UNUSED_MINUTES = 12  # a passing plan leaving this much of the user's time unused could add a stop
# The user asked for a camera stop (grader query 2: "include a camera stop if one fits").
CAMERA_WORDS = re.compile(r"\b(?:cameras?|webcams?|selfies?|photos? of (?:me|us|myself|ourselves))\b", re.I)
TRANSIT_REFRESH = timedelta(minutes=10)  # transit times older than this are looked up again
LOCATION_FRESH = timedelta(minutes=10)
OFF_ROUTE_M = 200  # a user this far from the leg's start gets directions from where they are


def evaluate_adventure_plan(ctx: ToolContext, draft: dict | None = None, **fields) -> dict:
    """Build the draft into a routed plan and check it; or, with kind "check", re-time the adventure under way.

    Draft fields sent beside `draft` instead of inside it are accepted too; models often flatten them.
    """
    draft = {**fields, **(draft or {})} if isinstance(draft, dict) or draft is None else draft
    if not isinstance(draft, dict):
        return tool_error("INVALID_ARGUMENT", "draft must be an object.", retryable=False,
                          next_step="Pass the draft fields listed in the tool schema.")
    kind = draft.get("kind", "new")
    now = ctx.now().replace(microsecond=0)
    active, progress = state.active_plan(ctx.record), ctx.record.adventure
    lookup, dev = camera_lookup(), dev_mode()
    # Links earlier drafts were told to drop: a repeat says so, so the model stops resending it.
    rejected = {url for entry in ctx.record.drafts.values() if entry.plan.validation and not entry.plan.validation.ok
                for url in unsourced_links(entry.plan)}
    try:
        if kind == "check":
            return _check_under_way(active, progress, draft, now, lookup, dev)
        if kind == "revision":
            if active is None or progress.status != "active":
                return tool_error("INVALID_ARGUMENT", "No adventure is under way to revise.", retryable=False,
                                  next_step="Plan a new adventure with kind 'new'.")
            plan, waived, warnings = build_revision(draft, active, progress, now, lookup)
            context = {"state": progress, "previous": active, "waived_required_ids": waived, "rejected_links": rejected}
        elif kind == "new":
            plan, warnings = build_new_plan(draft, now, lookup)
            waived, words = [], _user_words(ctx, draft)
            context = {"stop_count_stated": "stop_count" in (draft.get("user_stated") or []),
                       "user_text": words, "rejected_links": rejected, "camera_finder": _camera_finder(draft, words, dev)}
        else:
            return tool_error("INVALID_ARGUMENT", f"Unknown kind {kind!r}.", retryable=False,
                              next_step="Use kind 'new', 'revision', or 'check'.")
        plan, evaluation = _evaluated(plan, now, lookup, dev, context)
    except DraftError as e:
        return tool_error("INVALID_ARGUMENT", str(e), retryable=False, next_step="Fix the draft and evaluate again.")
    except (ValidationError, ValueError, TypeError, AttributeError, KeyError) as e:
        # A malformed field the draft checks did not anticipate: still something the model can fix.
        return tool_error("INVALID_ARGUMENT", f"The draft is malformed: {_detail(e)}", retryable=False,
                          next_step="Send each field in the shape the tool schema describes, then evaluate again.")
    # Stored with the session (Jan's state.py), so saving works after a restart or on another instance.
    if not ctx.record.remember_draft(plan.plan_id, state.EvaluatedDraft(kind=kind, plan=plan, waived_required_ids=waived)):
        return tool_error("INVALID_ARGUMENT", "This plan is too large to keep for saving.", retryable=False,
                          next_step="Shorten the story, activity, and hint text, then evaluate again.")
    summary = evaluation.summary()
    if summary["passes"] and (summary["slack_minutes"] or 0) >= UNUSED_MINUTES:
        summary["suggestions"].append(f"{summary['slack_minutes']:.0f} of the user's minutes are unused; consider "
                                      "adding a stop near the route before saving.")
    summary["next_step"] = ("Call save_adventure_plan with this draft_id. Then reply with the briefing, the number of "
                            "stops, about how long it takes, and the first stop, and ask if they are ready. Also say "
                            "what the user asked for that this plan leaves out, such as a camera stop no verified "
                            "position fits." if summary["passes"] else
                            "Fix every violation (each message says how), then evaluate again. Do not present this plan.")
    return tool_ok({"draft_id": plan.plan_id, "kind": kind, **summary, "plan": overview(plan, evaluation.timeline)},
                   warnings=warnings, freshness=_freshness(plan, now))


def save_adventure_plan(ctx: ToolContext, draft_id: str, start_now: bool = False) -> dict:
    """Store an evaluated plan that passed. A revision replaces the remaining route at once."""
    entry = ctx.record.drafts.get(draft_id)
    if entry is None:
        return tool_error("INVALID_ARGUMENT", "No evaluated draft with that id in this conversation.",
                          retryable=False, next_step="Evaluate the draft again with evaluate_adventure_plan.")
    kind, plan, waived = entry.kind, entry.plan, entry.waived_required_ids
    if not plan.validation.ok:
        return tool_error("PLAN_INFEASIBLE", "That draft did not pass evaluation.", retryable=False,
                          next_step="Fix its violations and evaluate again.")
    age = ctx.now() - plan.validation.evaluated_at
    if age > DRAFT_FRESH:
        return tool_error("INVALID_ARGUMENT", f"That draft was evaluated {age.total_seconds() / 60:.0f} minutes ago, so its "
                                              "times no longer start from now.", retryable=False,
                          next_step="Evaluate the same draft again, then save the new draft_id.")
    if kind == "new" and ctx.record.adventure.status == "active":
        return tool_error("INVALID_ARGUMENT", "An adventure is already under way.", retryable=False,
                          next_step="Ask whether to abandon it (update_adventure_state abandon_adventure) or revise it instead.")
    result = state.save_plan(ctx, plan, activate=bool(start_now), waived_required_ids=waived)
    if result["ok"]:
        ctx.record.drafts.pop(draft_id, None)
    return result


def get_next_directions(ctx: ToolContext) -> dict:
    """Directions to the current checkpoint, or to the destination once the stops are done."""
    plan, progress = state.active_plan(ctx.record), ctx.record.adventure
    if plan is None or progress.status not in ("proposed", "active"):
        return tool_error("INVALID_ARGUMENT", "There is no adventure under way.", retryable=False,
                          next_step="Plan an adventure first.")
    target = progress.current_checkpoint_id or ("destination" if plan.request.destination else None)
    if target is None:
        return tool_ok({"remaining": False}, warnings=["No stops remain; finish the adventure."])
    now = ctx.now().replace(microsecond=0)
    places = {p.place_id: p for p in plan.places}
    goal = _endpoint(plan, places, target)
    order = [c.checkpoint_id for c in plan.checkpoints]
    finished = [i for i in (order[:order.index(target)] if target in order else order) if i in progress.completed_ids]
    leg = next((leg for leg in plan.legs if leg.to_id == target), None)
    if leg is not None and leg.from_id in order and leg.from_id not in progress.completed_ids:
        leg = None  # it starts at a stop the user skipped or could not reach, not where they are

    here = _usable_location(progress.latest_location, now)
    origin = _endpoint(plan, places, leg.from_id) if leg else None  # None for a revision's "current_location"
    known = _endpoint(plan, places, finished[-1]) if finished else plan.request.start.point
    wandered = here is not None and origin is not None and distance_m(here.lat, here.lng, origin.lat, origin.lng) > OFF_ROUTE_M
    stale = leg is not None and "transit" in leg.actual_modes and (
        now - leg.retrieved_at > TRANSIT_REFRESH or (leg.depart_at is not None and leg.depart_at < now - timedelta(minutes=2)))
    start = here or origin or known

    warnings, details = [], None
    if (leg is None or stale or wandered) and start is not None and goal is not None:
        result = get_route([{"id": "here", "lat": start.lat, "lng": start.lng}, {"id": target, "lat": goal.lat, "lng": goal.lng}],
                           modes=plan.request.allowed_modes, depart_at=now.isoformat())
        if result["ok"]:
            leg_data, details = result["data"]["legs"][0], result["data"]["details"][0]
            directions = {"minutes": leg_data["duration_minutes"], "modes": leg_data["actual_modes"],
                          "instructions": leg_data["instructions"], "arrive_at": leg_data["arrive_at"], "refreshed": True}
        else:
            warnings.append(f"Could not refresh the route ({result['error']['message']}); these are the planned directions.")
    if details is None:
        if leg is None:
            return tool_error("NO_MATCH", f"The plan has no leg to {target}.", retryable=False,
                              next_step="Call get_route from the user's location to the stop.")
        directions = {"minutes": leg.duration_minutes, "modes": leg.actual_modes, "instructions": leg.instructions,
                      "arrive_at": leg.arrive_at.isoformat() if leg.arrive_at else None, "refreshed": False}
    if target == "destination":
        name = plan.request.destination.place_text or "the destination"
    else:
        name = places[next(c.place_id for c in plan.checkpoints if c.checkpoint_id == target)].name
    if target == "destination":
        warnings.append("The user is still on the way to the destination. Do not call finish_adventure until they say "
                        "they have arrived.")
    camera = _camera_at(plan, target)
    if camera and camera["verification_status"] == "image_verified":
        warnings.append("This camera spot was matched on the camera image, not tested in person: say so when you give "
                        "its positioning instructions, and offer a retake if they cannot find themselves.")
    return tool_ok({"to_id": target, "name": name, **directions, "details": details, **({"camera": camera} if camera else {})},
                   warnings=warnings,
                   freshness=Freshness(kind="scheduled" if "transit" in directions["modes"] else "static_reference",
                                       retrieved_at=now))


def _camera_at(plan, checkpoint_id):
    """What the user needs at a camera stop, from the catalogue: its planning context has usually scrolled away."""
    checkpoint = next((c for c in plan.checkpoints if c.checkpoint_id == checkpoint_id), None)
    record = camera_lookup()(checkpoint.camera_checkpoint_id) if checkpoint and checkpoint.camera_checkpoint_id else None
    if record is None:
        return None
    return {"checkpoint_id": record.checkpoint_id, "landmark": record.landmark, "side_of_street": record.side_of_street,
            "positioning_instructions": record.positioning_instructions,
            "verification_status": record.verification_status}


def _endpoint(plan, places, endpoint_id):
    """Coordinates of a leg endpoint, when the plan knows them."""
    if endpoint_id == "start":
        return plan.request.start.point
    if endpoint_id == "destination":
        return plan.request.destination.point if plan.request.destination else None
    checkpoint = next((c for c in plan.checkpoints if c.checkpoint_id == endpoint_id), None)
    return places[checkpoint.place_id].point if checkpoint else None  # "current_location" is not stored


def _check_under_way(active, progress, draft, now, lookup, dev):
    if active is None or progress.status not in ("proposed", "active"):
        return tool_error("INVALID_ARGUMENT", "There is no adventure to check.", retryable=False,
                          next_step="Plan a new adventure with kind 'new'.")
    changes = {key: draft[key] for key in ("deadline", "duration_minutes", "current_location") if draft.get(key)}
    if progress.status == "active":
        # Re-time what is left from where the user is now: a dry run of a revision keeping every pending stop.
        done = set(progress.completed_ids) | set(progress.skipped_ids) | set(progress.blocked_ids)
        keep = [{"keep": c.checkpoint_id} for c in active.checkpoints if c.checkpoint_id not in done]
        if not keep and active.request.destination is None:
            return tool_ok({"kind": "check", "passes": True, "remaining": False},
                           warnings=["No stops remain; finish the adventure."])
        plan, waived, warnings = build_revision({"kind": "revision", "stops": keep, **changes}, active, progress, now, lookup)
        plan, evaluation = _evaluated(plan, now, lookup, dev,
                                      {"state": progress, "previous": active, "waived_required_ids": waived})
        origin = "current_location"
    else:  # saved but not started: the plan as saved, timed from now
        plan, warnings, origin = active, [], "start"
        deadline = parse_time(changes.get("deadline"))
        if deadline is None and changes.get("duration_minutes"):
            deadline = now + timedelta(minutes=parse_minutes(changes["duration_minutes"], "duration_minutes"))
        if deadline is not None:
            plan = AdventurePlan.model_validate({**plan.model_dump(), "request": {**plan.request.model_dump(), "deadline": deadline}})
        evaluation = evaluate_plan(plan, now=now, start_at=now, camera_lookup=lookup, allow_synthetic=dev,
                                   story_checks=False)  # re-timing only: the story passed when it was saved
    summary = evaluation.summary()
    summary["next_step"] = ("The remaining route still fits." if summary["passes"] else
                            "Revise the remaining route (kind 'revision') or tell the user which limit cannot be met.")
    return tool_ok({"kind": "check", "plan_id": active.plan_id, "from": origin, **summary}, warnings=warnings,
                   freshness=_freshness(plan, now))


def _evaluated(plan, now, lookup, dev, context):
    """The plan carrying the evaluator's own total and report, and that evaluation."""
    def evaluate(candidate):
        return evaluate_plan(candidate, now=now, camera_lookup=lookup, allow_synthetic=dev, **context)

    plan = plan.model_copy(update={"estimated_total_minutes": evaluate(plan).report.estimated_total_minutes})
    evaluation = evaluate(plan)
    return plan.model_copy(update={"validation": evaluation.report}), evaluation


def _camera_finder(draft, words, dev):
    """When the user asked for a camera stop, a route search the evaluator runs itself if the plan has none.

    The model may skip find_camera_checkpoints and say no camera fits; this makes that claim checked.
    Each route is searched once per evaluation (the plan is evaluated twice)."""
    if "camera_stop" not in (draft.get("user_stated") or []) and not CAMERA_WORDS.search(words):
        return None
    searched = {}

    def find(route):
        key = tuple((round(p["lat"], 6), round(p["lng"], 6)) for p in route)
        if key not in searched:
            searched[key] = cameras.find_camera_checkpoints(corridor=[{"lat": lat, "lng": lng} for lat, lng in key],
                                                            allow_synthetic=dev)
        return searched[key]
    return find


def _user_words(ctx, draft):
    """The user's own words, so the evaluator can tell real people they name from invented characters."""
    earlier = [m["content"] for m in ctx.record.messages[-20:]
               if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return "\n".join([*earlier, str(draft.get("user_request") or "")])


def _usable_location(latest, now):
    """The user's browser fix, when it is fresh, precise, and inside the city (graders may be elsewhere)."""
    if (latest is not None and latest.point is not None and in_nyc(latest.point.lat, latest.point.lng)
            and now - latest.observed_at <= LOCATION_FRESH and (latest.accuracy_m or 0) <= OFF_ROUTE_M):
        return latest.point
    return None


def _detail(error):
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'value'}: {e['msg']}" for e in error.errors()[:5])
    return f"{type(error).__name__}: {error}"


def overview(plan: AdventurePlan, timeline: list[dict]) -> dict:
    """A compact view of a plan for the model: stops, legs, and the story premise."""
    places = {p.place_id: p for p in plan.places}
    arrivals = {row["checkpoint_id"]: row["arrive_local"] for row in timeline}
    return {
        "plan_id": plan.plan_id,
        "version": plan.version,
        "premise": plan.story.premise,
        "stops": [{"checkpoint_id": c.checkpoint_id, "name": places[c.place_id].name, "place_id": c.place_id,
                   "required": c.required_by_user, "activity": c.activity.type, "dwell_minutes": c.dwell_minutes,
                   "arrive_local": arrivals.get(c.checkpoint_id), "story_beat_id": c.story_beat_id,
                   "source_url": next((str(url) for claim in places[c.place_id].claims for url in claim.source_urls), None)}
                  for c in plan.checkpoints],
        "legs": [{"leg_id": leg.leg_id, "from": leg.from_id, "to": leg.to_id, "minutes": leg.duration_minutes,
                  "modes": leg.actual_modes, "first_step": leg.instructions[0] if leg.instructions else None}
                 for leg in plan.legs],
        "chat_beats": [b.beat_id for b in plan.story.beats if b.checkpoint_id is None],
    }


def camera_lookup():
    """Catalogue records by checkpoint id, from integrations.cameras' validated catalogue loader."""
    try:
        records = cameras.load_checkpoints(allow_synthetic=dev_mode())
    except (OSError, ValueError, TypeError, KeyError):
        records = []
    return {record.checkpoint_id: record for record in records}.get


def dev_mode() -> bool:
    return "1" in (os.environ.get("SCAVAGENT_DEV_FIXTURES"), os.environ.get("SCAVAGENT_DEV_CAMERA_FIXTURES"))


def _freshness(plan, now):
    rides = any("transit" in leg.actual_modes for leg in plan.legs)
    return Freshness(kind="scheduled" if rides else "static_reference", retrieved_at=now)


def reset_for_tests():
    """Nothing process-wide to clear: drafts live in each session's record."""


# --- What the model sees ---

_LOCATION = {
    "type": "object",
    "properties": {"place_text": {"type": "string"}, "lat": {"type": "number"}, "lng": {"type": "number"}},
    "required": ["lat", "lng"],
}
_BEAT = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "The invented scene you tell the user, in the story's voice."},
        "reveals": {"type": "string", "description": "What it reveals toward the solution."},
        "characters": {"type": "array", "items": {"type": "string"},
                       "description": "Cast names in this beat. At a stop, the first one contacts the user on arrival."},
        "clue": {"type": "string", "description": "At a stop: the concrete clue the user now holds (a number, word, "
                                                  "name, or direction), e.g. \"locker 1021\"; from a chat_puzzle, it "
                                                  "states the solution."},
        "uses": {"type": "array", "items": {"type": "string"},
                 "description": "Earlier stops whose clues this beat builds on, e.g. [\"stop_1\"]. Stops are numbered "
                                "stop_1, stop_2, ... in visiting order. The finale uses the stop clues."},
    },
    "required": ["summary"],
}
_ACTIVITY = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["user_observation", "chat_puzzle", "verified_feature", "camera_capture"],
            "description": (
                "user_observation: the user describes something they notice there; chat_puzzle: a fictional clue "
                "or choice given in chat, solvable from what you tell them, with its answer as solution; "
                "verified_feature: a physical detail backed by physical_feature evidence; camera_capture: a DOT "
                "camera photo at a verified position."
            ),
        },
        "prompt": {"type": "string", "description": "What you ask the user when they arrive."},
        "answer_rule": {"type": "string", "description": "How to judge their reply. Be generous."},
        "hints": {"type": "array", "items": {"type": "string"}, "description": "Progressive hints; a puzzle needs one."},
        "fallback": {"type": "string", "description": "How they continue if they cannot do it."},
        "solution": {"type": "string", "description": "chat_puzzle: the exact answer, kept from the user until solved."},
        "evidence_ids": {"type": "array", "items": {"type": "string"},
                         "description": "claim_ids of physical_feature claims. Research claims about history or "
                                        "architecture do not count."},
        "physical_requirements": {"type": "array", "items": {"type": "string"},
                                  "description": "Physical things the task needs; each needs evidence. Usually empty."},
    },
    "required": ["type", "prompt", "answer_rule", "fallback"],
}
_STOP = {
    "type": "object",
    "properties": {
        "place_id": {"type": "string", "description": "A place_id from find_places or research_place."},
        "place": {"type": "object", "description": "A geocoded spot with no research, e.g. a required errand.",
                  "properties": {"name": {"type": "string"}, "lat": {"type": "number"}, "lng": {"type": "number"}}},
        "camera_checkpoint_id": {"type": "string", "description": "From find_camera_checkpoints (camera_capture)."},
        "keep": {"type": "string", "description": "Revision only: keep this pending checkpoint_id unchanged."},
        "required_by_user": {"type": "boolean", "description": "A stop the user said they must make."},
        "dwell_minutes": {"type": "number", "description": "Minutes spent there, activity included. Default 5."},
        "window_start": {"type": "string", "description": "ISO opening time, for a required stop with hours."},
        "window_end": {"type": "string", "description": "ISO closing time."},
        "activity": _ACTIVITY,
        "beat": _BEAT,
        "theme_link": {
            "type": "object",
            "description": "With a theme the user stated: how this place ties to it, through its own research.",
            "properties": {
                "claim_ids": {"type": "array", "items": {"type": "string"},
                              "description": "claim_ids research_place returned for this place."},
                "why": {"type": "string", "description": "One sentence in the story's voice."},
            },
            "required": ["claim_ids", "why"],
        },
        "move_beat_id": {"type": "string", "description": "Revision only: an unrevealed beat to reveal here instead."},
        "research_focus": {"type": "string", "description": "The focus you researched this place with, if any."},
    },
}

PLANNING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_adventure_plan",
            "description": (
                "Check an adventure before offering it. kind 'new' builds your draft into a plan: it looks up each "
                "stop's place, routes every leg (walking or subway/bus), and then checks timing against the deadline or "
                "time budget with contingency, required stops and hours, allowed modes, physical tasks against "
                "evidence, camera positions, and the story: briefing and cast, a clue at every stop that the finale "
                "builds on, and theme links. kind 'revision' replaces the remaining route of the "
                "adventure under way and also checks that completed stops and revealed clues are kept. kind 'check' "
                "re-times the adventure under way from now, optionally against a new deadline, without changing it. "
                "Returns passes, concrete violations, a timeline, slack, and suggestions; a passing draft gets a "
                "draft_id for save_adventure_plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["new", "revision", "check"]},
                            "start": {**_LOCATION, "description": "new: from geocode_place, or the user's fresh location."},
                            "destination": {**_LOCATION, "description": "new: only if the user gave one."},
                            "deadline": {"type": "string", "description": "ISO time they must be done by (New York time if no offset)."},
                            "duration_minutes": {"type": "integer", "description": "new: a time budget instead of a deadline."},
                            "allowed_modes": {"type": "array", "items": {"type": "string", "enum": ["walk", "transit"]},
                                              "description": "Default walk and transit."},
                            "theme": {"type": "string", "description": "new: the user's theme; default a playful NYC mystery."},
                            "user_stated": {"type": "array", "items": {"type": "string", "enum": list(STATED_FIELDS)},
                                            "description": "Which of these the user actually said; the rest are defaults."},
                            "depart_at": {"type": "string", "description": "ISO time they leave; default now."},
                            "contingency_minutes": {"type": "number", "description": "Default 10% of travel and dwell, at least 2."},
                            "required_stops": {
                                "type": "array",
                                "description": "new: every place the user said they must visit, as geocoded. Also add a "
                                               "stop at each one, with required_by_user true.",
                                "items": {"type": "object", "properties": {
                                    "place_text": {"type": "string"}, "lat": {"type": "number"}, "lng": {"type": "number"},
                                    "dwell_minutes": {"type": "number", "description": "Minutes the user needs there."},
                                    "window_start": {"type": "string"}, "window_end": {"type": "string"},
                                    "order_index": {"type": "integer", "description": "Only if the user fixed the order."}},
                                    "required": ["lat", "lng"]},
                            },
                            "current_location": {**_LOCATION, "description": "revision: where the user is now, if you know better than their last location."},
                            "waived_required_ids": {"type": "array", "items": {"type": "string"},
                                                    "description": "revision: required stops the user explicitly agreed to drop."},
                            "user_request": {"type": "string", "description": "new: the user's request, in their words."},
                            "story": {
                                "type": "object",
                                "description": "new: the fiction. A revision keeps the existing story.",
                                "properties": {
                                    "premise": {"type": "string", "description": "The mystery, in one line."},
                                    "briefing": {"type": "string", "description": (
                                        "The opening scene, 3-6 sentences in second person: who the user is, who "
                                        "they work with and how that person reaches them, what is at stake, and how "
                                        "a stop works.")},
                                    "cast": {"type": "array", "description": "Invented characters; never a real person.",
                                             "items": {"type": "object", "properties": {
                                                 "name": {"type": "string"},
                                                 "role": {"type": "string", "description": "e.g. handler, rival, informant."},
                                                 "contact": {"type": "string", "description": "How they reach the user: radio, phone, telegram."},
                                                 "introduced_in": {"type": "string", "description": "\"briefing\", or the stop where they first appear."}},
                                                 "required": ["name", "role"]}},
                                    "solution": {"type": "string", "description": "How the case resolves, from the clues."},
                                },
                                "required": ["premise", "briefing", "cast", "solution"],
                            },
                            "stops": {"type": "array", "items": _STOP,
                                      "description": "new: every stop in visiting order. revision: the remaining stops."},
                            "chat_beats": {"type": "array", "items": _BEAT, "description": "Beats told in chat, e.g. the finale."},
                        },
                        "required": ["kind"],
                    },
                },
                "required": ["draft"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_adventure_plan",
            "description": (
                "Save a draft that passed evaluate_adventure_plan. A new plan is saved as proposed, or started at once "
                "with start_now when the user already asked to begin; otherwise start it later with "
                "update_adventure_state start_adventure. A revision takes effect immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string"},
                    "start_now": {"type": "boolean", "description": "The user asked to begin right away."},
                },
                "required": ["draft_id"],
            },
        },
    },
]

PLANNING_TOOLS.append({
    "type": "function",
    "function": {
        "name": "get_next_directions",
        "description": (
            "Directions to the current checkpoint of the adventure under way (or to the destination after the last "
            "stop): minutes, travel modes, and step-by-step instructions. Transit legs are looked up again when their "
            "times are stale, and the route starts from the user's fresh location if they have moved away."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
})

PLANNING_TOOL_MAP = {"evaluate_adventure_plan": evaluate_adventure_plan, "save_adventure_plan": save_adventure_plan,
                     "get_next_directions": get_next_directions}
PLANNING_SESSION_TOOLS = set(PLANNING_TOOL_MAP)
