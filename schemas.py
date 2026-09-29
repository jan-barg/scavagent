"""Shared records for Scavagent: the contract between the app, the agent, and the tools.

Every workstream builds against these models. Change them only after both
teammates agree (see docs/CONTRACTS.md). Timestamps must be timezone-aware;
user-facing NYC times are interpreted in America/New_York. Unknown information
stays None rather than becoming a model-invented value.
"""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

NYC_TIMEZONE = "America/New_York"

Id = str  # Stable, opaque identifiers such as "stop_2" or "place_7".
TravelMode = Literal["walk", "transit", "car"]


class Record(BaseModel):
    """Base for shared records: unknown fields are rejected, not silently kept."""

    model_config = ConfigDict(extra="forbid")


class LatLng(Record):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


# --- Tool results ---

ErrorCode = Literal[
    "INVALID_ARGUMENT",
    "NO_MATCH",
    "UPSTREAM_UNAVAILABLE",
    "STALE_DATA",
    "OUTSIDE_COVERAGE",
    "PLAN_INFEASIBLE",
    "MISSING_EVIDENCE",
    "STATE_VERSION_CONFLICT",
    "UNKNOWN_TOOL",
    "INTERNAL_ERROR",
]


class ToolError(Record):
    code: ErrorCode
    message: str = Field(min_length=1)
    retryable: bool
    next_step: str = Field(min_length=1, description="What the model should do instead")


class Freshness(Record):
    """Whether data is live, scheduled, historical, user-reported, or a dev fixture."""

    kind: Literal["live", "scheduled", "historical", "user_reported", "static_reference", "synthetic_fixture"]
    as_of: AwareDatetime | None = None  # When the data describes, if the source says so
    retrieved_at: AwareDatetime | None = None  # When we fetched it


class ToolResult(Record):
    ok: bool
    data: Any = None
    error: ToolError | None = None
    warnings: list[str] = []
    freshness: Freshness | None = None

    @model_validator(mode="after")
    def _error_iff_failed(self):
        if self.ok == (self.error is not None):
            raise ValueError("ok results carry no error; failed results must carry one")
        return self


def tool_ok(data: Any, *, warnings: list[str] | None = None, freshness: Freshness | None = None) -> dict:
    """A successful tool result, as the plain dict the trace and the model receive."""
    result = ToolResult(ok=True, data=data, warnings=warnings or [], freshness=freshness)
    return result.model_dump(mode="json")


def tool_error(code: ErrorCode, message: str, *, retryable: bool, next_step: str) -> dict:
    """A failed tool result. A failure is never disguised as an empty success."""
    error = ToolError(code=code, message=message, retryable=retryable, next_step=next_step)
    return ToolResult(ok=False, error=error).model_dump(mode="json")


# --- Location and the user's request ---


class LocationContext(Record):
    """A position from the browser, a place the user typed, or a geocoded result."""

    point: LatLng | None = None
    place_text: str | None = None
    source: Literal["browser", "user", "geocoded"]
    observed_at: AwareDatetime
    accuracy_m: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _has_position(self):
        if self.point is None and not self.place_text:
            raise ValueError("a location needs coordinates or a typed place")
        if self.source == "browser" and self.point is None:
            raise ValueError("a browser location must include coordinates")
        return self


class RequiredStop(Record):
    stop_id: Id
    place: LocationContext
    dwell_minutes: int = Field(default=0, ge=0)
    window_start: AwareDatetime | None = None  # e.g. opening hours or an appointment
    window_end: AwareDatetime | None = None
    order_index: int | None = Field(default=None, ge=0)  # Set only if the user fixed the order


class AdventureRequest(Record):
    """What the user asked for. Only `start` is required."""

    start: LocationContext
    destination: LocationContext | None = None
    deadline: AwareDatetime | None = None  # Hard arrival time, if stated
    duration_minutes: int | None = Field(default=None, gt=0)
    required_stops: list[RequiredStop] = []
    allowed_modes: list[TravelMode] = ["walk", "transit"]
    theme: str | None = None
    defaulted_fields: list[str] = Field(
        default=[],
        description="Fields filled by app defaults rather than stated by the user",
    )

    @model_validator(mode="after")
    def _defaults_name_real_fields(self):
        unknown = set(self.defaulted_fields) - set(type(self).model_fields)
        if unknown:
            raise ValueError(f"defaulted_fields names unknown fields: {sorted(unknown)}")
        return self


# --- Places and evidence ---


