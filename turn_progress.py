"""A running turn's live steps, for GET /progress: which tools have started and finished.

app.run_agent reports each model call and tool to a ProgressReporter. A writer thread copies the steps to the
message's claim (state.Store.set_progress) at most once a second, so any server instance can answer a poll, and a
page reloaded mid-turn sees the steps done so far. Three threads touch a reporter: the request thread (the hooks),
the lookup pool (a concurrent round's done-callbacks), and its writer; one lock guards the steps, and the writer
copies them under it. Progress is only a view of the turn: nothing here may fail one (app.report wraps each hook).
"""

import logging
import threading

logger = logging.getLogger("scavagent")

SUBJECT_CHARS = 60
# The one argument that names what a lookup is about. Every other tool shows no subject: the plan check, the plan
# save, and the state tools carry solutions, answer rules, and hints.
SUBJECT_ARGS = {"geocode_place": "text", "find_places": "query", "find_filming_records": "street",
                "get_weather": "location"}


def subject(name: str, args: dict, result: dict | None = None) -> str | None:
    """A short, safe label for a step: a whitelisted argument, or a researched place's name once it is known."""
    if name == "research_place":
        value = (((result or {}).get("data") or {}).get("evidence") or {}).get("name")
    elif name == "get_transit_arrivals":
        value = " at ".join(str(args[key]) for key in ("line", "station") if args.get(key))
    else:
        value = args.get(SUBJECT_ARGS[name]) if name in SUBJECT_ARGS else None
    value = " ".join(str(value).split()) if value else ""
    return value[:SUBJECT_CHARS] or None


def outcome(name: str, result) -> str:
    """ok or failed. A plan check that ran but did not pass counts as failed, as the page shows it."""
    if not isinstance(result, dict) or result.get("ok") is not True:
        return "failed"
    if name == "evaluate_adventure_plan" and (result.get("data") or {}).get("passes") is False:
        return "failed"
    return "ok"


class ProgressReporter:
    def __init__(self, store, session_id: str, message_id: str, token: str, interval: float = 1.0):
        self._store, self._key, self._token, self._interval = store, (session_id, message_id), token, interval
        self._lock = threading.Lock()
        self._steps: dict[int, dict] = {}  # By position in the turn's tool_calls
        self._args: dict[int, dict] = {}  # Kept to name a step once its result is known; never written
        self._phase: str | None = None
        self._round = 0
        self._changes = self._written = 0
        self._stopped = threading.Event()
        self._writer = threading.Thread(target=self._run, name="progress-writer", daemon=True)

    def start(self) -> "ProgressReporter":
        self._writer.start()
        return self

    # --- Hooks (app.run_agent and app.lookups_at_once) ---

    def thinking(self) -> None:
        with self._lock:
            self._phase = "thinking"
            self._changes += 1

    def new_round(self) -> None:
        with self._lock:
            self._round += 1

    def started(self, i: int, name: str, args: dict, parallel: bool = False) -> None:
        with self._lock:
            self._steps[i] = {"i": i, "round": self._round, "parallel": parallel, "name": name,
                              "subject": subject(name, args), "status": "running"}
            self._args[i] = args
            self._phase = "tools"
            self._changes += 1

    def finished(self, i: int, result) -> None:
        with self._lock:
            step = self._steps.get(i)
            if step is None:
                return
            step["status"] = outcome(step["name"], result)
            step["subject"] = subject(step["name"], self._args.get(i) or {}, result) or step["subject"]
            self._changes += 1

    # --- Writing ---

    def snapshot(self) -> dict:
        with self._lock:
            return {"phase": self._phase, "steps": [dict(self._steps[i]) for i in sorted(self._steps)]}

    def flush(self) -> None:
        """Write the steps if they changed since the last write. Only the writer thread (or a test) calls this."""
        with self._lock:
            if self._changes == self._written:
                return
            changes = self._changes
            data = {"phase": self._phase, "steps": [dict(self._steps[i]) for i in sorted(self._steps)]}
        try:
            self._store.set_progress(*self._key, self._token, data)
        except Exception:
            logger.warning("Couldn't write turn progress", exc_info=True)
        self._written = changes  # A failed write is not retried; the next change writes again

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            self.flush()

    def stop(self) -> None:
        """Stop the writer and wait for a write in flight, so none lands after the claim is released."""
        self._stopped.set()
        if self._writer.is_alive():
            self._writer.join(timeout=5)
