"""Turn the agent's proposed adventure into an AdventurePlan the evaluator can check.

The agent chooses the stops, activities, and story. This module takes each stop's place from what
find_places and research_place returned (the agent cannot supply evidence of its own), routes the
legs with integrations.routes, assigns stable ids, and fills in the totals.

A revision keeps the completed stops, the story, and every revealed beat of the plan it replaces.
Unrevealed beats of dropped or skipped stops move into chat unless the draft gives them a new stop.
The remaining route starts from the user's current location.
"""

import math
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from adventure.validation import CONTINGENCY_SHARE, MIN_CONTINGENCY_MINUTES, CameraLookup
from integrations import research
from integrations.common import distance_m, in_nyc, read_point
from integrations.routes import get_route
from schemas import (
    NYC_TIMEZONE,
    Activity,
    AdventurePlan,
    AdventureRequest,
    AdventureState,
    Checkpoint,
    LatLng,
    LocationContext,
    PlaceEvidence,
    RequiredStop,
    RouteLeg,
    Story,
    StoryBeat,
)

MAX_STOPS = 8
DEFAULT_DWELL_MINUTES = 5
DEFAULT_THEME = "a playful NYC mystery"
LOCATION_FRESH_MINUTES = 15  # a browser location this recent can start a revision's route
STATED_FIELDS = ("destination", "deadline", "duration_minutes", "required_stops", "allowed_modes", "theme")


class DraftError(Exception):
    """The draft cannot become a plan; the message says what to change."""


def build_new_plan(draft: dict, now: datetime, camera_lookup: CameraLookup | None = None):
    """(plan, warnings) for a fresh adventure. The plan has totals but no validation report yet."""
    stops = _stop_specs(draft)
    request = _request(draft, now)
    places, checkpoints, beats = {}, [], []
    for i, spec in enumerate(stops, 1):
        checkpoint_id = f"stop_{i}"
        place = _place(spec, now, camera_lookup)
        places[place.place_id] = place
        beat_id = None
        if spec.get("beat"):
            beat_id = f"beat_{len(beats) + 1}"
            beats.append(_beat(beat_id, checkpoint_id, spec["beat"]))
        checkpoints.append(_checkpoint(spec, checkpoint_id, place.place_id, beat_id))
    for beat in draft.get("chat_beats") or []:
        beats.append(_beat(f"beat_{len(beats) + 1}", None, beat))

    # The places the user said they must visit, as geocoded; the evaluator checks the stops cover them.
    required = [
        RequiredStop(stop_id=f"req_{n}", place=_location(spec, "required stop", now),
                     dwell_minutes=round(float(spec.get("dwell_minutes") or 0)),
                     window_start=parse_time(spec.get("window_start")), window_end=parse_time(spec.get("window_end")),
                     order_index=spec.get("order_index"))
        for n, spec in enumerate(draft.get("required_stops") or [], 1)
    ]
    if not draft.get("required_stops"):  # older drafts marked required stops only on the stops themselves
        for checkpoint, spec in zip(checkpoints, stops):
            if checkpoint.required_by_user:
                required.append(RequiredStop(
                    stop_id=f"req_{len(required) + 1}", place=_location_of(places[checkpoint.place_id], now),
                    dwell_minutes=checkpoint.dwell_minutes, window_start=parse_time(spec.get("window_start")),
                    window_end=parse_time(spec.get("window_end")), order_index=spec.get("order_index")))
    request = AdventureRequest.model_validate({**request.model_dump(), "required_stops": required})

    points = [_point("start", request.start.point),
              *[_point(c.checkpoint_id, places[c.place_id].point, c.dwell_minutes) for c in checkpoints]]
    if request.destination is not None:
        points.append(_point("destination", request.destination.point))
    legs, warnings = _route(points, request, draft.get("depart_at") or now)

    plan = _plan(
        plan_id=f"plan_{uuid.uuid4().hex[:12]}", version=1, request=request, places=list(places.values()),
        checkpoints=checkpoints, legs=legs, story=_story(draft.get("story"), beats), estimated_total_minutes=0,
        contingency_minutes=_contingency(draft, request, legs, checkpoints), created_at=now,
    )
    return plan, warnings