class Claim(Record):
    """One statement about one place, with where it came from."""

    claim_id: Id
    text: str = Field(min_length=1)
    kind: Literal["historical", "architectural", "physical_feature", "access", "other"]
    basis: Literal["source", "field_verified", "user_reported"]
    source_urls: list[HttpUrl] = []
    checked_at: AwareDatetime
    uncertainty: str | None = None

    @model_validator(mode="after")
    def _sourced_claims_cite(self):
        if self.basis == "source" and not self.source_urls:
            raise ValueError(f"claim {self.claim_id} is source-based but cites no URL")
        return self


class PlaceEvidence(Record):
    place_id: Id
    name: str
    address: str | None = None
    point: LatLng | None = None
    claims: list[Claim] = []
    access_notes: str | None = None
    checked_at: AwareDatetime
    uncertainty: str | None = None


# --- Cameras ---


class CameraCheckpoint(Record):
    """Where a participant stands to appear in a DOT camera view.

    `stand_location` is the pedestrian position, never the camera's mounting point.
    """

    checkpoint_id: Id
    camera_id: str  # DOT catalogue id
    stand_location: LatLng
    address: str
    landmark: str
    side_of_street: str
    positioning_instructions: str
    reference_image_asset_id: Id | None = None
    reference_view_notes: str | None = None
    person_region: tuple[float, float, float, float] | None = Field(
        default=None, description="Normalized x0, y0, x1, y1 where a participant appears"
    )
    verification_status: Literal["field_verified", "unverified", "synthetic_fixture"]
    last_field_verified_at: AwareDatetime | None = None
    visibility_notes: str | None = None
    enabled: bool = False
    fallback_checkpoint_id: Id | None = None

    @model_validator(mode="after")
    def _verification_is_dated(self):
        if self.verification_status == "field_verified" and self.last_field_verified_at is None:
            raise ValueError("a field-verified checkpoint needs last_field_verified_at")
        if self.person_region is not None:
            x0, y0, x1, y1 = self.person_region
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("person_region must be normalized with x0 < x1 and y0 < y1")
        return self


# --- Plans ---


class Activity(Record):
    type: Literal["verified_feature", "user_observation", "chat_puzzle", "camera_capture"]
    prompt: str = Field(min_length=1, description="What the user is asked on arrival")
    physical_requirements: list[str] = []
    evidence_ids: list[Id] = []  # Claim ids that support the physical requirements
    answer_rule: str = Field(min_length=1)
    hints: list[str] = []
    fallback: str = Field(min_length=1, description="How the user continues if this cannot be done")

    @model_validator(mode="after")
    def _physical_tasks_need_evidence(self):
        if self.type == "verified_feature" and not self.evidence_ids:
            raise ValueError("a verified_feature activity must reference evidence")
        return self


class Checkpoint(Record):
    checkpoint_id: Id
    place_id: Id
    required_by_user: bool = False
    activity: Activity
    dwell_minutes: int = Field(ge=0)
    story_beat_id: Id | None = None
    camera_checkpoint_id: Id | None = None

    @model_validator(mode="after")
    def _captures_name_a_camera(self):
        if self.activity.type == "camera_capture" and self.camera_checkpoint_id is None:
            raise ValueError(f"{self.checkpoint_id} is a camera capture without a camera checkpoint")
        return self


# Leg endpoints that are not checkpoints. A revision routes from "current_location".
ROUTE_ENDPOINTS = {"start", "current_location", "destination"}


class RouteLeg(Record):
    leg_id: Id
    from_id: Id  # A checkpoint id, or one of ROUTE_ENDPOINTS
    to_id: Id
    allowed_modes: list[TravelMode]
    actual_modes: list[TravelMode]
    depart_at: AwareDatetime | None = None
    arrive_at: AwareDatetime | None = None
    duration_minutes: float = Field(ge=0)
    distance_m: float | None = Field(default=None, ge=0)
    instructions: list[str] = []
    source: str  # Routing provider, or "synthetic_fixture"
    retrieved_at: AwareDatetime
    uncertainty: str | None = None


class StoryBeat(Record):
    """Invented plot. Never presented as historical fact."""

    beat_id: Id
    checkpoint_id: Id | None = None  # None: delivered in chat, not tied to a stop
    summary: str
    reveals: str | None = None


class Story(Record):
    premise: str
    cast: list[str] = []
    solution: str
    beats: list[StoryBeat] = []


class Violation(Record):
    code: str
    message: str
    checkpoint_id: Id | None = None


class ValidationReport(Record):
    """Output of the adventure evaluator. Only plans that pass should become active."""

    ok: bool
    evaluated_at: AwareDatetime
    violations: list[Violation] = []
    estimated_total_minutes: float | None = None


