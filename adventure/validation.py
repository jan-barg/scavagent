"""The adventure plan evaluator, Scavagent's original planning tool (evaluate_adventure_plan).

It rebuilds a plan's timeline from its route legs and dwell times, then checks the plan against
the user's request and the product rules in docs/PLAN.md:

- time: the finish, plus contingency, against a deadline or time budget; enough contingency; the
  stated total (travel, waits, and dwell, without contingency) matching the computed one;
- the request: every required stop present at the place the user named, in the user's order,
  inside its time window, with at least the dwell the user needs; no stop marked required that the
  user did not require; only the travel modes the user allows;
- route data: legs that connect the visiting order, transit timings that are neither stale nor
  already missed, no synthetic fixture data in a live plan;
- activities: a physical task only with physical-feature evidence from its own place, no invented
  object for the user to find there, hints for a puzzle, a camera stop only at an enabled,
  field-verified position near the stop;
- story: every optional stop moves the story, beats tied to the right stop and used once, no clue
  stranded at a skipped stop, no real person given a part in the plot (an architect from the
  sources, anyone the sources or the user name, or a voice "sounding like" someone);
- story design (docs/STORY_DESIGN.md), for a new plan and for what a revision adds: a briefing with
  a handler who has a contact channel, characters introduced before they act and every cast member
  used, a clue at every stop that a later beat uses, a finale built on the clues, codes and keys
  earned from solved puzzles, stops tied to a stated theme through their own claims, and enough
  stops for the time;
- a revision: the plan it replaces, completed stops unchanged, revealed beats kept, unresolved
  required stops kept unless the user waived them.

It returns a schemas.ValidationReport of concrete violations and the timeline behind them. It
checks arithmetic and references; it cannot certify that a quoted source is true.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from schemas import (
    NYC_TIMEZONE,
    AdventurePlan,
    AdventureState,
    CameraCheckpoint,
    Character,
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
MIN_BRIEFING_CHARS = 200  # about three sentences
ROOM_FOR_A_STOP_MINUTES = 10  # unused minutes that would fit one more short stop

CameraLookup = Callable[[str], CameraCheckpoint | None]

# Props a story might pretend are waiting at a real place. Fiction should arrive in chat instead.
# Objects a story might pretend are waiting at a real place. Fiction should arrive in chat instead.
# Words with everyday meanings ("key detail", "note its shape", "take a photo") are left out.
_PROP = (r"(?:note|envelope|chalk mark|chalk|package|parcel|card|letter|sticker|microfilm|film canister|token|coin|"
         r"capsule|briefcase|bag|box|flyer|map|transmitter|bug|disk|folder|dossier|dead drop|cache|stash)s?")
_OBJECT = rf"(?:(?:the|a|an|your|some|this|that)\s+)?(?:[\w-]+\s+){{0,2}}{_PROP}\b"  # the object of the verb
_ON_SITE = [  # asked in an activity prompt
    re.compile(rf"\b(?:find|look for|locate|search for|retrieve|collect|pick up|grab|dig up|uncover)\s+{_OBJECT}", re.I),
    re.compile(rf"\b(?:left|hid|stashed|placed|taped|wedged|tucked|planted|dropped)\s+"
               rf"(?:behind\s+|for you\s+)?{_OBJECT}", re.I),
    re.compile(rf"\b{_PROP}\b\s+(?:is\s+|was\s+|has been\s+|sits\s+)?(?:hidden|stashed|taped|wedged|tucked|planted|waiting)\b",
               re.I),
]
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_ARCHITECTS = re.compile(r"architect/builder ([^;]+);")  # the LPC building record claims name them
_FOUND = re.compile(rf"\byou (?:find|spot|discover|notice|see|locate|recover|pick up|retrieve|uncover)\s+{_OBJECT}"
                    rf"|\b(?:hands|gives|passes|slips) you\s+{_OBJECT}|\b(?:handed to you|falls into your hands)\b", re.I)
# Things a story might hand over instead of letting the user earn them. Everyday senses ("a combination of
# styles", "Morse code", "code name", "the key to the mystery") are left out.
_EARNED_OBJECT = re.compile(
    r"\b(?:pass(?:word|code|phrase)s?|combinations?(?!\s+of\b)|coordinates?|keycards?"
    r"|(?<!dress )(?<!zip )(?<!area )(?<!morse )(?<!postal )(?<!source )(?<!bar )(?<!color )code(?:word)?s?(?!\s+names?\b)"
    r"|(?:safe|locker|vault|door|room|box|cabinet|master|skeleton|brass|iron|spare|final|missing|secret)\s+keys?"
    r"|keys?\s+(?:card|to\s+(?:the|a|an|this|that|his|her|their|your)\s+(?:\w+\s+)?(?:door|safe|locker|vault|room|box|"
    r"lock|cabinet|drawer|office|archive|studio|car|trunk|chest|gate|cell)))\b", re.I)
# What a person does in a plot, as opposed to a sourced fact about them ("Emery Roth designed the towers").
_PLOT_VERBS = (r"ghost|spirit|voice|signal\w*|radio\w*|call\w*|whisper\w*|left|leav\w*|hid|hide\w*|stole|steal\w*|"
               r"sen[dt]\w*|want\w*|knew|know\w*|watch\w*|wait\w*|appear\w*|return\w*|said|say\w*|told|tell\w*|"
               r"warn\w*|ask\w*|met|meet\w*|hand\w*|gave|give\w*|plant\w*|buri\w*|himself|herself")
# A real person as a voice or look-alike: "a tipster sounding remarkably like Julian Casablancas".
_IMPERSONATION = re.compile(
    r"(?i:\b(?:sound|look|talk|dress|sing)(?:s|ed|ing)?\s+(?:\w+\s+){0,2}?like|\bimpersonat\w*|\ba dead ringer for)"
    r"\s+((?:[A-Z][\w'’.-]*)(?:\s+[A-Z][\w'’.-]*){0,3})")
# Runs of capitalized words, with initials and quoted nicknames: Samuel "Roxy" Rothafel, Thomas W. Lamb.
_NAME_TOKEN = r"(?:[A-Z][a-z]+(?:['’-][A-Za-z]+)*|[\"“][A-Z][a-z]+[\"”]|[A-Z]\.)"
_CAPITALIZED_RUN = re.compile(rf"\b{_NAME_TOKEN}(?:\s+{_NAME_TOKEN})+")
_NOT_A_NAME = frozenset("""street avenue broadway theatre theater temple hall house hotel church cathedral synagogue chapel
    park square center centre station records company club tower building plaza museum library school college
    university bank apartments side village city york manhattan brooklyn bronx queens island studio studios society
    association opera orchestra band line railroad bridge road place court heights hill river district commission
    department landmarks preservation register historic national american corporation lounge ballroom music rock
    jazz clock sessions session around west east north south upper lower revival deco art arts beaux renaissance
    style war world knights times records""".split())
_TITLES = frozenset("the agent detective inspector doctor mr mrs ms miss madame sir lady lord captain officer professor "
                    "sergeant chief director".split())


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
            "passes": self.report.ok,
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
    origin: str | None = None,
    start_at: datetime | None = None,
    waived_required_ids: list[str] | tuple = (),
    camera_lookup: CameraLookup | None = None,
    allow_synthetic: bool = False,
    story_checks: bool = True,
    stop_count_stated: bool = False,
    user_text: str = "",
    rejected_links: set[str] | frozenset = frozenset(),
) -> Evaluation:
    """Evaluate a new plan; with `previous` and `state`, a revision of the active one; or with
    `origin` and `state`, an adventure under way, from the stop the user last left.

    `start_at` is when the remaining route begins: by default the first leg's departure for a
    new plan and `now` otherwise. `waived_required_ids` are required checkpoints the user
    explicitly dropped. `camera_lookup` returns the catalogue record for a camera checkpoint id.
    `allow_synthetic` accepts fixture routes and cameras (development only).

    The story design checks apply to a new plan and to what a revision of such a plan adds;
    `story_checks=False` skips them when a saved plan is only re-timed. `stop_count_stated`: the user
    asked for a number of stops. `user_text`: the user's words, to recognize real people they name.
    `rejected_links`: URLs an earlier draft in this conversation was already told to drop.
    """
    violations: list[Violation] = []

    def flag(code, message, checkpoint_id=None):
        violations.append(Violation(code=code, message=message, checkpoint_id=checkpoint_id))

    request = plan.request
    # Progress belongs to the plan under way; a new plan starts clean even if ids repeat.
    retiming = origin is not None  # re-checking an adventure under way, not a new or revised plan
    under_way = previous is not None or retiming
    state = state if under_way else None
    resolved = set()
    if state is not None:
        resolved = set(state.completed_ids) | set(state.skipped_ids) | set(state.blocked_ids)
    remaining = [c for c in plan.checkpoints if c.checkpoint_id not in resolved]
    places = {p.place_id: p for p in plan.places}
    beats = {b.beat_id: b for b in plan.story.beats}
    local = ZoneInfo(NYC_TIMEZONE)

    # --- Route: the legs must connect the visiting order ---
    origin = origin or ("current_location" if previous is not None else "start")
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
        if (leg.from_id, leg.to_id) not in pairs and not (retiming and leg.to_id in resolved):  # already walked
            flag("ROUTE_GAP", f"Leg {leg.leg_id} ({leg.from_id} -> {leg.to_id}) is not part of the visiting order "
                              f"{' -> '.join(order)}.")
    for a, b in pairs:
        if (a, b) not in legs_by_pair:
            flag("ROUTE_GAP", f"No route leg from {a} to {b}; route that leg before evaluating.")

    # --- Timeline ---
    first_leg = legs_by_pair.get(pairs[0]) if pairs else None
    if start_at is None:
        start_at = now if under_way or first_leg is None or first_leg.depart_at is None else first_leg.depart_at
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
    if not retiming and abs(plan.estimated_total_minutes - total) > ESTIMATE_TOLERANCE_MINUTES:
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
            where = stop.place.place_text or stop.stop_id
            spot = (f'place {{"name": "{where}", "lat": {stop.place.point.lat}, "lng": {stop.place.point.lng}}}'
                    if stop.place.point else f'the place "{where}"')
            flag("REQUIRED_STOP_MISSING", f"The user's required stop {where} is not a required checkpoint in the plan. "
                                          f"Add a stop at {spot} with required_by_user true and its own activity; a "
                                          "landmark nearby does not stand in for it. If it is closed or the user no longer "
                                          "needs it, ask them, then pass their answer as waived_required_ids or a "
                                          "substitute required stop.")
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
    for checkpoint in required:
        place = places.get(checkpoint.place_id)
        if not any(_same_place(stop.place, place) for stop in request.required_stops):
            nearest = min(((_meters(s.place.point.lat, s.place.point.lng, place.point.lat, place.point.lng), s)
                           for s in request.required_stops if s.place.point and place and place.point),
                          key=lambda pair: pair[0], default=None)
            detail = f", {nearest[0]:.0f} m from {nearest[1].place.place_text}" if nearest else ""
            flag("REQUIRED_MISMARKED", f"{checkpoint.checkpoint_id} ({place.name if place else checkpoint.place_id}) is "
                                       f"marked required but is not at a place the user required{detail}. Keep it as an "
                                       "optional stop (required_by_user false), and put the required stop at the user's "
                                       "place itself.", checkpoint.checkpoint_id)
    fixed = sorted((s.order_index, positions[c.checkpoint_id], c.checkpoint_id) for s, c in matched
                   if s.order_index is not None)
    if [p for _, p, _ in fixed] != sorted(p for _, p, _ in fixed):
        flag("REQUIRED_ORDER", "Required stops are not in the order the user fixed: "
                               f"{', '.join(i for _, _, i in fixed)}.")

    # --- Places and activities ---
    claims = {claim.claim_id: (place.place_id, claim) for place in plan.places for claim in place.claims}
    rejected = {url.rstrip("/.,;") for url in rejected_links}
    for checkpoint_id, url in _unsourced(plan, remaining):
        again = "The same link was rejected before. " if url in rejected else ""
        own = _source_url(plan, checkpoint_id)
        instead = f"use this stop's source URL {own}" if own else "use a URL copied from a claim's source_urls"
        flag("UNSOURCED_LINK", f"{again}{url} is not a source URL of any claim in the plan. Do not build links: "
                               f"{instead}, or leave the link out.", checkpoint_id)
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
    # A revision inherits its premise, briefing, cast, and solution; only its unrevealed beats are new text.
    fiction = [] if previous is not None else [
        story.premise, story.briefing or "", story.solution,
        *(member if isinstance(member, str) else f"{member.name} {member.role or ''}" for member in story.cast)]
    fiction += [f"{b.summary} {b.reveals or ''} {b.clue or ''}" for b in story.beats if b.beat_id not in revealed]
    cast_names = [] if previous is not None else [_cast_name(member) for member in story.cast]
    for name in sorted(_architects(plan)):
        # Stating what they built is a sourced fact; a part in the plot or the cast is not.
        if (any(name.lower() in member.lower() for member in cast_names)
                or any(_in_the_plot(name, text) for text in fiction)):
            flag("REAL_PERSON_IN_FICTION", f"The story gives {name}, a real architect named in the sources, a part in "
                                           "the plot. State real people only as sourced facts; invent the cast.")
    if story_checks and previous is None:  # story design v2: the cast against everyone named in sources and request
        people = {**_named_people([user_text, request.theme or ""], "the user's request"),
                  **_named_people([c.text for place in plan.places for c in place.claims], "the sources")}
        for member in story.cast:
            name = _cast_name(member)
            shared = next((people[word] for word in _name_words(name) if word in people), None)
            if shared:
                flag("REAL_PERSON_IN_FICTION", f"The cast member {name} shares a name with {shared[0]}, a real person "
                                               f"named in {shared[1]}. Real people appear only as sourced history; "
                                               "invent the character instead.")
    if story_checks:  # and any voice "sounding like" someone, in the story's new text
        old = {b.beat_id for b in previous.story.beats} if previous is not None else set()
        fresh = fiction if previous is None else [f"{b.summary} {b.reveals or ''} {b.clue or ''}"
                                                  for b in story.beats if b.beat_id not in old]
        for name in sorted({name for text in fresh for name in _impersonated(text)}):
            flag("REAL_PERSON_IN_FICTION", f"The story has a voice or look-alike of {name}. Real people, including the "
                                           "user's idols, can be the subject of the adventure but never characters; "
                                           "give that part to an invented character.")
    for beat in story.beats:
        found = _FOUND.search(f"{beat.summary} {beat.reveals or ''}")
        if found and beat.beat_id not in (set(state.revealed_beat_ids) if state else set()):
            flag("INVENTED_PROP", f"{beat.beat_id} says something physically reached the user ('{found.group(0)}') at a "
                                  "real place, from an object or a person who will not be there. Tell it as something "
                                  "delivered in chat instead.", beat.checkpoint_id)
        if beat.checkpoint_id is not None and beat.checkpoint_id not in checkpoint_ids and beat.beat_id not in revealed:
            flag("ORPHAN_BEAT", f"{beat.beat_id} is tied to {beat.checkpoint_id}, which is not in the plan; move it to "
                                "another stop or deliver it in chat (checkpoint_id null).")
        if beat.checkpoint_id in skipped and beat.beat_id not in revealed:
            flag("CLUE_STRANDED", f"{beat.beat_id} waits at {beat.checkpoint_id}, which the user skipped or could not "
                                  "reach; move it to another stop or deliver it in chat.", beat.checkpoint_id)

    # --- Story design ---
    if story_checks and not retiming and (previous is None or previous.story.briefing):
        available = None
        if request.deadline is not None:
            available = (request.deadline - start_at).total_seconds() / 60
        elif request.duration_minutes is not None:
            available = float(request.duration_minutes)
        _check_story_design(plan, previous, flag, available=available, slack=slack, stop_count_stated=stop_count_stated)

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
    if not activity.evidence_ids:
        found = next((m for pattern in _ON_SITE if (m := pattern.search(activity.prompt))), None)
        if found:
            flag("INVENTED_PROP", f"The prompt at {cid} sends the user to find a physical object ('{found.group(0)}') "
                                  "that nothing shows is there. Deliver props in chat (a radio message, a telegram) "
                                  "or ask them to observe what is really there.", cid)
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


def _check_story_design(plan, previous, flag, *, available, slack, stop_count_stated):
    """docs/STORY_DESIGN.md: for a new plan, the whole story; for a revision, the stops and beats it adds."""
    story, request = plan.story, plan.request
    whole = previous is None
    old_beats = {} if whole else {b.beat_id: b.model_dump(exclude={"checkpoint_id"}) for b in previous.story.beats}
    old_stops = set() if whole else {c.checkpoint_id for c in previous.checkpoints}
    new_beats = [b for b in story.beats if old_beats.get(b.beat_id) != b.model_dump(exclude={"checkpoint_id"})]
    new_stops = [c for c in plan.checkpoints if c.checkpoint_id not in old_stops]
    order = {c.checkpoint_id: i for i, c in enumerate(plan.checkpoints)}
    by_id = {c.checkpoint_id: c for c in plan.checkpoints}
    places = {p.place_id: p for p in plan.places}
    beats = {b.beat_id: b for b in story.beats}
    end = len(plan.checkpoints)

    def position(beat):
        return order.get(beat.checkpoint_id, end)  # chat beats, the finale among them, come after every stop

    stop_beats = [b for b in story.beats if b.checkpoint_id in order]
    new_stop_beats = [b for b in new_beats if b.checkpoint_id in order]
    finale = next((b for b in reversed(story.beats) if b.checkpoint_id is None), None)
    cast = [member if isinstance(member, Character) else Character(name=_cast_name(member))
            for member in story.cast if _cast_name(member)]
    briefing = (story.briefing or "").strip()

    # --- The opening and the cast ---
    if whole:
        if len(briefing) < MIN_BRIEFING_CHARS:
            flag("MISSING_BRIEFING", ("The briefing is only a line. " if briefing else "The story has no briefing. ") +
                 "Write story.briefing, 3-6 sentences in second person: who the user is, who they work with and how "
                 "that person reaches them (radio, phone), what is at stake, and how a stop works.")
        if not any(m.introduced_in == "briefing" and (m.contact or "").strip() for m in cast):
            flag("HANDLER_MISSING", "No cast member is introduced in the briefing with a contact channel. Add a handler "
                                    "to story.cast (name, role \"handler\", contact such as \"radio\", introduced_in "
                                    "\"briefing\") and name them in the briefing.")
        for member in cast:
            if member.introduced_in == "briefing":
                if briefing and not _mentions(briefing, member.name):
                    flag("CAST_UNINTRODUCED", f"{member.name} is introduced_in the briefing, but the briefing never names "
                                              "them. Name them there, or set introduced_in to the stop where they first "
                                              "appear.")
            elif member.introduced_in not in order:
                flag("CAST_UNINTRODUCED", f"{member.name} is introduced_in {member.introduced_in!r}, which is neither "
                                          "\"briefing\" nor a stop of this plan.")
            elif not any(_member(name, cast) is member for b in stop_beats if b.checkpoint_id == member.introduced_in
                         for name in b.characters):
                flag("CAST_UNINTRODUCED", f"{member.name} is introduced_in {member.introduced_in}, but that stop's beat "
                                          "does not list them. Add them to its characters, or introduce them in the "
                                          "briefing.", member.introduced_in)
        appearing = {id(m) for b in story.beats for name in b.characters if (m := _member(name, cast))}
        for member in cast:
            if id(member) not in appearing:
                flag("CAST_UNUSED", f"{member.name} never appears in a beat. List them in the characters of the beats "
                                    "where they act (the handler usually in every stop's beat), or drop them.")
    for beat in story.beats if whole else new_beats:
        where = beat.checkpoint_id or "chat"
        for name in beat.characters:
            member = _member(name, cast)
            if member is None:
                flag("CAST_UNINTRODUCED", f"{beat.beat_id} ({where}) lists {name}, who is not in story.cast. Add them "
                                          "to the cast, introduced in the briefing or at this stop, or take them out of "
                                          "the beat.", beat.checkpoint_id)
            elif member.introduced_in in order and order[member.introduced_in] > position(beat):
                flag("CAST_UNINTRODUCED", f"{beat.beat_id} ({where}) lists {member.name}, who is introduced only at "
                                          f"{member.introduced_in}. Introduce them in the briefing or at this stop.",
                     beat.checkpoint_id)

    # --- Clues ---
    for beat in stop_beats if whole else new_stop_beats:
        if not (beat.clue or "").strip():
            flag("CLUE_MISSING", f"{beat.checkpoint_id}'s beat gives the user no clue. Give it a clue: a number, word, "
                                 "name, or direction they earn there (a chat_puzzle's solution works best), for a later "
                                 "beat or the finale to use.", beat.checkpoint_id)
    for beat in story.beats if whole else new_beats:
        for ref in beat.uses:
            source = beats.get(ref)
            if source is not None and beat.checkpoint_id in order and position(source) > position(beat):
                flag("CLUE_OUT_OF_ORDER", f"{beat.beat_id} at {beat.checkpoint_id} uses the clue from "
                                          f"{source.checkpoint_id or 'chat'}, which the user gets only later. A beat can "
                                          "build only on clues the user already has.", beat.checkpoint_id)
    clue_beats = {b.beat_id for b in stop_beats if (b.clue or "").strip()}
    if whole:
        used = {ref for other in story.beats for ref in other.uses
                if ref in beats and ref != other.beat_id and position(beats[ref]) <= position(other)}
        for beat in stop_beats:
            if beat.beat_id in clue_beats and beat.beat_id not in used:
                flag("CLUE_UNUSED", f"{beat.checkpoint_id}'s clue ({beat.clue}) is never used. List "
                                    f"{beat.checkpoint_id} in the uses of a later beat or the finale, and let that beat "
                                    "build on it.", beat.checkpoint_id)
        needed = min(2, len(plan.checkpoints))
        if finale is None:
            flag("SOLUTION_UNEARNED", f"The story has no finale. Add one in chat_beats whose uses lists the stops whose "
                                      f"clues solve the case (at least {needed}), and write the solution from those clues.")
        elif len(set(finale.uses) & clue_beats) < needed:
            flag("SOLUTION_UNEARNED", f"The finale builds on {len(set(finale.uses) & clue_beats)} stop clue(s); it needs "
                                      f"at least {needed}. List those stops in its uses and let their clues solve the case "
                                      "(e.g. \"Locker 1021, under the name Roxy\").")

    # --- Codes, keys, and passwords come from puzzles the user solves ---
    # A stop's clue is earned when the user solves its chat_puzzle and the clue states that answer. A beat may
    # speak of a code (a key, a combination...) at such a stop, when it builds on an earned clue (its uses), or
    # when an earlier earned clue names that kind of object ("the locker code 1021").
    solved = {}  # beat_id -> position of the stop where the user earns its clue
    named = []  # (position, kinds of object an earned clue names)
    for beat in stop_beats:
        activity = by_id[beat.checkpoint_id].activity
        if activity.type == "chat_puzzle" and activity.solution and beat.clue and _within(activity.solution, beat.clue):
            solved[beat.beat_id] = position(beat)
            named.append((position(beat), {_object_kind(m.group(0)) for m in _EARNED_OBJECT.finditer(beat.clue)}))
    texts = [(f"{b.beat_id} ({b.checkpoint_id or 'chat'})", b, position(b), f"{b.summary} {b.reveals or ''} {b.clue or ''}")
             for b in (story.beats if whole else new_beats)]
    if whole:
        texts.append(("The solution", finale, end, story.solution))  # it resolves what the finale builds on
    for label, beat, at, text in texts:
        built = beat is not None and (beat.beat_id in solved or any(solved.get(ref, end + 1) <= at for ref in beat.uses))
        for found in _EARNED_OBJECT.finditer(text):
            if not built and not any(p <= at and _object_kind(found.group(0)) in kinds for p, kinds in named):
                flag("OBJECT_UNEARNED", f"{label} mentions \"{found.group(0)}\", but it builds on no puzzle the user "
                                        "solves. List in its uses the stops whose chat_puzzle answers make it up (each "
                                        "with activity.solution, stated in its beat's clue), or leave it out.",
                     beat.checkpoint_id if beat is not None and label != "The solution" else None)
                break

    # --- The theme, through each stop's own sourced claims ---
    if request.theme and "theme" not in request.defaulted_fields:
        for checkpoint in plan.checkpoints if whole else new_stops:
            if checkpoint.required_by_user or checkpoint.activity.type == "camera_capture":
                continue  # the user's own errand, or a camera position: not a place chosen for the theme
            place = places.get(checkpoint.place_id)
            name = place.name if place else checkpoint.place_id
            link, own = checkpoint.theme_link, {c.claim_id for c in place.claims} if place else set()
            if link is None or not link.claim_ids:
                flag("THEME_UNLINKED", f"{checkpoint.checkpoint_id} ({name}) has no theme_link to \"{request.theme}\". "
                                       "Cite claim_ids from research_place about this place that tie it to the theme, "
                                       "with why in the story's voice. If none do, swap in a place whose claims do, or "
                                       "say honestly in the briefing that no documented site fits and tie each stop to "
                                       "the nearest honest version of the theme.", checkpoint.checkpoint_id)
            elif foreign := [i for i in link.claim_ids if i not in own]:
                flag("THEME_UNLINKED", f"{checkpoint.checkpoint_id}'s theme_link cites {', '.join(foreign)}, which "
                                       f"research_place did not return for {name}. Cite only this place's claims.",
                     checkpoint.checkpoint_id)

    # --- Enough story for the time ---
    if whole and not stop_count_stated:
        # Only a limit the user gave can leave no room for more; a budget the planner chose is not a reason.
        limited = ((request.duration_minutes and "duration_minutes" not in request.defaulted_fields)
                   or (request.deadline and "deadline" not in request.defaulted_fields))
        needed = 2 if not limited else 3 if available >= 60 else 2 if available >= 20 else 1
        room = not limited or slack is None or slack >= ROOM_FOR_A_STOP_MINUTES
        if len(plan.checkpoints) < needed and room:
            time = (f"{available:.0f} available minutes, {slack:.0f} unused" if limited else
                    "no time limit from the user, so a first chapter")
            flag("TOO_FEW_STOPS", f"{len(plan.checkpoints)} stop(s) for {time}: plan at least {needed}. Add a researched "
                                  "stop near the route (ride transit between far-apart stops), and drop any time limit "
                                  "the user did not give. If the user asked for fewer stops, include \"stop_count\" in "
                                  "user_stated.")


def _cast_name(member):
    """A cast member's name; an old plain entry may carry a role in parentheses: "Agent Vance (Handler)"."""
    return member.name if isinstance(member, Character) else re.sub(r"\s*\(.*?\)\s*", " ", member).strip() or member


