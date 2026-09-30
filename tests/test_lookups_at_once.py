"""The harness runs a round of lookup tools at once, and anything that touches the session in order."""

import time

import app
from fakes import FakeMessage, script, tool_call
from schemas import tool_ok

SLOW = 0.2  # seconds each fake tool takes


def slow_tools(monkeypatch):
    ran = []

    def run_tool(name, args, ctx=None):
        ran.append(name)
        time.sleep(SLOW)
        return tool_ok({"name": name, **args})

    monkeypatch.setattr(app, "run_tool", run_tool)
    return ran


def turn(monkeypatch, *calls):
    script(monkeypatch, FakeMessage(content=None, tool_calls=list(calls)), FakeMessage(content="Done.", tool_calls=None))
    trace, started = [], time.monotonic()
    answer = app.run_agent([{"role": "system", "content": "TEST"}, {"role": "user", "content": "go"}], trace)
    return answer, trace, time.monotonic() - started


def test_research_asked_for_together_runs_at_once_and_keeps_its_order(monkeypatch):
    # Live on Sonnet, three research_place calls in one round took 16 s one after another.
    slow_tools(monkeypatch)
    answer, trace, seconds = turn(monkeypatch, *(tool_call(f"c{i}", "research_place", f'{{"place_id": "wiki:{i}"}}')
                                                 for i in (1, 2, 3)))

    assert answer == "Done."
    assert [t["result"]["data"]["place_id"] for t in trace] == ["wiki:1", "wiki:2", "wiki:3"]
    assert seconds < 2.5 * SLOW  # about one tool's time, not three


def test_a_round_with_a_session_tool_runs_in_order(monkeypatch):
    ran = slow_tools(monkeypatch)
    _, trace, seconds = turn(monkeypatch, tool_call("c1", "get_adventure_state", "{}"),
                             tool_call("c2", "research_place", '{"place_id": "wiki:1"}'))

    assert ran == ["get_adventure_state", "research_place"] and [t["name"] for t in trace] == ran
    assert seconds >= 2 * SLOW