def build_revision(draft: dict, old: AdventurePlan, state: AdventureState, now: datetime,
                   camera_lookup: CameraLookup | None = None):
    """(plan, waived_required_ids, warnings) replacing the remaining route of the active plan."""
    completed = [c for c in old.checkpoints if c.checkpoint_id in state.completed_ids]
    resolved = set(state.completed_ids) | set(state.skipped_ids) | set(state.blocked_ids)
    pending = {c.checkpoint_id: c for c in old.checkpoints if c.checkpoint_id not in resolved}
    required_ids = {c.checkpoint_id for c in old.checkpoints if c.required_by_user}
    waived = [str(w) for w in draft.get("waived_required_ids") or []]
    for checkpoint_id in waived:
        if checkpoint_id not in required_ids:
            raise DraftError(f"{checkpoint_id} is not a stop the user required, so there is nothing to waive.")
    # Skipping a required stop took the user's explicit waiver (state.resolve_checkpoint enforces it),
    # and a blocked one cannot be visited, so neither keeps its place among the required stops.
    given_up = set(waived) | (required_ids & (set(state.skipped_ids) | set(state.blocked_ids)))

    request = _revised_request(draft, old, given_up, now)
    old_places = {p.place_id: p for p in old.places}
    places = {c.place_id: old_places[c.place_id] for c in completed}
    beats = {b.beat_id: b for b in old.story.beats}
    revealed = set(state.revealed_beat_ids)
    checkpoints, moved = list(completed), set()
    number = 1 + max((int(c.checkpoint_id.split("_")[-1]) for c in old.checkpoints
                      if c.checkpoint_id.split("_")[-1].isdigit()), default=0)
    specs = draft.get("stops") or []
    if len(specs) > MAX_STOPS:
        raise DraftError(f"Keep the remaining route to at most {MAX_STOPS} stops.")
    for spec in specs:
        if spec.get("keep"):
            kept = pending.get(spec["keep"])
            if kept is None:
                raise DraftError(f"keep must name a pending stop of the active plan: {', '.join(pending) or 'none left'}.")
            checkpoints.append(kept)
            places[kept.place_id] = old_places[kept.place_id]
            continue
        checkpoint_id, number = f"stop_{number}", number + 1
        place = _place(spec, now, camera_lookup)
        places[place.place_id] = place
        beat_id = None
        if spec.get("move_beat_id"):
            beat_id = spec["move_beat_id"]
            if beat_id not in beats or beat_id in revealed:
                raise DraftError(f"move_beat_id must name an unrevealed beat: {', '.join(sorted(set(beats) - revealed))}.")
            beats[beat_id] = beats[beat_id].model_copy(update={"checkpoint_id": checkpoint_id})
            moved.add(beat_id)
        elif spec.get("beat"):
            beat_id = f"beat_{len(beats) + 1}"
            while beat_id in beats:
                beat_id += "b"
            beats[beat_id] = _beat(beat_id, checkpoint_id, spec["beat"])
        checkpoints.append(_checkpoint(spec, checkpoint_id, place.place_id, beat_id))

    added = [(c, spec) for c, spec in zip(checkpoints[len(checkpoints) - len(specs):], specs)
             if not spec.get("keep") and c.required_by_user]
    if added:
        extra = [RequiredStop(stop_id=f"req_{len(request.required_stops) + n}", place=_location_of(places[c.place_id], now),
                              dwell_minutes=c.dwell_minutes, window_start=parse_time(spec.get("window_start")),
                              window_end=parse_time(spec.get("window_end")))
                 for n, (c, spec) in enumerate(added, 1)]
        request = AdventureRequest.model_validate({**request.model_dump(), "required_stops": [*request.required_stops, *extra]})

    # Clues waiting at stops that are gone, skipped, or blocked are delivered in chat instead.
    kept_ids = {c.checkpoint_id for c in checkpoints}
    for beat in list(beats.values()):
        stranded = beat.checkpoint_id is not None and (
            beat.checkpoint_id not in kept_ids or beat.checkpoint_id in set(state.skipped_ids) | set(state.blocked_ids))
        if stranded and beat.beat_id not in revealed and beat.beat_id not in moved:
            beats[beat.beat_id] = beat.model_copy(update={"checkpoint_id": None})
    for beat in draft.get("chat_beats") or []:
        beat_id = f"beat_{len(beats) + 1}"
        while beat_id in beats:
            beat_id += "b"
        beats[beat_id] = _beat(beat_id, None, beat)

    here = _current_point(draft, state, completed, old_places, old, now)
    remaining = [c for c in checkpoints if c.checkpoint_id not in resolved]
    points = [_point("current_location", here),
              *[_point(c.checkpoint_id, places[c.place_id].point, c.dwell_minutes) for c in remaining]]
    if request.destination is not None:
        points.append(_point("destination", request.destination.point))
    if len(points) < 2:
        raise DraftError("Nothing is left to route: add a stop, or finish the adventure instead.")
    legs, warnings = _route(points, request, now)

    story = old.story.model_copy(update={"beats": list(beats.values())})
    plan = _plan(
        plan_id=f"plan_{uuid.uuid4().hex[:12]}", version=old.version + 1, supersedes_plan_id=old.plan_id,
        request=request, places=list(places.values()), checkpoints=checkpoints, legs=legs, story=story,
        estimated_total_minutes=0, contingency_minutes=_contingency(draft, request, legs, remaining), created_at=now,
    )
    return plan, waived, warnings


