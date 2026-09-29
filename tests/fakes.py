"""A scripted stand-in for the model, so tests exercise the harness without a live call."""

import json
from types import SimpleNamespace

import app as app_module


class FakeMessage(SimpleNamespace):
    """A model reply. Optional thinking_blocks / reasoning_content stand in for Claude's and Kimi's reasoning."""

    def model_dump(self):
        calls = None
        if self.tool_calls:
            calls = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in self.tool_calls
            ]
        reasoning = {k: getattr(self, k) for k in ("thinking_blocks", "reasoning_content") if hasattr(self, k)}
        return {"role": "assistant", "content": self.content, "tool_calls": calls, **reasoning}


def tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def reply(message):
    """What litellm.completion returns around one message."""
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def script(monkeypatch, *steps):
    """Each step is a FakeMessage to return, or an exception to raise."""
    remaining = list(steps)
    seen = []

    def completion(**kwargs):
        seen.append(json.loads(json.dumps(kwargs["messages"])))  # Must be JSON-serializable
        step = remaining.pop(0)
        if isinstance(step, BaseException):
            raise step
        return reply(step)

    monkeypatch.setattr(app_module.litellm, "completion", completion)
    return seen
