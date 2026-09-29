"""Durable sessions: conversation, adventure progress, plans, and saved photos.

A turn loads one SessionRecord, changes it only through the operations below, and
saves it with an optimistic version check. Image bytes live in asset storage,
addressed by an unguessable asset id; the record keeps only PhotoAsset metadata.

Backends: SQLite for local development (default), Firestore + Cloud Storage on
Cloud Run (SCAVAGENT_STORE=firestore), and memory for tests.
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol

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
MAX_DRAFTS = 3  # Evaluated plans waiting to be saved
# The whole record is one JSON string in one Firestore document, which holds at most 1 MiB. A planning turn
# stores about 30 kB three times over (model messages, chat transcript, cached reply), so a long adventure
# reaches the limit well before the count caps above. trim() keeps the record under this budget.
MAX_RECORD_BYTES = 800_000
MAX_DRAFT_BYTES = 200_000  # One evaluated plan larger than this is refused rather than stored
MIN_KEPT_REPLIES = 3  # Replies kept for resends even when trimming for size
COMPACT_BYTES = 4_000  # Last resort: a stored tool argument or result bigger than this becomes a short note
# A claim older than this belongs to a turn that has stopped: app.run_agent starts no model call after
# its own shorter deadline. (Cloud Run's request timeout does not stop the code, so it cannot be relied on.)
IN_FLIGHT_TIMEOUT = timedelta(minutes=5)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stored_bytes(value) -> int:
    """UTF-8 bytes, as Firestore counts them (a character can take up to four)."""
    return len((value if isinstance(value, str) else json.dumps(value)).encode())


class RecordTooLarge(Exception):
    """The session cannot fit one Firestore document even after trimming."""


class EvaluatedDraft(Record):
    """A plan evaluate_adventure_plan checked in this session, waiting for save_adventure_plan."""

    kind: Literal["new", "revision"]
    plan: AdventurePlan
    waived_required_ids: list[str] = []


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
    # client_message_id -> {camera checkpoint id: asset id} for photos taken while answering a message that has
    # no stored reply yet. If that turn dies and the message runs again, the camera tool reuses the photo.
    captures: dict[str, dict[str, str]] = {}
    # draft id -> an evaluated plan not saved yet, oldest first. Kept here rather than in process memory,
    # so a save still finds it after a restart or on another Cloud Run instance.
    drafts: dict[str, EvaluatedDraft] = {}

    @classmethod
    def new(cls, session_id: str) -> "SessionRecord":
        now = utc_now()
        return cls(session_id=session_id, created_at=now, updated_at=now)

    def remember_draft(self, draft_id: str, draft: EvaluatedDraft) -> bool:
        """Keep an evaluated plan for save_adventure_plan. False, and nothing kept, if it alone is too large."""
        if _stored_bytes(draft.model_dump_json()) > MAX_DRAFT_BYTES:
            return False
        self.drafts = {**{k: v for k, v in self.drafts.items() if k != draft_id}, draft_id: draft}
        while len(self.drafts) > MAX_DRAFTS:
            self.drafts.pop(next(iter(self.drafts)))
        return True

    def remember_reply(self, client_message_id: str, reply: dict) -> None:
        self.replies[client_message_id] = reply
        self.captures.pop(client_message_id, None)  # A resend now replays the reply; it never runs again
        while len(self.replies) > MAX_CACHED_REPLIES:
            self.replies.pop(next(iter(self.replies)))

    def size(self) -> int:
        return _stored_bytes(self.model_dump_json())

    def trim(self) -> None:
        """Bound stored history by count and by size, or raise RecordTooLarge rather than leave it oversized.

        History is cut only at a user message, so tool calls keep their results. Over the size budget the
        cheapest losses come first: unsaved drafts (the model can evaluate again), older cached replies, the
        oldest model turns, the oldest chat entries, superseded plans, and finally the bulk of stored tool
        arguments and results. The active plan, photos, and progress are never dropped.
        """
        if len(self.messages) > MAX_STORED_MESSAGES:
            self._drop_messages_before(len(self.messages) - MAX_STORED_MESSAGES)
        self.transcript = self.transcript[-MAX_TRANSCRIPT_ENTRIES:]
        for shrink in (self._drop_oldest_draft, self._drop_oldest_reply, self._drop_oldest_turn,
                       self._drop_oldest_chat_entries, self._drop_superseded_plan, self._compact_tool_payloads):
            while self.size() > MAX_RECORD_BYTES and shrink():
                pass
        if self.size() > MAX_RECORD_BYTES:
            raise RecordTooLarge(f"{self.size()} bytes after trimming")

    def _drop_messages_before(self, cut: int) -> None:
        while cut < len(self.messages) and self.messages[cut].get("role") != "user":
            cut += 1
        self.messages = self.messages[cut:]

    def _drop_oldest_draft(self) -> bool:
        return bool(self.drafts) and self.drafts.pop(next(iter(self.drafts))) is not None

    def _drop_oldest_reply(self) -> bool:
        return len(self.replies) > MIN_KEPT_REPLIES and self.replies.pop(next(iter(self.replies))) is not None

    def _drop_oldest_turn(self) -> bool:
        if not any(m.get("role") == "user" for m in self.messages[1:]):
            return False
        self._drop_messages_before(1)
        return True

    def _drop_oldest_chat_entries(self) -> bool:
        if len(self.transcript) <= 2:
            return False
        self.transcript = self.transcript[2:]
        return True

    def _drop_superseded_plan(self) -> bool:
        old = next((plan_id for plan_id in self.plans if plan_id != self.adventure.active_plan_id), None)
        return old is not None and self.plans.pop(old) is not None

    def _compact_tool_payloads(self) -> bool:
        """Replace big stored tool arguments and results with a note; the reply already sent kept them."""
        def note(value):
            return {"omitted": f"{_stored_bytes(value)} bytes not stored"}

        changed = False
        for message in self.messages:
            if message.get("role") == "tool" and _stored_bytes(message.get("content") or "") > COMPACT_BYTES:
                message["content"], changed = json.dumps(note(message["content"])), True
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                if _stored_bytes(function.get("arguments") or "") > COMPACT_BYTES:
                    function["arguments"], changed = json.dumps(note(function["arguments"])), True
        traces = [c for entry in self.transcript for c in entry.get("tool_calls") or []]
        traces += [c for reply in self.replies.values() for c in reply.get("tool_calls") or []]
        for call in traces:
            if _stored_bytes(call.get("args")) > COMPACT_BYTES:
                call["args"], changed = note(call["args"]), True
            result = call.get("result")
            if isinstance(result, dict) and _stored_bytes(result) > COMPACT_BYTES:
                # Keep ok and error: the chat history shows each tool's outcome.
                call["result"], changed = {"ok": result.get("ok"), "error": result.get("error"), **note(result)}, True
        return changed


# --- Storage backends ---


class VersionConflict(Exception):
    """Another turn saved this session after we loaded it."""


class Store(Protocol):
    def load(self, session_id: str) -> SessionRecord | None: ...
    def save(self, record: SessionRecord) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def claim(self, session_id: str, message_id: str, now: datetime) -> str | None: ...
    def release(self, session_id: str, message_id: str, token: str) -> None: ...
    def put_asset(self, asset_id: str, data: bytes, content_type: str, session_id: str) -> None: ...
    def get_asset(self, asset_id: str) -> tuple[bytes, str] | None: ...


# Claims mark a client_message_id whose turn is running, so a resend does not start a second turn.
# They live apart from the session record: claiming one message must not make another turn's save stale.
# claim() returns a token, or None while a live turn holds the message. release() drops the claim only if
# it still holds that token, so a turn that overran its claim cannot free the claim of the turn that took over.


def _dump(record: SessionRecord) -> str:
    return record.model_dump_json()


def _next_version(record: SessionRecord, stored_version: int | None) -> SessionRecord:
    """The record as it will be stored. The caller's copy changes only after the write commits,
    so a retried or failed write still compares against the version that was loaded."""
    if (stored_version or 0) != record.storage_version:
        raise VersionConflict(record.session_id)
    return record.model_copy(update={"storage_version": record.storage_version + 1, "updated_at": utc_now()})


def _saved(record: SessionRecord, stored: SessionRecord) -> None:
    record.storage_version, record.updated_at = stored.storage_version, stored.updated_at


def _claim_is_live(started_at: datetime | None, now: datetime) -> bool:
    return started_at is not None and now - started_at < IN_FLIGHT_TIMEOUT


class MemoryStore:
    def __init__(self):
        self._sessions: dict[str, str] = {}
        self._claims: dict[tuple[str, str], tuple[datetime, str]] = {}  # -> (started_at, token)
        self._assets: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def load(self, session_id):
        raw = self._sessions.get(session_id)
        return SessionRecord.model_validate_json(raw) if raw else None

    def save(self, record):
        with self._lock:
            raw = self._sessions.get(record.session_id)
            new = _next_version(record, SessionRecord.model_validate_json(raw).storage_version if raw else None)
            self._sessions[record.session_id] = _dump(new)
        _saved(record, new)

    def delete(self, session_id):
        self._sessions.pop(session_id, None)

    def claim(self, session_id, message_id, now):
        with self._lock:
            held = self._claims.get((session_id, message_id))
            if held and _claim_is_live(held[0], now):
                return None
            token = uuid.uuid4().hex
            self._claims[(session_id, message_id)] = (now, token)
            return token

    def release(self, session_id, message_id, token):
        with self._lock:
            if self._claims.get((session_id, message_id), (None, None))[1] == token:
                del self._claims[(session_id, message_id)]

    def put_asset(self, asset_id, data, content_type, session_id):
        self._assets[asset_id] = (data, content_type)

    def get_asset(self, asset_id):
        return self._assets.get(asset_id)


class SqliteStore:
    """One shared connection; every use holds the lock so no statement joins another thread's transaction."""

    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, version INTEGER, data TEXT)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS claims (session_id TEXT, message_id TEXT, started_at TEXT, token TEXT, "
            "PRIMARY KEY (session_id, message_id))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, session_id TEXT, content_type TEXT, data BLOB)"
        )

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _query(self, sql, params):
        with self._lock:
            return self._db.execute(sql, params).fetchone()

    def load(self, session_id):
        row = self._query("SELECT data FROM sessions WHERE id = ?", (session_id,))
        return SessionRecord.model_validate_json(row[0]) if row else None

    def save(self, record):
        with self._transaction():
            row = self._db.execute("SELECT version FROM sessions WHERE id = ?", (record.session_id,)).fetchone()
            new = _next_version(record, row[0] if row else None)
            self._db.execute(
                "INSERT OR REPLACE INTO sessions (id, version, data) VALUES (?, ?, ?)",
                (record.session_id, new.storage_version, _dump(new)),
            )
        _saved(record, new)

    def delete(self, session_id):
        with self._transaction():
            self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def claim(self, session_id, message_id, now):
        with self._transaction():
            row = self._db.execute(
                "SELECT started_at FROM claims WHERE session_id = ? AND message_id = ?", (session_id, message_id)
            ).fetchone()
            if row and _claim_is_live(datetime.fromisoformat(row[0]), now):
                return None
            token = uuid.uuid4().hex
            self._db.execute(
                "INSERT OR REPLACE INTO claims (session_id, message_id, started_at, token) VALUES (?, ?, ?, ?)",
                (session_id, message_id, now.isoformat(), token),
            )
            return token

    def release(self, session_id, message_id, token):
        with self._transaction():
            self._db.execute(
                "DELETE FROM claims WHERE session_id = ? AND message_id = ? AND token = ?",
                (session_id, message_id, token),
            )

    def put_asset(self, asset_id, data, content_type, session_id):
        with self._transaction():
            self._db.execute(
                "INSERT OR REPLACE INTO assets (id, session_id, content_type, data) VALUES (?, ?, ?, ?)",
                (asset_id, session_id, content_type, data),
            )

    def get_asset(self, asset_id):
        row = self._query("SELECT data, content_type FROM assets WHERE id = ?", (asset_id,))
        return (bytes(row[0]), row[1]) if row else None


