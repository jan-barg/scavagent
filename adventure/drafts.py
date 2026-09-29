"""Turn the agent's proposed adventure into an AdventurePlan the evaluator can check.

The agent chooses the stops, activities, and story. This module takes each stop's place from what
find_places and research_place returned (the agent cannot supply evidence of its own), routes the
legs with integrations.routes, assigns stable ids, and fills in the totals.

A revision keeps the completed stops, the story, and every revealed beat of the plan it replaces.
Unrevealed beats of dropped or skipped stops move into chat unless the draft gives them a new stop.
The remaining route starts from the user's current location.
"""

import math
import re
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
DEPART_WINDOW = timedelta(hours=2)  # a departure further ahead is most likely a UTC/local mix-up
PAST_GRACE = timedelta(minutes=5)  # a departure further back would time the route from a moment already gone
ACCURATE_ENOUGH_M = 200  # a browser fix less precise than this does not place the user on a block
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
    # They are required whenever a stop is marked required, so a nearby stand-in cannot define them.
    required_specs = draft.get("required_stops") or []
    if any(c.required_by_user for c in checkpoints) and not required_specs:
        raise DraftError("A stop is marked required_by_user, so list every place the user required in required_stops, "
                         "as geocoded from their words.")
    required = []
    for n, spec in enumerate(required_specs, 1):
        if not isinstance(spec, dict) or _location(spec, "required stop", now) is None:
            raise DraftError("Each required_stops entry needs lat and lng (from geocode_place).")
        required.append(RequiredStop(
            stop_id=f"req_{n}", place=_location(spec, "required stop", now),
            dwell_minutes=round(parse_minutes(spec.get("dwell_minutes"), "required stop dwell_minutes")),
            window_start=parse_time(spec.get("window_start")), window_end=parse_time(spec.get("window_end")),
            order_index=_order_index(spec.get("order_index"))))
    request = AdventureRequest.model_validate({**request.model_dump(), "required_stops": required})

    points = [_point("start", request.start.point),
              *[_point(c.checkpoint_id, places[c.place_id].point, c.dwell_minutes) for c in checkpoints]]
    if request.destination is not None:
        points.append(_point("destination", request.destination.point))
    depart = parse_time(draft.get("depart_at")) or now
    if depart - now > DEPART_WINDOW:
        raise DraftError(f"depart_at {depart.isoformat()} is {(depart - now).total_seconds() / 3600:.1f} hours from now. "
                         "Omit it to start now; tool timestamps ending in Z are UTC, not New York time.")
    if now - depart > PAST_GRACE:
        raise DraftError(f"depart_at {depart.isoformat()} has already passed; omit it to start now.")
    if request.duration_minutes and request.deadline is None:
        # A time budget runs from the planned start; as a deadline it holds through checks and revisions.
        request = AdventureRequest.model_validate({
            **request.model_dump(), "deadline": depart + timedelta(minutes=request.duration_minutes),
            "defaulted_fields": sorted(set(request.defaulted_fields) | {"deadline"})})
    legs, warnings = _route(points, request, depart)

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
    # Skipping a required stop took the user's explicit waiver (state.resolve_checkpoint enforces it).
    # A blocked one stays required until the user waives it or picks a substitute (PLAN.md section 7).
    given_up = set(waived) | (required_ids & set(state.skipped_ids))

    request = _revised_request(draft, old, given_up, now)
    old_places = {p.place_id: p for p in old.places}
    places = {c.place_id: old_places[c.place_id] for c in completed}
    beats = {b.beat_id: b for b in old.story.beats}
    revealed = set(state.revealed_beat_ids)
    checkpoints, moved = list(completed), set()
    # New ids continue past every id this adventure has used, including stops earlier revisions
    # dropped: a reused id would inherit that stop's recorded outcome.
    used = {c.checkpoint_id for c in old.checkpoints} | resolved
    number = 1 + max((int(i.split("_")[-1]) for i in used if i.split("_")[-1].isdigit()), default=0)
    specs = draft.get("stops") or []
    if not isinstance(specs, list) or not all(isinstance(spec, dict) for spec in specs):
        raise DraftError("stops must be a list of objects.")
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
    if added:  # a required stop the user adds mid-adventure, placed where the draft puts it
        extra = [RequiredStop(stop_id=f"req_{len(request.required_stops) + n}", place=_location_of(places[c.place_id], now),
                              dwell_minutes=c.dwell_minutes, window_start=parse_time(spec.get("window_start")),
                              window_end=parse_time(spec.get("window_end")))
                 for n, (c, spec) in enumerate(added, 1)]
        request = AdventureRequest.model_validate({**request.model_dump(), "required_stops": [*request.required_stops, *extra]})

    # Clues tied to stops that are gone, skipped, or blocked belong to chat: an unrevealed one is told
    # there next, and a revealed one was already told there (its words stay the same).
    kept_ids = {c.checkpoint_id for c in checkpoints}
    for beat in list(beats.values()):
        stranded = beat.checkpoint_id is not None and (
            beat.checkpoint_id not in kept_ids or beat.checkpoint_id in set(state.skipped_ids) | set(state.blocked_ids))
        if stranded and beat.beat_id not in moved:
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
    if not all(isinstance(spec, dict) for spec in stops):
        raise DraftError("Each stop must be an object with place_id (or place) and an activity.")
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
        "duration_minutes": round(parse_minutes(draft.get("duration_minutes"), "duration_minutes")) or None,
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
        update["deadline"] = now + timedelta(minutes=parse_minutes(draft["duration_minutes"], "duration_minutes"))
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
    if spec.get("camera_checkpoint_id") and isinstance(spec["activity"], dict) and spec["activity"].get("type") != "camera_capture":
        # Otherwise the stop sits at the camera but never captures, and the reply says no camera fit.
        raise DraftError(f"Stop {checkpoint_id} is the camera stop {spec['camera_checkpoint_id']}: make its activity "
                         "type camera_capture (prompt: the positioning instructions; answer_rule: they say they are "
                         "in position), or leave out camera_checkpoint_id for an ordinary stop.")
    try:
        activity = Activity.model_validate(spec.get("activity") or {})
        return Checkpoint(checkpoint_id=checkpoint_id, place_id=place_id,
                          required_by_user=bool(spec.get("required_by_user")), activity=activity,
                          dwell_minutes=round(parse_minutes(spec.get("dwell_minutes"), "dwell_minutes", DEFAULT_DWELL_MINUTES)),
                          story_beat_id=beat_id, camera_checkpoint_id=spec.get("camera_checkpoint_id"))
    except (ValidationError, TypeError, ValueError) as e:
        detail = _errors(e) if isinstance(e, ValidationError) else str(e)
        raise DraftError(f"Stop {checkpoint_id} is invalid: {detail}") from e