def _name_words(name):
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'’-]+", name) if len(w) >= 3 and w.lower() not in _TITLES]


def _member(name, cast):
    """The cast member a beat's character name refers to: the full name or one of its words ("Mara" for Mara Quill)."""
    wanted = _cast_name(name).lower()
    return next((m for m in cast if m.name.lower() == wanted), None) or next(
        (m for m in cast if wanted in _name_words(m.name) or re.search(rf"\b{re.escape(m.name.lower())}\b", wanted)),
        None)


def _mentions(text, name):
    return any(re.search(rf"\b{re.escape(word)}\b", text, re.I) for word in _name_words(name))


def _in_the_plot(name, text):
    """Whether the text gives a real person a part: "Emery Roth's ghost signals", not "Emery Roth built this"."""
    person = re.escape(name)
    return bool(re.search(rf"\b(?:ghost|spirit|voice)\s+of\s+{person}\b|\b{person}(?:['’]s)?\s+(?:\w+\s+){{0,2}}?"
                          rf"(?:{_PLOT_VERBS})\b", text, re.I))


def _impersonated(text):
    """Full names written as a voice or look-alike: "a tipster sounding remarkably like Julian Casablancas"."""
    names = []
    for found in _IMPERSONATION.finditer(text):
        before = text[:found.start()].rstrip()
        if not before or before[-1] in ".!?:;\"“(" or re.search(r"\b(?:it|this|there)$", before, re.I):
            continue  # "It looks like Mara Quill was right", "Sounds like Roxy beat us here": figures of speech
        tokens = [t.rstrip(".'’") for t in found.group(1).split()]
        tokens = tokens[1:] if tokens[0] == "The" else tokens
        if len(tokens) >= 2 and not any(t.lower() in _NOT_A_NAME for t in tokens):
            names.append(" ".join(tokens))
    return names


