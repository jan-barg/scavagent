"""find_filming_records: street matching, coverage dates, and refusing to imply a current set."""

import pytest

from fake_http import FakeHTTP
from integrations import filming
from integrations.filming import find_filming_records

PERMITS = "tg4x-b46p"
COVERAGE = (200, [{"latest_start": "2026-06-29T16:00:00.000", "latest_entry": "2026-06-29T07:04:26.000"}])


def permit(event_id, parking, start="2026-06-15T06:00:00.000", category="Television"):
    return {"eventid": event_id, "eventtype": "Shooting Permit", "startdatetime": start,
            "enddatetime": start.replace("06:00", "19:00"), "parkingheld": parking, "borough": "Manhattan",
            "category": category, "subcategoryname": "Episodic series", "zipcode_s": "10024, 10025,"}


@pytest.fixture(autouse=True)
def fresh():
    filming.reset_for_tests()


def test_permits_on_the_street_are_returned_with_coverage_and_a_caveat():
    rows = [permit("1", "COLUMBUS AVENUE between WEST   77 STREET and WEST   81 STREET"),
            permit("2", "WEST   181 STREET between BROADWAY and ST NICHOLAS AVENUE")]  # 181, not 81
    with FakeHTTP({PERMITS: [COVERAGE, (200, rows)]}) as http:
        result = find_filming_records(street="W 81st St")

    assert result["ok"], result
    data = result["data"]
    assert [r["event_id"] for r in data["records"]] == ["1"]
    assert data["records"][0]["parking_held"] == "COLUMBUS AVENUE between WEST 77 STREET and WEST 81 STREET"
    assert data["records"][0]["start"] == "2026-06-15T06:00:00-04:00"
    assert data["coverage"]["latest_permit_start"] == "2026-06-29"
    assert "does not show that filming happened" in result["warnings"][0]
    assert result["freshness"]["kind"] == "historical"
    where = http.calls[1]["params"]["$where"]
    assert "startdatetime between '2025-06-29T00:00:00' and '2026-06-29T23:59:59'" in where


def test_dates_after_the_data_ends_are_stale_not_an_empty_success():
    with FakeHTTP({PERMITS: [COVERAGE]}) as http:
        result = find_filming_records(street="Columbus Avenue", date_from="2026-09-28")

    assert result["error"]["code"] == "STALE_DATA"
    assert "never promise a current set" in result["error"]["next_step"]
    assert len(http.calls) == 1  # only the coverage check


def test_a_range_running_past_the_data_says_where_it_stops():
    with FakeHTTP({PERMITS: [COVERAGE, (200, [])]}):
        result = find_filming_records(zip_code="10024", date_from="2026-06-01", date_to="2026-09-28")

    assert result["ok"] and result["data"]["records"] == []
    assert "stop with permits starting 2026-06-29" in result["warnings"][1]


@pytest.mark.parametrize("args", [{}, {"street": "Barnes and Noble"}, {"zip_code": "1002"}, {"street": "Broadway", "date_to": "June"}])
def test_bad_arguments_are_rejected(args):
    with FakeHTTP({PERMITS: [COVERAGE]}):
        result = find_filming_records(**args)

    assert result["error"]["code"] == "INVALID_ARGUMENT"
