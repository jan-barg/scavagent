"""Durable sessions: conversation, adventure progress, plans, and saved photos.

A turn loads one SessionRecord, changes it only through the operations below, and
saves it with an optimistic version check. Image bytes live in asset storage,
addressed by an unguessable asset id; the record keeps only PhotoAsset metadata.

Backends: SQLite for local development (default), Firestore + Cloud Storage on
Cloud Run (SCAVAGENT_STORE=firestore), and memory for tests.
"""

import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from pydantic import Field

from schemas import (
    AdventurePlan,
    AdventureState,
    Checkpoint,
    LocationContext,
    PhotoAsset,
    Record,
    UserReport,
    tool_error,
    tool_ok,
)

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MAX_STORED_MESSAGES = 200
MAX_TRANSCRIPT_ENTRIES = 200
MAX_CACHED_REPLIES = 20
# Cloud Run's default request timeout. A claim older than this belongs to a turn that can no longer finish,
# so its message may run again. Keep the service's --timeout at or below this.
IN_FLIGHT_TIMEOUT = timedelta(minutes=5)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionRecord(Record):
    session_id: str
    storage_version: int = 0  # Bumped by every save; guards concurrent turns
    created_at: datetime
    updated_at: datetime
    messages: list[dict] = []  # Model conversation, without the system prompt
    transcript: list[dict] = []  # What the chat shows: role, text, tool_calls, at
    adventure: AdventureState = Field(default_factory=AdventureState)
    plans: dict[str, AdventurePlan] = {}
    photos: dict[str, PhotoAsset] = {}
    replies: dict[str, dict] = {}  # client_message_id -> the ChatResponse already sent
    in_flight: dict[str, datetime] = {}  # client_message_id -> when a turn started answering it

    @classmethod
    def new(cls, session_id: str) -> "SessionRecord":
        now = utc_now()
        return cls(session_id=session_id, created_at=now, updated_at=now)

    def claim(self, client_message_id: str, now: datetime) -> bool:
        """Mark a message as being answered. False while a live turn already has it."""
        self.in_flight = {k: t for k, t in self.in_flight.items() if now - t < IN_FLIGHT_TIMEOUT}
        if client_message_id in self.in_flight:
            return False
        self.in_flight[client_message_id] = now
        return True

    def remember_reply(self, client_message_id: str, reply: dict) -> None:
        self.replies[client_message_id] = reply
        self.in_flight.pop(client_message_id, None)
        while len(self.replies) > MAX_CACHED_REPLIES:
            self.replies.pop(next(iter(self.replies)))

    def trim(self) -> None:
        """Bound stored history, cutting only at a user message so tool calls keep their results."""
        if len(self.messages) > MAX_STORED_MESSAGES:
            cut = len(self.messages) - MAX_STORED_MESSAGES
            while cut < len(self.messages) and self.messages[cut].get("role") != "user":
                cut += 1
            self.messages = self.messages[cut:]
        self.transcript = self.transcript[-MAX_TRANSCRIPT_ENTRIES:]


# --- Storage backends ---


class VersionConflict(Exception):
    """Another turn saved this session after we loaded it."""


class Store(Protocol):
    def load(self, session_id: str) -> SessionRecord | None: ...
    def save(self, record: SessionRecord) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def put_asset(self, asset_id: str, data: bytes, content_type: str, session_id: str) -> None: ...
    def get_asset(self, asset_id: str) -> tuple[bytes, str] | None: ...


def _dump(record: SessionRecord) -> str:
    return record.model_dump_json()


def _check_and_bump(record: SessionRecord, stored_version: int | None) -> None:
    if (stored_version or 0) != record.storage_version:
        raise VersionConflict(record.session_id)
    record.storage_version += 1
    record.updated_at = utc_now()


class MemoryStore:
    def __init__(self):
        self._sessions: dict[str, str] = {}
        self._assets: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def load(self, session_id):
        raw = self._sessions.get(session_id)
        return SessionRecord.model_validate_json(raw) if raw else None

    def save(self, record):
        with self._lock:
            raw = self._sessions.get(record.session_id)
            stored = SessionRecord.model_validate_json(raw).storage_version if raw else None
            _check_and_bump(record, stored)
            self._sessions[record.session_id] = _dump(record)

    def delete(self, session_id):
        self._sessions.pop(session_id, None)

    def put_asset(self, asset_id, data, content_type, session_id):
        self._assets[asset_id] = (data, content_type)

    def get_asset(self, asset_id):
        return self._assets.get(asset_id)