def _named_people(texts, source):
    """Surnames and nicknames of people named in the texts: {word: (full name, source)}."""
    people = {}
    for text in texts:
        for run in _CAPITALIZED_RUN.findall(text or ""):
            tokens = [re.sub(r"['’]s$", "", t.strip("\"“”")) for t in run.split()]
            if tokens[0] == "The":
                tokens = tokens[1:]
            while tokens and tokens[-1] in ("Jr", "Sr"):
                tokens.pop()  # Sammy Davis Jr.: the surname is Davis
            if len(tokens) < 2 or any(t.lower() in _NOT_A_NAME for t in tokens):
                continue
            nicknames = [t.strip("\"“”") for t in run.split() if t[0] in "\"“"]
            for word in {tokens[-1], *nicknames}:
                if len(word) >= 3 and word.lower() not in _TITLES:
                    people.setdefault(word.lower(), (" ".join(tokens), source))
    return people


def _object_kind(text):
    lowered = text.lower()
    return next(kind for kind in ("pass", "combination", "coordinate", "key", "code") if kind in lowered)


def _within(part, text):
    """Whether `part` appears in `text` as whole words, ignoring case and punctuation."""
    plain = [re.sub(r"[^a-z0-9]+", " ", value.lower()).strip() for value in (part, text)]
    return bool(plain[0]) and f" {plain[0]} " in f" {plain[1]} "


