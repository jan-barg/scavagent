"""A stand-in for the adapters' shared HTTP session, so no test touches the network."""

from unittest.mock import patch

from integrations import common


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


class FakeHTTP:
    """Answers each request by URL substring from queued (status, body) pairs or exceptions.

    The last queued answer for a URL repeats. Any request without a matching entry fails the test.
    Use as a context manager; `calls` records every request made.
    """

    def __init__(self, answers):
        self.answers = {key: list(queue) for key, queue in answers.items()}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for key, queue in self.answers.items():
            if key in url:
                answer = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(answer, Exception):
                    raise answer
                return FakeResponse(*answer)
        raise AssertionError(f"Unexpected request: {method} {url}")

    def urls(self):
        return [call["url"] for call in self.calls]

    def __enter__(self):
        self._patch = patch.object(common._session, "request", self.request)
        self._patch.start()
        return self

    def __exit__(self, *exc_info):
        self._patch.stop()
