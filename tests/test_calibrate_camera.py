"""Field notes become a catalogue entry only with the participant's own evidence; stills are saved as fetched.

Coordinates and wording here are TEST data, not fieldwork.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from adventure import agent_tools
from integrations import cameras
from scripts import calibrate_camera as cal

MOUNT = {"lat": 40.785302, "lng": -73.969353}
NOW = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
JPEG = b"\xff\xd8\xffTEST_STILL_NOT_A_PHOTO\xff\xd9"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Tests must not reach DOT")
    monkeypatch.setattr(cameras.requests, "get", denied)
    monkeypatch.delenv("SCAVAGENT_DEV_CAMERA_FIXTURES", raising=False)


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    """A copy of the committed catalogue at a temporary path the app loads from."""
    path = tmp_path / "camera_catalogue.json"
    path.write_text(cameras.CATALOGUE_PATH.read_text())
    monkeypatch.setattr(cameras, "CATALOGUE_PATH", path)
    return path


@pytest.fixture
def notes(tmp_path):
    still = tmp_path / "evidence.jpg"
    still.write_bytes(JPEG)
    return {
        "checkpoint_id": "test_cpw_86_corner", "camera_id": "8a6bc417-4877-4ebe-8052-88c1b261baf1",
        "from_candidate": "candidate_cpw_86", "stand_location": {"lat": 40.78518, "lng": -73.96990},
        "stand_location_source": "TEST map pin", "address": "TEST address", "landmark": "TEST landmark",
        "side_of_street": "TEST side", "positioning_instructions": "TEST: stand by the pole, face east.",
        "reference_view_notes": "TEST view", "visibility_notes": "", "person_region": [0.0, 0.4, 0.2, 0.8],
        "participant_visible": True, "verified_at": "2026-09-30T13:05:00-04:00", "verified_by": "TEST",
        "evidence_stills": [str(still)], "fallback_checkpoint_id": None,
    }


def test_notes_become_an_enabled_field_verified_entry_with_hashed_evidence(notes):
    checkpoint, log = cal.build_entry(notes, MOUNT, now=NOW)
    assert (checkpoint.verification_status, checkpoint.enabled) == ("field_verified", True)
    assert checkpoint.last_field_verified_at == datetime(2026, 9, 30, 17, 5, tzinfo=timezone.utc)
    assert checkpoint.stand_location.lat == 40.78518 and checkpoint.visibility_notes is None
    assert log["evidence_stills"] == [{"file": "evidence.jpg", "sha256": hashlib.sha256(JPEG).hexdigest(), "bytes": len(JPEG)}]
    assert (log["verified_by"], log["from_candidate"]) == ("TEST", "candidate_cpw_86")


def refuse(**changes):
    def apply(notes):
        notes.update(changes)
    return apply


@pytest.mark.parametrize("change, message", [
    (refuse(participant_visible=None), "participant_visible"),
    (refuse(participant_visible=False), "participant_visible"),
    (refuse(evidence_stills=[]), "at least one saved still"),
    (refuse(evidence_stills=["/nonexistent/still.jpg"]), "does not exist"),
    (refuse(verified_at="2026-09-30T13:05:00"), "UTC offset"),
    (refuse(verified_at=""), "ISO time"),
    (refuse(verified_at=(NOW + timedelta(hours=1)).isoformat()), "future"),
    (refuse(stand_location=dict(MOUNT)), "mounting coordinates"),
    (refuse(stand_location={"lat": -73.96990, "lng": 40.78518}), "lat/lng swapped"),
    (refuse(stand_location={"lat": None, "lng": None}), "numeric lat/lng"),
    (refuse(landmark="  "), "landmark"),
    (refuse(checkpoint_id="candidate_cpw_86"), "own checkpoint_id"),
    (refuse(person_region=[0.5, 0.4, 0.2, 0.8]), "person_region"),
])
def test_notes_without_field_evidence_are_refused(notes, change, message):
    change(notes)
    with pytest.raises(cal.NotesError, match=message):
        cal.build_entry(notes, MOUNT, now=NOW)


def test_an_unfilled_template_cannot_become_an_entry(catalogue):
    camera_id, candidate = cal.resolve("candidate_cpw_86", cal.load_catalogue())
    assert camera_id == "8a6bc417-4877-4ebe-8052-88c1b261baf1"
    form = cal.template(camera_id, candidate)
    assert form["stand_location"] == {"lat": None, "lng": None} and form["participant_visible"] is None
    with pytest.raises(cal.NotesError):
        cal.build_entry(form, MOUNT, now=NOW)


class FakeDOT:
    """Serves the given frames in order; an exception in the list is raised for that fetch."""

    def __init__(self, frames=()):
        self.frames, self.stills = list(frames), 0

    def catalogue(self):
        return {"8a6bc417-4877-4ebe-8052-88c1b261baf1": {"is_online": True}}, NOW

    def still(self, camera_id, catalogue):
        self.stills += 1
        frame = self.frames.pop(0) if self.frames else JPEG
        if isinstance(frame, Exception):
            raise frame
        return frame, "image/jpeg", f"{cameras.CATALOGUE_URL}/{camera_id}/image", NOW + timedelta(seconds=2 * self.stills)


def test_a_written_entry_is_found_by_the_finder_and_the_planner(catalogue, notes):
    checkpoint, log = cal.build_entry(notes, MOUNT, now=NOW)
    cal.write_catalogue(cal.add_to_catalogue(cal.load_catalogue(catalogue), checkpoint, log))

    found = cameras.find_camera_checkpoints(point={"lat": 40.7852, "lng": -73.9698}, client=FakeDOT())
    assert found["ok"] and found["data"]["checkpoints"][0]["checkpoint"]["checkpoint_id"] == "test_cpw_86_corner"
    assert agent_tools.camera_lookup()("test_cpw_86_corner") == checkpoint
    saved = json.loads(catalogue.read_text())
    assert saved["field_log"][-1]["checkpoint_id"] == "test_cpw_86_corner"
    [candidate] = [c for c in saved["candidates"] if c["checkpoint_id"] == "candidate_cpw_86"]
    assert candidate["calibrated_as"] == ["test_cpw_86_corner"] and candidate["stand_location"] is None


def test_recalibrating_replaces_the_entry_and_its_log_without_duplicates(catalogue, notes):
    first, log = cal.build_entry(notes, MOUNT, now=NOW)
    cal.write_catalogue(cal.add_to_catalogue(cal.load_catalogue(catalogue), first, log))
    notes["positioning_instructions"] = "TEST: moved two steps north."
    second, log = cal.build_entry(notes, MOUNT, now=NOW)
    cal.write_catalogue(cal.add_to_catalogue(cal.load_catalogue(catalogue), second, log))

    saved = json.loads(catalogue.read_text())
    assert [c["positioning_instructions"] for c in saved["checkpoints"]] == ["TEST: moved two steps north."]
    assert len(saved["field_log"]) == 1
    assert saved["candidates"][0]["calibrated_as"] == ["test_cpw_86_corner"]


def test_a_catalogue_the_app_cannot_load_is_not_written(catalogue, notes):
    before = catalogue.read_text()
    checkpoint, log = cal.build_entry(notes, MOUNT, now=NOW)
    broken = cal.add_to_catalogue(cal.load_catalogue(catalogue), checkpoint, log)
    broken["checkpoints"].append(dict(broken["checkpoints"][0]))  # duplicate id
    with pytest.raises(cal.NotesError, match="left it unchanged"):
        cal.write_catalogue(broken)
    assert catalogue.read_text() == before


def test_watch_saves_each_changed_frame_and_skips_unavailable_ones(tmp_path):
    other = JPEG.replace(b"TEST", b"NEXT")
    client = FakeDOT([JPEG, JPEG, cameras.CameraUnavailable("offline"), other, JPEG])
    ticks = iter(range(100))
    logged = []
    saved = cal.watch(client, "8a6bc417-4877-4ebe-8052-88c1b261baf1", every=2, minutes=6 / 60,
                      out_dir=tmp_path, sleep=lambda s: None, clock=lambda: next(ticks), log=logged.append)
    assert client.stills == 5
    assert [p.read_bytes() for p in saved] == [JPEG, other, JPEG]  # the repeated frame is not saved twice
    assert [p.name for p in saved] == ["20260930T180002Z.jpg", "20260930T180008Z.jpg", "20260930T180010Z.jpg"]
    assert any("unavailable" in line for line in logged)


def test_still_is_saved_under_the_camera_id(tmp_path):
    path, data = cal.save_still(FakeDOT(), "8a6bc417-4877-4ebe-8052-88c1b261baf1", {}, out_dir=tmp_path)
    assert path == tmp_path / "8a6bc417-4877-4ebe-8052-88c1b261baf1" / "20260930T180002Z.jpg"
    assert path.read_bytes() == data == JPEG


def test_load_checkpoints_is_the_validated_catalogue(catalogue):
    committed = [r["checkpoint_id"] for r in json.loads(catalogue.read_text())["checkpoints"]]
    assert [c.checkpoint_id for c in cameras.load_checkpoints()] == committed
    fixtures = [c for c in cameras.load_checkpoints(allow_synthetic=True) if c.verification_status == "synthetic_fixture"]
    assert fixtures and not [c for c in cameras.load_checkpoints() if c.verification_status == "synthetic_fixture"]
    catalogue.write_text(json.dumps({"checkpoints": [{"checkpoint_id": "x"}]}))
    with pytest.raises(ValueError):
        cameras.load_checkpoints()
    assert agent_tools.camera_lookup()("x") is None


def test_image_notes_become_image_verified_and_need_their_own_confirmation(notes):
    notes.update(method="image", spot_in_view=True)
    del notes["participant_visible"]
    checkpoint, log = cal.build_entry(notes, MOUNT, now=NOW)
    assert (checkpoint.verification_status, checkpoint.enabled, log["method"]) == ("image_verified", True, "image")
    assert checkpoint.last_image_verified_at is not None and checkpoint.last_field_verified_at is None

    notes.update(spot_in_view=None, participant_visible=True)  # the other method's confirmation does not count
    with pytest.raises(cal.NotesError, match="spot_in_view"):
        cal.build_entry(notes, MOUNT, now=NOW)
    notes.update(method="guess")
    with pytest.raises(cal.NotesError, match="method"):
        cal.build_entry(notes, MOUNT, now=NOW)
    form = cal.template("cam", None, method="image")
    assert form["spot_in_view"] is None and "participant_visible" not in form