# --- Pieces ---


def _plan(**fields):
    try:
        return AdventurePlan(**fields)
    except ValidationError as e:
        # e.g. evidence_ids naming a claim that research_place never returned for that place
        raise DraftError(f"The plan is inconsistent: {_errors(e)}") from e


def _stop_specs(draft):
    stops = draft.get("stops") or []
    if not isinstance(stops, list) or not stops:
        raise DraftError("A plan needs at least one stop.")
    if len(stops) > MAX_STOPS:
        raise DraftError(f"Keep a plan to at most {MAX_STOPS} stops.")
    return stops


def _request(draft, now):
    start = _location(draft.get("start"), "start", now)
    if start is None:
        raise DraftError("start needs place_text and lat/lng; resolve it with geocode_place first.")
    stated = set(draft.get("user_stated") or [])
    deadline = parse_time(draft.get("deadline"))
    if deadline is not None and deadline <= now:
        raise DraftError("The deadline has already passed; ask the user for their time limit.")
    fields = {
        "destination": _location(draft.get("destination"), "destination", now),
        "deadline": deadline,
        "duration_minutes": draft.get("duration_minutes"),
        "allowed_modes": draft.get("allowed_modes") or ["walk", "transit"],
        "theme": draft.get("theme") or DEFAULT_THEME,
    }
    try:
        return AdventureRequest(
            start=start, **fields,
            defaulted_fields=[name for name, value in fields.items() if value is not None and name not in stated],
        )
    except ValidationError as e:
        raise DraftError(f"Request fields are invalid: {_errors(e)}") from e


def _revised_request(draft, old, waived, now):
    update = {}
    if draft.get("deadline"):
        deadline = parse_time(draft["deadline"])
        if deadline <= now:
            raise DraftError("The new deadline has already passed.")
        update["deadline"] = deadline
    if draft.get("duration_minutes") and not draft.get("deadline"):
        # Mid-adventure, "I have 15 minutes" counts from now, so it becomes a deadline.
        update["deadline"] = now + timedelta(minutes=float(draft["duration_minutes"]))
    if draft.get("allowed_modes"):
        update["allowed_modes"] = draft["allowed_modes"]
    if draft.get("destination"):
        update["destination"] = _location(draft["destination"], "destination", now)
    if waived:
        dropped = {c.place_id for c in old.checkpoints if c.checkpoint_id in waived}
        places = {p.place_id: p for p in old.places}
        update["required_stops"] = [
            s for s in old.request.required_stops
            if not any(_near(s.place.point, places[p].point) for p in dropped if places[p].point and s.place.point)
        ]
    defaulted = [f for f in old.request.defaulted_fields if f not in update]
    try:
        return AdventureRequest.model_validate({**old.request.model_dump(), **update, "defaulted_fields": defaulted})
    except ValidationError as e:
        raise DraftError(f"Request changes are invalid: {_errors(e)}") from e


def _place(spec, now, camera_lookup):
    """PlaceEvidence for a stop: a researched place, a camera standing position, or a bare location."""
    camera_id = spec.get("camera_checkpoint_id")
    if camera_id:
        camera = camera_lookup(camera_id) if camera_lookup else None
        if camera is None:
            raise DraftError(f"Unknown camera checkpoint {camera_id}; use one returned by find_camera_checkpoints.")
        return PlaceEvidence(place_id=f"camera:{camera_id}", name=camera.landmark, address=camera.address,
                             point=camera.stand_location, checked_at=now,
                             uncertainty="Camera standing position from the calibrated catalogue.")
    if spec.get("place_id"):
        place_id = str(spec["place_id"])
        known = research.remembered_place(place_id)
        if known is None or known["evidence"] is None:
            result = research.research_place(place_id, focus=spec.get("research_focus"))
            if not result["ok"] and known is None:
                raise DraftError(f"Could not look up {place_id}: {result['error']['message']}")
            known = research.remembered_place(place_id) or known
        if known["evidence"] is not None:
            place = PlaceEvidence.model_validate(known["evidence"])
        else:
            place = PlaceEvidence(place_id=place_id, name=known["name"], address=known["address"],
                                  point=LatLng(**known["point"]) if known["point"] else None, checked_at=now,
                                  uncertainty="Found by find_places but not researched; no sourced facts.")
        if place.point is None:
            raise DraftError(f"{place.name} has no reliable coordinates; geocode its address and pass the stop as "
                             "place {name, lat, lng}.")
        return place
    location = read_point(spec.get("place") or {})
    if location is None:
        raise DraftError("Each stop needs place_id (from find_places), place {name, lat, lng}, or camera_checkpoint_id.")
    if not in_nyc(*location):
        raise DraftError(f"Stop {spec['place'].get('name', location)} is outside New York City.")
    name = str(spec["place"].get("name") or spec["place"].get("place_text") or f"{location[0]:.5f}, {location[1]:.5f}")
    return PlaceEvidence(place_id=f"geo:{location[0]:.5f},{location[1]:.5f}", name=name,
                         point=LatLng(lat=location[0], lng=location[1]), checked_at=now,
                         uncertainty="A location only; no sourced facts about it.")