class AdventurePlan(Record):
    plan_id: Id
    version: int = Field(ge=1)
    supersedes_plan_id: Id | None = None
    request: AdventureRequest
    places: list[PlaceEvidence] = []
    checkpoints: list[Checkpoint]  # In visiting order
    legs: list[RouteLeg] = []
    story: Story
    estimated_total_minutes: float = Field(ge=0)
    contingency_minutes: float = Field(default=0, ge=0)
    validation: ValidationReport | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _references_resolve(self):
        checkpoint_ids = [c.checkpoint_id for c in self.checkpoints]
        if len(set(checkpoint_ids)) != len(checkpoint_ids):
            raise ValueError("checkpoint ids must be unique within a plan")

        place_ids = {p.place_id for p in self.places}
        claim_ids = {c.claim_id for p in self.places for c in p.claims}
        beat_ids = {b.beat_id for b in self.story.beats}
        for c in self.checkpoints:
            if c.place_id not in place_ids:
                raise ValueError(f"{c.checkpoint_id} references unknown place {c.place_id}")
            missing = set(c.activity.evidence_ids) - claim_ids
            if missing:
                raise ValueError(f"{c.checkpoint_id} references unknown evidence {sorted(missing)}")
            if c.story_beat_id is not None and c.story_beat_id not in beat_ids:
                raise ValueError(f"{c.checkpoint_id} references unknown story beat {c.story_beat_id}")

        endpoints = set(checkpoint_ids) | ROUTE_ENDPOINTS
        for leg in self.legs:
            if leg.from_id not in endpoints or leg.to_id not in endpoints:
                raise ValueError(f"leg {leg.leg_id} connects unknown stops")
        return self


# --- Progress ---


class UserReport(Record):
    """Something the user told us, kept distinct from verified evidence."""

    checkpoint_id: Id | None = None
    text: str
    reported_at: AwareDatetime


class AdventureState(Record):
    """Authoritative progress for one session. Only state operations change it."""

    version: int = Field(default=0, ge=0)
    status: Literal["idle", "proposed", "active", "completed", "abandoned"] = "idle"
    active_plan_id: Id | None = None
    current_checkpoint_id: Id | None = None
    completed_ids: list[Id] = []
    skipped_ids: list[Id] = []
    blocked_ids: list[Id] = []
    revealed_beat_ids: list[Id] = []
    user_reports: list[UserReport] = []
    latest_location: LocationContext | None = None
    photo_asset_ids: list[Id] = []
    updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _outcomes_are_exclusive(self):
        done, skipped, blocked = set(self.completed_ids), set(self.skipped_ids), set(self.blocked_ids)
        overlap = (done & skipped) | (done & blocked) | (skipped & blocked)
        if overlap:
            raise ValueError(f"a checkpoint has more than one outcome: {sorted(overlap)}")
        if self.status in ("proposed", "active") and self.active_plan_id is None:
            raise ValueError(f"a {self.status} adventure needs an active plan")
        return self


class PhotoAsset(Record):
    """A saved camera still. The bytes live in asset storage, never in state or model context."""

    asset_id: Id
    media_url: str  # App-controlled path such as /media/{asset_id}
    camera_id: str
    checkpoint_id: Id | None = None  # The CameraCheckpoint id; a plan stop links to it via camera_checkpoint_id
    source_url: HttpUrl | None = None
    content_type: str = "image/jpeg"
    byte_size: int = Field(ge=0)
    retrieved_at: AwareDatetime
    frame_time: AwareDatetime | None = None  # Only when the source establishes it
    visibility: Literal["unconfirmed", "user_confirmed_visible", "user_reported_not_visible"] = "unconfirmed"
    provenance: Literal["live_capture", "synthetic_fixture"]


# --- /chat ---


class ChatRequest(Record):
    # Lenient at the HTTP boundary: a client sending an extra field still gets a reply.
    model_config = ConfigDict(extra="ignore")

    message: str
    session_id: str | None = None
    location: LocationContext | None = None
    client_message_id: str | None = Field(
        default=None, max_length=128, description="Lets a retried send return the original reply"
    )


class ToolCallTrace(Record):
    name: str
    args: dict
    result: dict


class ChatResponse(Record):
    response: str
    session_id: str
    tool_calls: list[ToolCallTrace]


# --- Development fixtures ---


class FixtureProvenance(Record):
    kind: Literal["synthetic_fixture"] = "synthetic_fixture"
    description: str
    field_verified: Literal[False] = False  # A fixture is never evidence of real conditions