def unsourced_links(plan: AdventurePlan) -> set[str]:
    """The links in a plan's text that no claim cites (UNSOURCED_LINK)."""
    return {url for _, url in _unsourced(plan, plan.checkpoints)}


def _unsourced(plan, checkpoints):
    """(checkpoint_id, url) for each link in the plan's text that is not a claim's source URL."""
    sourced = {str(url).rstrip("/") for place in plan.places for claim in place.claims for url in claim.source_urls}
    texts = [*((c.checkpoint_id, f"{c.activity.prompt} {c.theme_link.why if c.theme_link else ''}") for c in checkpoints),
             *((b.checkpoint_id, f"{b.summary} {b.reveals or ''} {b.clue or ''}") for b in plan.story.beats),
             (None, plan.story.briefing or "")]
    return [(checkpoint_id, url) for checkpoint_id, text in texts
            for url in (found.rstrip("/.,;") for found in _URL.findall(text)) if url not in sourced]


def _source_url(plan, checkpoint_id):
    """The first source URL of the stop's place, to offer instead of a built link."""
    checkpoint = next((c for c in plan.checkpoints if c.checkpoint_id == checkpoint_id), None)
    place = next((p for p in plan.places if checkpoint and p.place_id == checkpoint.place_id), None)
    return next((str(url) for claim in (place.claims if place else []) for url in claim.source_urls), None)


def _architects(plan):
    """Architects and builders the plan's LPC building records name."""
    names = set()
    for place in plan.places:
        for claim in place.claims:
            found = _ARCHITECTS.search(claim.text)
            for name in re.split(r"\s+and\s+|,\s*", found.group(1)) if found else []:
                if len(name.strip()) >= 5 and name.strip().lower() not in ("not recorded", "not determined"):
                    names.add(name.strip())
    return names


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