def _checkpoint(spec, checkpoint_id, place_id, beat_id):
    if not spec.get("activity"):
        raise DraftError(f"Stop {checkpoint_id} needs an activity with type, prompt, answer_rule, and fallback; a "
                         "plain arrival can be a user_observation.")
    try:
        activity = Activity.model_validate(spec.get("activity") or {})
        return Checkpoint(checkpoint_id=checkpoint_id, place_id=place_id,
                          required_by_user=bool(spec.get("required_by_user")), activity=activity,
                          dwell_minutes=round(float(spec.get("dwell_minutes", DEFAULT_DWELL_MINUTES))),
                          story_beat_id=beat_id, camera_checkpoint_id=spec.get("camera_checkpoint_id"))
    except (ValidationError, TypeError, ValueError) as e:
        detail = _errors(e) if isinstance(e, ValidationError) else str(e)
        raise DraftError(f"Stop {checkpoint_id} is invalid: {detail}") from e


def _beat(beat_id, checkpoint_id, spec):
    if not isinstance(spec, dict) or not str(spec.get("summary") or "").strip():
        raise DraftError(f"Story beat for {checkpoint_id or 'chat'} needs a summary.")
    return StoryBeat(beat_id=beat_id, checkpoint_id=checkpoint_id, summary=str(spec["summary"]),
                     reveals=spec.get("reveals"))


def _story(spec, beats):
    spec = spec or {}
    if not str(spec.get("premise") or "").strip() or not str(spec.get("solution") or "").strip():
        raise DraftError("story needs a premise and a solution.")
    return Story(premise=spec["premise"], cast=[str(c) for c in spec.get("cast") or []], solution=spec["solution"],
                 beats=beats)


def _route(points, request, depart_at):
    result = get_route(points, modes=request.allowed_modes, depart_at=_iso(depart_at))
    if not result["ok"]:
        error = result["error"]
        raise DraftError(f"Routing failed ({error['code']}): {error['message']} {error['next_step']}")
    return [RouteLeg.model_validate(leg) for leg in result["data"]["legs"]], result["warnings"]


def _contingency(draft, request, legs, checkpoints):
    if draft.get("contingency_minutes") is not None:
        return max(0.0, float(draft["contingency_minutes"]))
    busy = sum(leg.duration_minutes for leg in legs) + sum(c.dwell_minutes for c in checkpoints)
    return float(max(MIN_CONTINGENCY_MINUTES, math.ceil(CONTINGENCY_SHARE * busy)))


def _current_point(draft, state, completed, places, old, now):
    given = read_point(draft.get("current_location") or {})
    if given is not None:
        return LatLng(lat=given[0], lng=given[1])
    latest = state.latest_location
    if latest is not None and latest.point is not None and now - latest.observed_at <= timedelta(minutes=LOCATION_FRESH_MINUTES):
        return latest.point
    if completed:  # the last stop the user finished is the best remaining guess
        return places[completed[-1].place_id].point
    return old.request.start.point


def _location(spec, name, now):
    if not spec:
        return None
    position = read_point(spec)
    if position is None:
        raise DraftError(f"{name} needs numeric lat and lng.")
    if not in_nyc(*position):
        raise DraftError(f"{name} is outside New York City.")
    source = spec.get("source") if spec.get("source") in ("browser", "user", "geocoded") else "geocoded"
    return LocationContext(point=LatLng(lat=position[0], lng=position[1]),
                           place_text=spec.get("place_text") or spec.get("name"), source=source, observed_at=now)


def _location_of(place, now):
    return LocationContext(point=place.point, place_text=place.name, source="geocoded", observed_at=now)


def _point(point_id, point, dwell=0):
    if point is None:
        raise DraftError(f"{point_id} has no coordinates to route from.")
    return {"id": point_id, "lat": point.lat, "lng": point.lng, "dwell_minutes": dwell}


def _near(a, b, meters=150):
    return distance_m(a.lat, a.lng, b.lat, b.lng) <= meters


def parse_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as e:
            raise DraftError(f"{value!r} is not an ISO time like 2026-10-01T15:45:00-04:00.") from e
    return moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo(NYC_TIMEZONE))


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _errors(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'value'}: {e['msg']}" for e in error.errors()[:5])

