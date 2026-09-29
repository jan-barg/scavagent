"""Describing a standing spot from street geometry, and placing one from a description. Offline: geometry is given."""

import math

import pytest

from integrations import cameras
from schemas import LatLng
from scripts import camera_geometry as geo

CENTER = LatLng(lat=40.7993084, lng=-73.9432361)
G = math.radians(geo.GRID_DEG)
AVENUE = geo.Street("Park Avenue", (math.sin(G), math.cos(G)), 19.6)  # runs grid-north
STREET = geo.Street("East 116th Street", (-math.cos(G), math.sin(G)), 11.5)  # runs grid-west


def grid_point(east, north):
    """A pin this many meters grid-east and grid-north of the intersection."""
    x = east * math.cos(G) + north * math.sin(G)
    y = -east * math.sin(G) + north * math.cos(G)
    return geo.from_local(CENTER, x, y)


def test_a_pin_along_a_street_is_described_by_side_and_distance_in_grid_directions():
    described = geo.describe(CENTER, [AVENUE, STREET], grid_point(28, 11))
    assert described["side_of_street"] == "north side of East 116th Street, about 30 m east of Park Avenue"
    assert geo.describe(CENTER, [AVENUE, STREET], grid_point(-19, -60))["side_of_street"] == \
        "west side of Park Avenue, about 60 m south of East 116th Street"


def test_a_pin_near_both_streets_is_a_corner():
    assert geo.describe(CENTER, [AVENUE, STREET], grid_point(-15, 12))["side_of_street"] == \
        "northwest corner of Park Avenue and East 116th Street"


@pytest.mark.parametrize("side, direction, along", [("north", "east", 30), ("south", "west", 45), ("south", "east", 0)])
def test_locate_places_a_described_spot_where_describe_reads_it_back(side, direction, along):
    pin = geo.locate(CENTER, STREET, AVENUE, side, direction, along)
    again = geo.describe(CENTER, [AVENUE, STREET], pin)
    if along:
        assert again["side_of_street"] == f"{side} side of East 116th Street, about {along} m {direction} of Park Avenue"
    else:
        assert again["corner"] and again["side_of_street"].startswith(f"{side}{direction} corner")


def test_locate_rejects_a_side_the_street_does_not_have():
    with pytest.raises(ValueError, match="not a side"):
        geo.locate(CENTER, STREET, AVENUE, "east", "east", 30)


def test_off_grid_streets_use_true_compass_directions():
    due_north = geo.Street("Broadway", (0.0, 1.0), 10)
    due_east = geo.Street("Battery Place", (1.0, 0.0), 10)
    assert not geo.on_grid([due_north, due_east])
    pin = geo.from_local(CENTER, 40, 9)
    assert geo.describe(CENTER, [due_north, due_east], pin)["side_of_street"] == \
        "north side of Battery Place, about 40 m east of Broadway"


def test_a_divided_avenue_puts_its_sidewalk_beyond_the_outer_carriageway():
    def way(offset_east, lanes):
        return {"tags": {"lanes": str(lanes)}, "geometry": [
            {"lat": p.lat, "lon": p.lng} for p in (geo.from_local(CENTER, offset_east, -100), geo.from_local(CENTER, offset_east, 100))]}
    single = geo.street_from_ways("Avenue", CENTER, [way(0, 2)])
    divided = geo.street_from_ways("Avenue", CENTER, [way(-12, 3), way(12, 3)])
    assert single.sidewalk_m == pytest.approx(2 * geo.LANE_M / 2 + geo.PARKING_M + geo.HALF_SIDEWALK_M)
    assert divided.sidewalk_m == pytest.approx(12 + 3 * geo.LANE_M / 2 + geo.PARKING_M + geo.HALF_SIDEWALK_M, abs=0.2)
    assert abs(divided.direction[1]) == pytest.approx(1)


def test_camera_names_become_two_streets():
    assert geo.camera_streets("Park Ave @ E 116 Street") == ["park avenue", "east 116th street"]
    assert geo.camera_streets("Amsterdam @ 72 St") == ["amsterdam avenue", "72nd street"]
    assert geo.camera_streets("Third Ave Bridge") is None