def _beat(beat_id, checkpoint_id, spec):
    if not isinstance(spec, dict) or not str(spec.get("summary") or "").strip():
        raise DraftError(f"Story beat for {checkpoint_id or 'chat'} needs a summary.")
    reveals = spec.get("reveals")
    if isinstance(reveals, list):
        reveals = "; ".join(map(str, reveals))
    return StoryBeat(beat_id=beat_id, checkpoint_id=checkpoint_id, summary=str(spec["summary"]),
                     reveals=str(reveals) if reveals is not None else None)


def _story(spec, beats):
    spec = spec or {}
    if not isinstance(spec, dict):
        raise DraftError("story must be an object with premise, cast, and solution.")
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
        return parse_minutes(draft["contingency_minutes"], "contingency_minutes")
    busy = sum(leg.duration_minutes for leg in legs) + sum(c.dwell_minutes for c in checkpoints)
    return float(max(MIN_CONTINGENCY_MINUTES, math.ceil(CONTINGENCY_SHARE * busy)))


def _current_point(draft, state, completed, places, old, now):
    given = read_point(draft.get("current_location") or {})
    if given is not None:
        return LatLng(lat=given[0], lng=given[1])
    latest = state.latest_location
    if (latest is not None and latest.point is not None and in_nyc(latest.point.lat, latest.point.lng)
            and (latest.accuracy_m or 0) <= ACCURATE_ENOUGH_M
            and now - latest.observed_at <= timedelta(minutes=LOCATION_FRESH_MINUTES)):
        return latest.point  # a grader testing from elsewhere sends a fix outside the city: ignore it
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


def parse_minutes(value, name, default=0.0):
    """Minutes from 12, 12.5, or text like "12 min"; DraftError for anything else."""
    if value is None or value == "":
        return float(default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        found = re.match(r"\s*(\d+(?:\.\d+)?)", str(value))
        if not found:
            raise DraftError(f"{name} must be a number of minutes, not {value!r}.")
        number = float(found.group(1))
    if not 0 <= number <= 600:
        raise DraftError(f"{name} must be between 0 and 600 minutes.")
    return number


def _order_index(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DraftError(f"order_index must be a whole number from 0, not {value!r}.")
    return value


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