class SqliteStore:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, version INTEGER, data TEXT)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, session_id TEXT, content_type TEXT, data BLOB)"
        )

    def load(self, session_id):
        row = self._db.execute("SELECT data FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return SessionRecord.model_validate_json(row[0]) if row else None

    def save(self, record):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT version FROM sessions WHERE id = ?", (record.session_id,)).fetchone()
                _check_and_bump(record, row[0] if row else None)
                self._db.execute(
                    "INSERT OR REPLACE INTO sessions (id, version, data) VALUES (?, ?, ?)",
                    (record.session_id, record.storage_version, _dump(record)),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def delete(self, session_id):
        self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def put_asset(self, asset_id, data, content_type, session_id):
        self._db.execute(
            "INSERT OR REPLACE INTO assets (id, session_id, content_type, data) VALUES (?, ?, ?, ?)",
            (asset_id, session_id, content_type, data),
        )

    def get_asset(self, asset_id):
        row = self._db.execute("SELECT data, content_type FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return (bytes(row[0]), row[1]) if row else None


class FirestoreStore:
    """Sessions in Firestore (one document each, JSON-encoded), photos in Cloud Storage."""

    def __init__(self, bucket: str, collection: str = "sessions", database: str | None = None):
        from google.cloud import firestore, storage

        self._firestore = firestore
        self._db = firestore.Client(database=database) if database else firestore.Client()
        self._sessions = self._db.collection(collection)
        self._bucket = storage.Client().bucket(bucket)

    def load(self, session_id):
        snapshot = self._sessions.document(session_id).get()
        return SessionRecord.model_validate_json(snapshot.get("data")) if snapshot.exists else None

    def save(self, record):
        doc = self._sessions.document(record.session_id)

        @self._firestore.transactional
        def write(transaction):
            snapshot = doc.get(transaction=transaction)
            _check_and_bump(record, snapshot.get("version") if snapshot.exists else None)
            transaction.set(
                doc, {"version": record.storage_version, "data": _dump(record), "updated_at": record.updated_at}
            )

        write(self._db.transaction())

    def delete(self, session_id):
        self._sessions.document(session_id).delete()

    def put_asset(self, asset_id, data, content_type, session_id):
        blob = self._bucket.blob(f"assets/{asset_id}")
        blob.metadata = {"session_id": session_id}
        blob.upload_from_string(data, content_type=content_type)

    def get_asset(self, asset_id):
        blob = self._bucket.get_blob(f"assets/{asset_id}")
        return (blob.download_as_bytes(), blob.content_type) if blob else None


def store_from_env() -> Store:
    kind = os.environ.get("SCAVAGENT_STORE", "sqlite")
    if kind == "firestore":
        return FirestoreStore(
            bucket=os.environ["SCAVAGENT_ASSET_BUCKET"],
            database=os.environ.get("SCAVAGENT_FIRESTORE_DATABASE"),
        )
    if kind == "memory":
        return MemoryStore()
    return SqliteStore(os.environ.get("SCAVAGENT_SQLITE_PATH", ".data/scavagent.db"))


# --- The per-turn context tools receive ---


@dataclass
class ToolContext:
    """Bound by the server to the current session. The model never chooses it."""

    record: SessionRecord
    store: Store
    now: Callable[[], datetime] = field(default=utc_now)

    def save_asset(
        self,
        data: bytes,
        content_type: str,
        camera_id: str,
        checkpoint_id: str | None,
        source_url: str | None,
        retrieved_at: datetime,
        frame_time: datetime | None = None,
        provenance: str = "live_capture",
    ) -> PhotoAsset:
        """Store image bytes, attach the photo to this session's progress, and save the session now.

        Saving here, not at the end of the turn, keeps a captured frame even if the turn dies before
        it replies. The turn adds its messages only when it finishes, so this save never holds half a turn.
        """
        asset_id = uuid.uuid4().hex
        self.store.put_asset(asset_id, data, content_type, self.record.session_id)
        photo = PhotoAsset(
            asset_id=asset_id,
            media_url=f"/media/{asset_id}",
            camera_id=camera_id,
            checkpoint_id=checkpoint_id,
            source_url=source_url,
            content_type=content_type,
            byte_size=len(data),
            retrieved_at=retrieved_at,
            frame_time=frame_time,
            provenance=provenance,
        )
        self.record.photos[asset_id] = photo
        _commit(self, self.record.adventure.model_copy(
            update={"photo_asset_ids": [*self.record.adventure.photo_asset_ids, asset_id]}
        ))
        self.store.save(self.record)
        return photo


# --- State operations ---
# Each returns a tool result dict. Only these change AdventureState.


def _commit(ctx: ToolContext, new_state: AdventureState) -> AdventureState:
    new_state = AdventureState.model_validate(
        {**new_state.model_dump(), "version": ctx.record.adventure.version + 1, "updated_at": ctx.now()}
    )
    ctx.record.adventure = new_state
    return new_state


def _version_conflict(ctx: ToolContext, expected_version: int | None) -> dict | None:
    current = ctx.record.adventure.version
    if expected_version is not None and expected_version != current:
        return tool_error(
            "STATE_VERSION_CONFLICT",
            f"Expected state version {expected_version}, but it is now {current}.",
            retryable=True,
            next_step="Call get_adventure_state, then retry against the current version.",
        )
    return None


def active_plan(record: SessionRecord) -> AdventurePlan | None:
    plan_id = record.adventure.active_plan_id
    return record.plans.get(plan_id) if plan_id else None


def _resolved(state: AdventureState) -> set[str]:
    return set(state.completed_ids) | set(state.skipped_ids) | set(state.blocked_ids)


def _next_checkpoint(plan: AdventurePlan, state: AdventureState) -> str | None:
    done = _resolved(state)
    return next((c.checkpoint_id for c in plan.checkpoints if c.checkpoint_id not in done), None)


def save_plan(
    ctx: ToolContext,
    plan: AdventurePlan,
    *,
    activate: bool = False,
    waived_required_ids: list[str] = (),
    expected_version: int | None = None,
) -> dict:
    """Store a new plan, or a revision replacing the remaining route of the current one.

    A revision keeps completed checkpoints unchanged, keeps revealed story beats, and
    keeps every unresolved required stop unless the user explicitly waived it.
    """
    if conflict := _version_conflict(ctx, expected_version):
        return conflict
    record, state = ctx.record, ctx.record.adventure
    if plan.plan_id in record.plans:
        return tool_error(
            "INVALID_ARGUMENT",
            f"Plan {plan.plan_id} already exists; plans are immutable.",
            retryable=False,
            next_step="Save a revision with a new plan_id and supersedes_plan_id set.",
        )
    is_revision = state.status == "active"
    if (activate or is_revision) and not (plan.validation and plan.validation.ok):
        return tool_error(
            "PLAN_INFEASIBLE",
            "Only a plan that passed evaluation can become active.",
            retryable=True,
            next_step="Run evaluate_adventure_plan, fix its violations, and attach the passing report.",
        )

    if is_revision:
        problem = _revision_problem(active_plan(record), plan, state, set(waived_required_ids))
        if problem:
            return tool_error("INVALID_ARGUMENT", problem, retryable=False, next_step="Revise the plan and try again.")
        base = state
    else:
        # A fresh adventure: earlier progress belonged to another plan. Photos and location stay.
        base = AdventureState(
            version=state.version, latest_location=state.latest_location, photo_asset_ids=state.photo_asset_ids
        )

    record.plans[plan.plan_id] = plan
    status = "active" if activate or is_revision else "proposed"
    new_state = base.model_copy(update={"status": status, "active_plan_id": plan.plan_id})
    new_state = new_state.model_copy(update={"current_checkpoint_id": _next_checkpoint(plan, new_state)})
    new_state = _commit(ctx, new_state)
    return tool_ok({"plan_id": plan.plan_id, "status": new_state.status, "state_version": new_state.version})


def _revision_problem(old: AdventurePlan, new: AdventurePlan, state: AdventureState, waived: set[str]) -> str | None:
    if new.supersedes_plan_id != old.plan_id:
        return f"A revision must set supersedes_plan_id to the active plan {old.plan_id}."
    old_stops: dict[str, Checkpoint] = {c.checkpoint_id: c for c in old.checkpoints}
    new_stops = {c.checkpoint_id: c for c in new.checkpoints}
    for done in state.completed_ids:
        if done in old_stops and new_stops.get(done) != old_stops[done]:
            return f"Completed checkpoint {done} must stay in the revision unchanged."
    for c in old.checkpoints:
        unresolved = c.checkpoint_id not in _resolved(state)
        if c.required_by_user and unresolved and c.checkpoint_id not in new_stops and c.checkpoint_id not in waived:
            return f"Required stop {c.checkpoint_id} was dropped without the user waiving it."
    kept_beats = {b.beat_id for b in new.story.beats}
    lost = [b for b in state.revealed_beat_ids if b not in kept_beats]
    if lost:
        return f"Revealed story beats must stay in the story: {lost}."
    return None


def _checkpoint_or_error(ctx: ToolContext, checkpoint_id: str) -> tuple[AdventurePlan, Checkpoint] | dict:
    plan = active_plan(ctx.record)
    if plan is None or ctx.record.adventure.status not in ("proposed", "active"):
        return tool_error(
            "INVALID_ARGUMENT",
            "There is no adventure in progress.",
            retryable=False,
            next_step="Plan an adventure first.",
        )
    checkpoint = next((c for c in plan.checkpoints if c.checkpoint_id == checkpoint_id), None)
    if checkpoint is None:
        ids = [c.checkpoint_id for c in plan.checkpoints]
        return tool_error(
            "INVALID_ARGUMENT",
            f"Checkpoint {checkpoint_id} is not in the active plan.",
            retryable=False,
            next_step=f"Use one of: {', '.join(ids)}.",
        )
    return plan, checkpoint


def resolve_checkpoint(
    ctx: ToolContext,
    checkpoint_id: str,
    outcome: str,
    *,
    note: str | None = None,
    user_waived_required: bool = False,
    expected_version: int | None = None,
) -> dict:
    """Mark a checkpoint completed, skipped, or blocked. Repeating the same outcome is a no-op."""
    if conflict := _version_conflict(ctx, expected_version):
        return conflict
    found = _checkpoint_or_error(ctx, checkpoint_id)
    if isinstance(found, dict):
        return found
    plan, checkpoint = found
    state = ctx.record.adventure
    field_name = {"completed": "completed_ids", "skipped": "skipped_ids", "blocked": "blocked_ids"}[outcome]

    if checkpoint_id in getattr(state, field_name):
        return tool_ok({"checkpoint_id": checkpoint_id, "outcome": outcome, "already_recorded": True,
                        "current_checkpoint_id": state.current_checkpoint_id, "state_version": state.version})
    if checkpoint_id in _resolved(state):
        return tool_error(
            "INVALID_ARGUMENT",
            f"Checkpoint {checkpoint_id} already has a different outcome.",
            retryable=False,
            next_step="Continue with the current checkpoint.",
        )
    if outcome == "skipped" and checkpoint.required_by_user and not user_waived_required:
        return tool_error(
            "INVALID_ARGUMENT",
            f"{checkpoint_id} is a stop the user required.",
            retryable=False,
            next_step="Ask the user to confirm they no longer need this stop, then retry with user_waived_required.",
        )

    update = {field_name: [*getattr(state, field_name), checkpoint_id], "status": "active"}
    if note:
        report = UserReport(checkpoint_id=checkpoint_id, text=note, reported_at=ctx.now())
        update["user_reports"] = [*state.user_reports, report]
    new_state = state.model_copy(update=update)
    new_state = new_state.model_copy(update={"current_checkpoint_id": _next_checkpoint(plan, new_state)})
    new_state = _commit(ctx, new_state)
    return tool_ok({
        "checkpoint_id": checkpoint_id,
        "outcome": outcome,
        "current_checkpoint_id": new_state.current_checkpoint_id,
        "remaining": new_state.current_checkpoint_id is not None,
        "state_version": new_state.version,
    })


def reveal_beat(ctx: ToolContext, beat_id: str, *, expected_version: int | None = None) -> dict:
    if conflict := _version_conflict(ctx, expected_version):
        return conflict
    plan, state = active_plan(ctx.record), ctx.record.adventure
    if plan is None or beat_id not in {b.beat_id for b in plan.story.beats}:
        return tool_error("INVALID_ARGUMENT", f"Story beat {beat_id} is not in the active plan.",
                          retryable=False, next_step="Use a beat_id from get_adventure_state.")
    if beat_id not in state.revealed_beat_ids:
        state = _commit(ctx, state.model_copy(update={"revealed_beat_ids": [*state.revealed_beat_ids, beat_id]}))
    return tool_ok({"beat_id": beat_id, "revealed_beat_ids": state.revealed_beat_ids, "state_version": state.version})


def set_status(ctx: ToolContext, status: str, *, expected_version: int | None = None) -> dict:
    """Start, finish, or abandon the adventure."""
    if conflict := _version_conflict(ctx, expected_version):
        return conflict
    state, plan = ctx.record.adventure, active_plan(ctx.record)
    if plan is None:
        return tool_error("INVALID_ARGUMENT", "There is no adventure plan.", retryable=False,
                          next_step="Plan an adventure first.")
    if status == "active" and not (plan.validation and plan.validation.ok):
        return tool_error("PLAN_INFEASIBLE", "The active plan has not passed evaluation.", retryable=True,
                          next_step="Evaluate the plan and save a passing version before starting.")
    if state.status != status:
        update = {"status": status}
        if status in ("completed", "abandoned"):
            update["current_checkpoint_id"] = None
        state = _commit(ctx, state.model_copy(update=update))
    return tool_ok({"status": state.status, "state_version": state.version})


def set_photo_visibility(ctx: ToolContext, asset_id: str, visibility: str) -> dict:
    photo = ctx.record.photos.get(asset_id)
    if photo is None:
        return tool_error("INVALID_ARGUMENT", f"No saved photo {asset_id} in this session.", retryable=False,
                          next_step="Use an asset_id from get_adventure_state.")
    ctx.record.photos[asset_id] = photo.model_copy(update={"visibility": visibility})
    return tool_ok({"asset_id": asset_id, "visibility": visibility})


def update_location(record: SessionRecord, location: LocationContext) -> None:
    """Keep the newest location the client sent. Not a model action and not versioned."""
    latest = record.adventure.latest_location
    if latest is None or location.observed_at >= latest.observed_at:
        record.adventure = record.adventure.model_copy(update={"latest_location": location})


# --- What the model sees ---


def state_summary(record: SessionRecord) -> dict:
    """A compact view of progress: the current checkpoint in full, the rest as ids."""
    state, plan = record.adventure, active_plan(record)
    summary = {
        "status": state.status,
        "state_version": state.version,
        "latest_location": state.latest_location.model_dump(mode="json") if state.latest_location else None,
        "photos": [
            {"asset_id": p.asset_id, "media_url": p.media_url, "checkpoint_id": p.checkpoint_id,
             "visibility": p.visibility, "retrieved_at": p.retrieved_at.isoformat()}
            for p in record.photos.values()
        ],
    }
    if plan is None:
        return summary
    outcome = {i: "completed" for i in state.completed_ids}
    outcome |= {i: "skipped" for i in state.skipped_ids} | {i: "blocked" for i in state.blocked_ids}
    current = next((c for c in plan.checkpoints if c.checkpoint_id == state.current_checkpoint_id), None)
    summary |= {
        "plan_id": plan.plan_id,
        "plan_version": plan.version,
        "theme": plan.request.theme,
        "deadline": plan.request.deadline.isoformat() if plan.request.deadline else None,
        "destination": plan.request.destination.place_text if plan.request.destination else None,
        "premise": plan.story.premise,
        "checkpoints": [
            {"checkpoint_id": c.checkpoint_id, "required_by_user": c.required_by_user,
             "activity_type": c.activity.type, "camera_checkpoint_id": c.camera_checkpoint_id,
             "outcome": outcome.get(c.checkpoint_id, "pending")}
            for c in plan.checkpoints
        ],
        "current_checkpoint": current.model_dump(mode="json") if current else None,
        "current_place": next(
            (p.model_dump(mode="json") for p in plan.places if current and p.place_id == current.place_id), None
        ),
        "revealed_beats": [b.model_dump(mode="json") for b in plan.story.beats if b.beat_id in state.revealed_beat_ids],
        "next_beat": next(
            (b.model_dump(mode="json") for b in plan.story.beats
             if current and b.beat_id == current.story_beat_id and b.beat_id not in state.revealed_beat_ids),
            None,
        ),
        "solution_if_finished": plan.story.solution if state.current_checkpoint_id is None else None,
    }
    return summary
