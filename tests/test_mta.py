"""get_transit_arrivals against a synthetic GTFS-realtime feed built with the same bindings."""

from datetime import datetime, timezone

import pytest
from google.transit import gtfs_realtime_pb2

from fake_http import FakeHTTP
from integrations import mta
from integrations.mta import get_transit_arrivals

STATIONS = "39hk-dx4f"
ACE = "gtfs-ace"
ALERTS = "subway-alerts.json"

ROWS = [
    {"gtfs_stop_id": "A20", "stop_name": "86 St", "daytime_routes": "C B", "gtfs_latitude": "40.785868",
     "gtfs_longitude": "-73.968916", "north_direction_label": "Uptown", "south_direction_label": "Downtown"},
    {"gtfs_stop_id": "121", "stop_name": "86 St", "daytime_routes": "1", "gtfs_latitude": "40.788644",
     "gtfs_longitude": "-73.976218", "north_direction_label": "Uptown & The Bronx", "south_direction_label": "Downtown"},
    {"gtfs_stop_id": "626", "stop_name": "86 St", "daytime_routes": "4 5 6", "gtfs_latitude": "40.779492",
     "gtfs_longitude": "-73.955589", "north_direction_label": "Uptown & The Bronx", "south_direction_label": "Downtown"},
    {"gtfs_stop_id": "A02", "stop_name": "Inwood-207 St", "daytime_routes": "A", "gtfs_latitude": "40.868072",
     "gtfs_longitude": "-73.919899", "north_direction_label": "", "south_direction_label": "Manhattan"},
    {"gtfs_stop_id": "A32", "stop_name": "W 4 St-Wash Sq", "daytime_routes": "A C E B D F M", "gtfs_latitude": "40.732338",
     "gtfs_longitude": "-74.000495", "north_direction_label": "Uptown", "south_direction_label": "Downtown"},
]
NOW = datetime.now(timezone.utc).timestamp()


def feed(*trips, age_s=20):
    """trips: (route, [(stop_id, seconds from now), ...])"""
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.timestamp = int(NOW - age_s)
    for i, (route, stops) in enumerate(trips):
        entity = message.entity.add(id=str(i))
        entity.trip_update.trip.route_id = route
        for stop_id, offset in stops:
            update = entity.trip_update.stop_time_update.add(stop_id=stop_id)
            update.arrival.time = int(NOW + offset)
    return (200, message.SerializeToString())


ALERT_BODY = {"header": {"timestamp": int(NOW)}, "entity": [
    {"id": "1", "alert": {"active_period": [{"start": int(NOW - 600)}],
                          "informed_entity": [{"route_id": "C"}],
                          "header_text": {"translation": [{"text": "[C] trains are delayed.", "language": "en"}]},
                          "transit_realtime.mercury_alert": {"alert_type": "Delays"}}},
    {"id": "2", "alert": {"active_period": [{"start": int(NOW - 7200), "end": int(NOW - 3600)}],
                          "informed_entity": [{"route_id": "C"}],
                          "header_text": {"translation": [{"text": "An old alert.", "language": "en"}]}}},
    {"id": "3", "alert": {"informed_entity": [{"route_id": "7"}],
                          "header_text": {"translation": [{"text": "[7] elsewhere.", "language": "en"}]}}},
]}


@pytest.fixture(autouse=True)
def fresh():
    mta.reset_for_tests()


def answers(feed_answer):
    # 86 St on Central Park West is a C and B station, so both the ACE and the BDFM feeds are read.
    return {STATIONS: [(200, ROWS)], ACE: [feed_answer], "gtfs-bdfm": [feed()], ALERTS: [(200, ALERT_BODY)]}


def test_arrivals_by_direction_with_terminal_and_active_alerts_only():
    trains = feed(("A", [("A20N", 240), ("A02N", 1800)]), ("C", [("A20S", 60), ("A32S", 900)]),
                  ("A", [("A20N", -300)]))  # a train that already left
    with FakeHTTP(answers(trains)):
        result = get_transit_arrivals("86 St", line=None, lat=40.7853, lng=-73.9693)

    assert result["ok"], result
    data = result["data"]
    assert data["station"]["gtfs_stop_id"] == "A20"  # the nearest of the two 86 St stations
    uptown, = data["arrivals"]["Uptown"]
    assert (uptown["line"], uptown["toward"], uptown["minutes_away"]) == ("A", "Inwood-207 St", 4)
    assert data["arrivals"]["Downtown"][0]["toward"] == "W 4 St-Wash Sq"
    assert [a["text"] for a in data["alerts"]] == ["[C] trains are delayed."]
    assert result["freshness"]["kind"] == "live"


def test_a_name_shared_by_several_stations_asks_for_the_line_or_location():
    with FakeHTTP({STATIONS: [(200, ROWS)]}) as http:
        result = get_transit_arrivals("86th Street")

    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "several stations" in result["error"]["message"]
    assert all(STATIONS in url for url in http.urls())


def test_the_line_picks_the_station_and_filters_trains():
    trains = feed(("A", [("A20N", 240)]), ("C", [("A20N", 120)]))
    with FakeHTTP(answers(trains)):
        result = get_transit_arrivals("86 St", line="A", direction="uptown")

    assert [a["line"] for a in result["data"]["arrivals"]["Uptown"]] == ["A"]


def test_an_old_feed_is_refused_as_stale():
    with FakeHTTP(answers(feed(("A", [("A20N", 240)]), age_s=900))):
        result = get_transit_arrivals("86 St", line="A")

    assert result["error"]["code"] == "STALE_DATA"


def test_a_feed_outage_is_upstream_unavailable_with_a_schedule_fallback():
    with FakeHTTP({STATIONS: [(200, ROWS)], ACE: [(503, None)]}):
        result = get_transit_arrivals("86 St", line="A")

    assert result["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert "get_route" in result["error"]["next_step"]


def test_an_unknown_station_is_no_match():
    with FakeHTTP({STATIONS: [(200, ROWS)]}):
        result = get_transit_arrivals("Hogwarts")

    assert result["error"]["code"] == "NO_MATCH"


def test_a_line_picks_its_own_station_among_same_named_ones():
    one = feed(("1", [("121S", 180)]))
    with FakeHTTP({STATIONS: [(200, ROWS)], "mtagtfsfeeds/nyct%2Fgtfs": [one], ALERTS: [(200, ALERT_BODY)]}):
        result = get_transit_arrivals("86 St", line="1")

    assert result["ok"], result
    assert result["data"]["station"]["gtfs_stop_id"] == "121"
