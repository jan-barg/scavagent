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


def google_transit_response(ready, *, wait_s=600, ride_s=840, walk_before_s=130, walk_after_s=400,
                            line="A Line", vehicle="SUBWAY", headsign="Far Rockaway-Mott Av"):
    """A Google Routes TRANSIT answer shaped like a live one captured on 2026-09-29 (CPW/86th to W 4 St).

    The train leaves `wait_s` seconds after `ready`; walking before it takes `walk_before_s`.
    """
    from datetime import timedelta, timezone
    from zoneinfo import ZoneInfo

    depart = ready + timedelta(seconds=wait_s)
    arrive = depart + timedelta(seconds=ride_s)

    def utc(t):
        return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def local(t):
        return {"time": {"text": t.astimezone(ZoneInfo("America/New_York")).strftime("%-I:%M %p")},
                "timeZone": "America/New_York"}

    steps = [
        {"travelMode": "WALK", "staticDuration": f"{walk_before_s - 108}s", "distanceMeters": 20},
        {"travelMode": "WALK", "staticDuration": "108s", "distanceMeters": 61,
         "navigationInstruction": {"instructions": "Take entrance Central Park West & 86th St at NW corner"}},
        {"travelMode": "TRANSIT", "staticDuration": f"{ride_s}s", "distanceMeters": 6778,
         "navigationInstruction": {"instructions": f"Subway towards {headsign}"},
         "transitDetails": {
             "stopDetails": {"departureStop": {"name": "86 St"}, "arrivalStop": {"name": "W 4 St-Wash Sq"},
                             "departureTime": utc(depart), "arrivalTime": utc(arrive)},
             "localizedValues": {"departureTime": local(depart), "arrivalTime": local(arrive)},
             "headsign": headsign, "headway": "1200s", "stopCount": 9,
             "transitLine": {"name": "A Train (8 Av Express)", "nameShort": line, "color": "#0062cf",
                             "vehicle": {"name": {"text": "Subway"}, "type": vehicle}}}},
        {"travelMode": "WALK", "staticDuration": f"{walk_after_s}s", "distanceMeters": 519,
         "navigationInstruction": {"instructions": "Take exit West 8 St-Waverly Pl & Avenue of the Americas"}},
    ]
    # Like Google's, `duration` runs from the latest moment that still catches the train: no wait.
    total = walk_before_s + ride_s + walk_after_s
    return {"routes": [{"duration": f"{total}s", "distanceMeters": 7378,
                        "localizedValues": {"transitFare": {"text": "$3.00"}}, "legs": [{"steps": steps}]}]}