def test_described_distances_stay_within_the_import_limit():
    pin = geo.locate(CENTER, STREET, AVENUE, "north", "east", 30)
    assert cameras._distance(pin, CENTER) < 250


def test_an_overloaded_overpass_answer_falls_back_to_the_next_server(monkeypatch):
    """An overloaded server answers 200 with an error remark and no data; that is not 'no intersection'."""
    center = CENTER
    def way(name, pts):
        return {"type": "way", "tags": {"name": name}, "geometry": [{"lat": p.lat, "lon": p.lng} for p in pts]}
    good = {"elements": [
        {"type": "node", "lat": center.lat, "lon": center.lng},
        way("Park Avenue", [grid_point(0, -80), grid_point(0, 80)]),
        way("East 116th Street", [grid_point(-80, 0), grid_point(80, 0)]),
    ]}
    answers = {"https://busy": {"elements": [], "remark": "runtime error: Query timed out"}, "https://ok": good}
    monkeypatch.setattr(geo, "fetch_json", lambda provider, method, url, **kw: answers[url])
    found, streets = geo.intersection_streets("Park Ave @ E 116 Street", center, urls=["https://busy", "https://ok"])
    assert [s.name for s in streets] == ["Park Avenue", "East 116th Street"]
    assert cameras._distance(found, center) < 1

    answers["https://ok"] = answers["https://busy"]
    with pytest.raises(ValueError, match="did not answer"):
        geo.intersection_streets("Park Ave @ E 116 Street", center, urls=["https://busy", "https://ok"])


def test_a_street_is_found_under_its_alternate_name():
    pattern = geo.geocoding.street_pattern("2nd avenue")
    assert geo._named({"name": "México-Tenochtitlan Avenue", "alt_name": "2nd Avenue"}, pattern)
    assert geo._named({"name": "Honorary Way", "official_name": "Plaza X;Second Avenue"}, pattern)
    assert not geo._named({"name": "México-Tenochtitlan Avenue", "alt_name": "22nd Avenue"}, pattern)


def test_camera_name_suffixes_are_ignored():
    assert geo.camera_streets("7 Ave @ 43 St - 64.186 - PTZ") == ["7th avenue", "43rd street"]
    assert geo.camera_streets("Broadway @ 46 St- Quad North") == ["broadway", "46th street"]
    assert geo.camera_streets("Rockefeller Plz @ 48 St (between 5 Ave and 6 Ave)") == ["rockefeller plaza", "48th street"]


def test_the_grid_fallback_places_spots_like_the_mapped_grid():
    center, (avenue, street) = geo.grid_streets("Broadway @ 46 St", CENTER)
    assert center == CENTER and geo.on_grid([avenue, street])
    pin = geo.locate(center, street, avenue, "north", "east", 30)
    assert geo.describe(center, [avenue, street], pin)["side_of_street"] == "north side of 46th Street, about 30 m east of Broadway"
    with pytest.raises(ValueError, match="not an avenue meeting"):
        geo.grid_streets("E 63 St @ QBB", CENTER)


def test_a_three_street_camera_name_is_not_turned_into_one_invented_street():
    assert geo.camera_streets("Broadway @ 6 Ave / 33 St") is None
    with pytest.raises(ValueError, match="Cannot read two streets"):
        geo.grid_streets("Broadway @ 6 Ave / 33 St", CENTER)


def test_a_spot_on_a_wide_avenues_sidewalk_is_described_on_the_avenue():
    """1st Avenue at 40th Street is ~70 m wide (tunnel plus service roads): its sidewalk is 35 m from its center."""
    avenue = geo.Street("1st Avenue", (math.sin(G), math.cos(G)), 35.0)
    street = geo.Street("40th Street", (-math.cos(G), math.sin(G)), 8.0)
    along = geo.locate(CENTER, avenue, street, "east", "north", 30)
    assert geo.describe(CENTER, [avenue, street], along)["side_of_street"] == \
        "east side of 1st Avenue, about 30 m north of 40th Street"
    near_corner = geo.locate(CENTER, avenue, street, "east", "north", 8)
    assert geo.describe(CENTER, [avenue, street], near_corner)["side_of_street"] == \
        "northeast corner of 1st Avenue and 40th Street"
