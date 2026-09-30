"""The check scripts run to their report with a faked chat: no crash from a shared helper they import.

An earlier attempt to share the report helper named it `finish`, which a local variable in
acceptance_checks.main() shadowed; the run crashed at its very end. These run each main() through.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import acceptance_checks  # noqa: E402
import camera_checks  # noqa: E402
import guiding_checks  # noqa: E402


@pytest.fixture
def quiet_chat(monkeypatch, tmp_path):
    """Every message gets an empty reply; the transcript goes to a temporary file."""
    acceptance_checks.results.clear()
    acceptance_checks.transcript.clear()

    def chat(base, session, message):
        reply = {"response": "", "session_id": session, "tool_calls": []}
        acceptance_checks.transcript.append({"session_id": session, "message": message, "reply": reply})
        return reply

    for script in (acceptance_checks, guiding_checks, camera_checks):
        monkeypatch.setattr(script, "chat", chat)
    out = tmp_path / "transcript.json"
    monkeypatch.setattr(sys, "argv", ["script", "http://127.0.0.1:9", "--out", str(out)])
    return out


@pytest.mark.parametrize("script", [acceptance_checks, guiding_checks, camera_checks])
def test_each_script_reports_and_exits_without_crashing(script, quiet_chat):
    with pytest.raises(SystemExit) as exit_:
        script.main()

    assert exit_.value.code == 1  # empty replies fail checks; what matters is reaching the report
    assert acceptance_checks.results and json.loads(quiet_chat.read_text())


def test_arguments_take_a_base_url_and_an_optional_transcript_path():
    assert acceptance_checks.arguments([]) == ("http://127.0.0.1:8000", None)
    assert acceptance_checks.arguments(["https://example.run.app/", "--out", "t.json"]) == ("https://example.run.app", "t.json")
    assert acceptance_checks.arguments(["--out", "t.json", "http://localhost:8000"]) == ("http://localhost:8000", "t.json")


def test_the_replan_scenario_reports_and_exits_without_crashing(quiet_chat, monkeypatch):
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--scenario", "replan"])
    with pytest.raises(SystemExit) as exit_:
        guiding_checks.main()

    assert exit_.value.code == 1
    assert [name for name, _ in acceptance_checks.results] == ["Plan passed and saved"]


def test_a_draft_includes_fields_sent_beside_it():
    # A live Flash-Lite call put user_stated next to "draft"; the tool merges it in, and so must the checks.
    call = {"args": {"user_stated": ["stop_count"], "draft": {"kind": "new", "stops": []}}}
    assert acceptance_checks.draft_of(call) == {"user_stated": ["stop_count"], "kind": "new", "stops": []}
    assert acceptance_checks.draft_of({"args": {"kind": "check"}}) == {"kind": "check"}