class FirestoreStore:
    """Sessions in Firestore (one document each, JSON-encoded), photos in Cloud Storage."""

    def __init__(self, bucket: str, collection: str = "sessions", database: str | None = None):
        from google.cloud import firestore, storage

        self._firestore = firestore
        self._db = firestore.Client(database=database) if database else firestore.Client()
        self._sessions = self._db.collection(collection)
        self._claims = self._db.collection(f"{collection}_claims")
        self._bucket = storage.Client().bucket(bucket)

    def load(self, session_id):
        snapshot = self._sessions.document(session_id).get()
        return SessionRecord.model_validate_json(snapshot.get("data")) if snapshot.exists else None

    def save(self, record):
        doc = self._sessions.document(record.session_id)

        # Firestore reruns this function when the commit loses a race, so it must not change `record`.
        @self._firestore.transactional
        def write(transaction):
            snapshot = doc.get(transaction=transaction)
            new = _next_version(record, snapshot.get("version") if snapshot.exists else None)
            transaction.set(doc, {"version": new.storage_version, "data": _dump(new), "updated_at": new.updated_at})
            return new

        _saved(record, write(self._db.transaction()))

    def delete(self, session_id):
        self._sessions.document(session_id).delete()

    def _claim_doc(self, session_id, message_id):
        # Message ids come from clients; hashing keeps any '/' out of the document id.
        return self._claims.document(hashlib.sha256(f"{session_id}\n{message_id}".encode()).hexdigest())

    def claim(self, session_id, message_id, now):
        doc, token = self._claim_doc(session_id, message_id), uuid.uuid4().hex

        @self._firestore.transactional
        def take(transaction):
            snapshot = doc.get(transaction=transaction)
            if snapshot.exists and _claim_is_live(snapshot.get("started_at"), now):
                return None
            transaction.set(doc, {"session_id": session_id, "started_at": now, "token": token})
            return token

        return take(self._db.transaction())

    def release(self, session_id, message_id, token):
        doc = self._claim_doc(session_id, message_id)

        @self._firestore.transactional
        def drop(transaction):
            snapshot = doc.get(transaction=transaction)
            if snapshot.exists and snapshot.get("token") == token:
                transaction.delete(doc)

        drop(self._db.transaction())

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
    message_id: str | None = None  # The client_message_id this turn answers, when the client sent one

    def saved_capture(self, checkpoint_id: str) -> PhotoAsset | None:
        """The photo already taken at this camera checkpoint while answering this same message, if any."""
        asset_id = self.record.captures.get(self.message_id, {}).get(checkpoint_id) if self.message_id else None
        return self.record.photos.get(asset_id) if asset_id else None

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
        before = self.record.photos, self.record.adventure, self.record.captures
        self.record.photos = {**self.record.photos, asset_id: photo}
        if self.message_id and checkpoint_id:
            taken = {**self.record.captures.get(self.message_id, {}), checkpoint_id: asset_id}
            self.record.captures = {**self.record.captures, self.message_id: taken}
        _commit(self, self.record.adventure.model_copy(
            update={"photo_asset_ids": [*self.record.adventure.photo_asset_ids, asset_id]}
        ))
        try:
            self.record.trim()  # Drafts added this turn could otherwise push the record past one document
            self.store.save(self.record)
        except Exception:
            # Not saved: detach it here too, so the end of the turn cannot keep a photo the tool reported as failed.
            self.record.photos, self.record.adventure, self.record.captures = before
            raise
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
    if outcome == "completed" and checkpoint_id != state.current_checkpoint_id:
        # Stops are routed in order; completing a later one would leave the route and the story out of step.
        current = state.current_checkpoint_id
        return tool_error(
            "INVALID_ARGUMENT",
            f"{checkpoint_id} is not the current checkpoint ({current or 'none'}).",
            retryable=False,
            next_step=f"Complete or skip {current} first." if current else "Every checkpoint is already resolved.",
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
    if status == "completed" and state.current_checkpoint_id is not None:
        return tool_error("INVALID_ARGUMENT", f"Checkpoint {state.current_checkpoint_id} and any after it are unresolved.",
                          retryable=False,
                          next_step="Complete or skip the remaining stops, or abandon_adventure if the user is stopping early.")
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
