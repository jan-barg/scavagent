"""Camera checkpoint calibration: save DOT stills and turn notes into catalogue entries.

    uv run python -m scripts.calibrate_camera still CAMERA...                 # save each camera's current still
    uv run python -m scripts.calibrate_camera watch CAMERA [--every 3] [--minutes 20]
    uv run python -m scripts.calibrate_camera template CAMERA > notes.json    # field-notes form to fill in
    uv run python -m scripts.calibrate_camera entry notes.json [--write]      # check notes, print the entry, add it
    uv run python -m scripts.calibrate_camera import-spots SPOTS_DIR --evidence DIR [--write]

Run from the repository root.
CAMERA is a DOT camera id or a candidate id from data/camera_catalogue.json (e.g. candidate_cpw_86).
Evidence paths in the notes are relative to the repository root. Stills are saved under .data/calibration/<camera id>/ (gitignored) and named by retrieval time in UTC;
the DOT overlay in the image shows the frame's own local time. `watch` keeps saving new frames during a
field visit so the stills can be matched afterwards to the times the participant stood at each spot.

Notes have a method. "field": someone stood at the spot and saw themselves in a still (field_verified).
"image": a teammate matched a sidewalk spot in a live still to map imagery without standing there
(image_verified). `entry` refuses notes without that confirmation, an existing evidence still, a
verification time, or a standing position that is plausibly not the camera mount. It never fills in a
missing field: coordinates and wording come only from the notes.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from integrations import cameras
from schemas import NYC_TIMEZONE, CameraCheckpoint, LatLng

ROOT = Path(__file__).resolve().parents[1]
STILLS_DIR = ROOT / ".data" / "calibration"
MIN_FROM_MOUNT_M = 1  # closer than this, the "standing position" is the camera's own coordinates
MAX_FROM_MOUNT_M = 250  # farther than this, a participant is not usefully visible (or lat/lng are swapped)
TEXT_FIELDS = ("address", "landmark", "side_of_street", "positioning_instructions", "reference_view_notes")
NYC = ZoneInfo(NYC_TIMEZONE)


METHODS = {  # method -> (confirmation field, what it confirms, status, date field)
    "field": ("participant_visible", "the person who stood there saw themselves in a still",
              "field_verified", "last_field_verified_at"),
    "image": ("spot_in_view", "the marked spot is a public sidewalk area visible in the evidence still",
              "image_verified", "last_image_verified_at"),
}


class NotesError(ValueError):
    """The notes cannot become a verified checkpoint; the message says what to fix."""


def load_catalogue(path=None):
    return json.loads(Path(path or cameras.CATALOGUE_PATH).read_text())


def resolve(camera, catalogue):
    """(camera_id, candidate or None) for a DOT camera id or a candidate checkpoint id."""
    for candidate in catalogue.get("candidates", []):
        if camera in (candidate["checkpoint_id"], candidate["camera_id"]):
            return candidate["camera_id"], candidate
    return camera, None


def save_still(client, camera_id, catalogue, out_dir=STILLS_DIR):
    """Fetch one still through the bounded DOT client and save it; returns (path, bytes)."""
    data, content_type, _, retrieved_at = client.still(camera_id, catalogue)
    return _write(out_dir, camera_id, data, content_type, retrieved_at), data


def watch(client, camera_id, every, minutes, out_dir=STILLS_DIR, sleep=time.sleep, clock=time.monotonic, log=print):
    """Save each new frame until `minutes` pass; an unavailable frame is reported and skipped."""
    catalogue, _ = client.catalogue()
    end, last, saved = clock() + minutes * 60, None, []
    while clock() < end:
        try:
            data, content_type, _, retrieved_at = client.still(camera_id, catalogue)
        except cameras.CameraUnavailable as error:
            log(f"{datetime.now(NYC):%H:%M:%S} unavailable: {error}")
        else:
            if data != last:  # the feed refreshes about every 2 s; keep only changed frames
                path, last = _write(out_dir, camera_id, data, content_type, retrieved_at), data
                saved.append(path)
                log(f"{retrieved_at.astimezone(NYC):%H:%M:%S} {path}")
        sleep(every)
    return saved


def _write(out_dir, camera_id, data, content_type, retrieved_at):
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
    folder = Path(out_dir) / camera_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{retrieved_at.astimezone(timezone.utc):%Y%m%dT%H%M%S}Z{suffix}"
    path.write_bytes(data)
    return path


def template(camera_id, candidate, method="field"):
    """A notes form: every field starts empty, so nothing unobserved can slip into an entry."""
    return {
        "method": method,
        "checkpoint_id": "",
        "camera_id": camera_id,
        "from_candidate": candidate["checkpoint_id"] if candidate else None,
        "camera_name": candidate["name"] if candidate else None,
        "stand_location": {"lat": None, "lng": None},
        "stand_location_source": "",
        "address": "",
        "landmark": "",
        "side_of_street": "",
        "positioning_instructions": "",
        "reference_view_notes": "",
        "visibility_notes": "",
        "person_region": None,
        METHODS[method][0]: None,
        "verified_at": "",
        "verified_by": "",
        "evidence_stills": [],
        "fallback_checkpoint_id": None,
    }


def build_entry(notes, mount=None, now=None, base_dir=ROOT):
    """Notes -> (CameraCheckpoint, field-log record). Raises NotesError naming what to fix."""
    now = now or datetime.now(timezone.utc)
    method = notes.get("method", "field")
    if method not in METHODS:
        raise NotesError(f"method must be one of {', '.join(METHODS)}.")
    confirmation, confirms, status, date_field = METHODS[method]
    missing = [k for k in ("checkpoint_id", "camera_id", "stand_location_source", "verified_by", *TEXT_FIELDS)
               if not isinstance(notes.get(k), str) or not notes[k].strip()]
    if missing:
        raise NotesError(f"Fill in {', '.join(missing)}.")
    checkpoint_id = notes["checkpoint_id"]
    if checkpoint_id.startswith(("candidate_", "fixture_")):
        raise NotesError("Give the calibrated position its own checkpoint_id, not a candidate or fixture id.")
    if notes.get(confirmation) is not True:
        raise NotesError(f"{confirmation} must be true: {confirms}.")
    try:
        stand = LatLng.model_validate(notes.get("stand_location"))
        verified_at = datetime.fromisoformat(notes.get("verified_at") or "")
    except (ValueError, TypeError) as error:
        raise NotesError(f"stand_location needs numeric lat/lng and verified_at an ISO time: {error}") from None
    if verified_at.tzinfo is None:
        raise NotesError("verified_at needs a UTC offset, e.g. 2026-09-30T14:05:00-04:00.")
    if verified_at > now + timedelta(minutes=5):
        raise NotesError("verified_at is in the future.")
    if mount is not None:
        distance = cameras._distance(stand, LatLng.model_validate(mount))
        if distance < MIN_FROM_MOUNT_M:
            raise NotesError("stand_location equals the camera's mounting coordinates; record where the person stood.")
        if distance > MAX_FROM_MOUNT_M:
            raise NotesError(f"stand_location is {distance:.0f} m from the camera; check the coordinates (lat/lng swapped?).")
    evidence = []
    for name in notes.get("evidence_stills") or []:
        path = Path(base_dir) / name  # an absolute name stays absolute
        if not path.is_file() or path.stat().st_size == 0:
            raise NotesError(f"Evidence still {name} does not exist or is empty.")
        data = path.read_bytes()
        evidence.append({"file": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    if not evidence:
        raise NotesError("List at least one saved still (or screenshot) the position was checked on.")
    fields = {k: notes[k].strip() for k in TEXT_FIELDS}
    try:
        checkpoint = CameraCheckpoint(
            checkpoint_id=checkpoint_id, camera_id=notes["camera_id"], stand_location=stand, **fields,
            person_region=notes.get("person_region"), visibility_notes=(notes.get("visibility_notes") or "").strip() or None,
            verification_status=status, **{date_field: verified_at}, enabled=True,
            fallback_checkpoint_id=notes.get("fallback_checkpoint_id"),
        )
    except ValueError as error:
        raise NotesError(f"Not a valid CameraCheckpoint: {error}") from None
    if len(checkpoint.model_dump_json()) > 6000:
        raise NotesError("The entry's text is too long for model context; shorten the notes.")
    log = {"checkpoint_id": checkpoint_id, "camera_id": notes["camera_id"], "from_candidate": notes.get("from_candidate"),
           "method": method, "verified_at": verified_at.isoformat(), "verified_by": notes["verified_by"].strip(),
           "stand_location_source": notes["stand_location_source"].strip(), "evidence_stills": evidence,
           "note": "Evidence stills are kept outside Git; the hashes identify them."}
    if notes.get("workbench_spot_id"):
        log["workbench_spot_id"] = notes["workbench_spot_id"]
    return checkpoint, log


def spot_notes(spot, evidence_dir, taken):
    """A workbench spot (image method) as notes for build_entry; `taken` holds checkpoint ids already used."""
    words = re.sub(r"[^a-z0-9]+", "_", spot["camera_name"].lower().replace("@", " at ")).strip("_")
    base = f"img_{words}"[:100]
    checkpoint_id = next(f"{base}_{letter}" for letter in "abcdefghijklmnopqrstuvwxyz" if f"{base}_{letter}" not in taken)
    taken.add(checkpoint_id)
    return {
        "method": "image", "checkpoint_id": checkpoint_id, "camera_id": spot["camera_id"],
        "stand_location": spot["stand_location"], "stand_location_source": spot.get("stand_location_source") or "",
        **{k: spot.get(k) or "" for k in TEXT_FIELDS}, "visibility_notes": spot.get("visibility_notes") or "",
        "person_region": spot.get("box"), "spot_in_view": spot.get("spot_in_view") is True,
        "verified_at": spot.get("still_retrieved_at") or "",
        "verified_by": f"{spot.get('marked_by') or 'teammate'} (camera workbench, {spot.get('marked_at', '')[:10]})",
        "evidence_stills": [str(Path(evidence_dir) / spot["still_file"])] if spot.get("still_file") else [],
    }


def add_to_catalogue(catalogue, checkpoint, log):
    """A copy of the catalogue with this checkpoint added (or replaced) and its field log recorded."""
    updated = json.loads(json.dumps(catalogue))
    row = checkpoint.model_dump(mode="json", exclude_none=True)
    rows = [r for r in updated["checkpoints"] if r["checkpoint_id"] != checkpoint.checkpoint_id]
    updated["checkpoints"] = rows + [row]
    updated["field_log"] = [e for e in updated.get("field_log", []) if e["checkpoint_id"] != checkpoint.checkpoint_id] + [log]
    for candidate in updated.get("candidates", []):
        if candidate["checkpoint_id"] == log.get("from_candidate"):
            candidate.setdefault("calibrated_as", [])
            if checkpoint.checkpoint_id not in candidate["calibrated_as"]:
                candidate["calibrated_as"].append(checkpoint.checkpoint_id)
    return updated


def write_catalogue(updated, path=None):
    """Write the catalogue, then load it the way the app does; restore the old file if that fails."""
    path = Path(path or cameras.CATALOGUE_PATH)
    previous = path.read_text()
    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    try:
        cameras.load_checkpoints()
    except (OSError, ValueError, TypeError, KeyError) as error:
        path.write_text(previous)
        raise NotesError(f"The catalogue would not load ({error}); left it unchanged.") from None


def live_mount(camera_id):
    """The camera's mounting coordinates from the live DOT catalogue, for the sanity check only."""
    response = requests.get(cameras.CATALOGUE_URL, timeout=cameras.TIMEOUT, allow_redirects=False)
    response.raise_for_status()
    row = next((r for r in response.json() if r.get("id") == camera_id), None)
    if row is None:
        raise NotesError(f"Camera {camera_id} is not in the live DOT catalogue.")
    return {"lat": row["latitude"], "lng": row["longitude"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("still").add_argument("camera", nargs="+")
    w = sub.add_parser("watch")
    w.add_argument("camera")
    w.add_argument("--every", type=float, default=3)
    w.add_argument("--minutes", type=float, default=20)
    sub.add_parser("template").add_argument("camera")
    e = sub.add_parser("entry")
    e.add_argument("notes")
    e.add_argument("--write", action="store_true", help="add the entry to data/camera_catalogue.json")
    i = sub.add_parser("import-spots", help="workbench spot documents (one JSON file each) -> image_verified entries")
    i.add_argument("spots_dir")
    i.add_argument("--evidence", required=True, help="folder the workbench's still_file paths are relative to")
    i.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    catalogue = load_catalogue()
    client = cameras.DOTCameraClient()

    if args.command == "still":
        live, _ = client.catalogue()
        for camera in args.camera:
            camera_id, _ = resolve(camera, catalogue)
            path, data = save_still(client, camera_id, live)
            print(f"{path} ({len(data)} bytes)")
    elif args.command == "watch":
        if not 2 <= args.every <= 60 or not 0 < args.minutes <= 60:
            parser.error("--every must be 2-60 seconds and --minutes at most 60")
        camera_id, _ = resolve(args.camera, catalogue)
        print(f"Saving new frames of {camera_id} for {args.minutes:g} min; Ctrl-C stops.", file=sys.stderr)
        try:
            watch(client, camera_id, args.every, args.minutes)
        except KeyboardInterrupt:
            pass
    elif args.command == "template":
        print(json.dumps(template(*resolve(args.camera, catalogue)), indent=2))
    elif args.command == "import-spots":
        updated, taken, failed = catalogue, {c["checkpoint_id"] for c in catalogue["checkpoints"]}, 0
        imported = {e.get("workbench_spot_id") for e in catalogue.get("field_log", [])}
        for path in sorted(Path(args.spots_dir).rglob("*.json")):
            if path.stem in imported:
                continue  # already in the catalogue; re-importing must not duplicate it
            spot = json.loads(path.read_text())
            spot = spot.get("data", spot)  # an exported document may wrap its fields
            notes = dict(spot_notes(spot, args.evidence, taken), workbench_spot_id=path.stem)
            try:
                checkpoint, log = build_entry(notes, spot.get("camera_mount"))
            except NotesError as error:
                failed += 1
                print(f"Skipped {path.name} ({spot.get('camera_name')}): {error}", file=sys.stderr)
                continue
            updated = add_to_catalogue(updated, checkpoint, log)
            print(f"{checkpoint.checkpoint_id}: {spot['camera_name']} -> {checkpoint.address}")
        if args.write:
            try:
                write_catalogue(updated)
            except NotesError as error:
                sys.exit(f"Not added: {error}")
        print(f"{len(updated['checkpoints']) - len(catalogue['checkpoints'])} new entries, {failed} skipped"
              + ("" if args.write else " (dry run; --write adds them)"), file=sys.stderr)
    else:
        notes = json.loads(Path(args.notes).read_text())
        _, candidate = resolve(notes.get("camera_id", ""), catalogue)
        mount = candidate["camera_mount_location"] if candidate else live_mount(notes.get("camera_id", ""))
        try:
            checkpoint, log = build_entry(notes, mount)
        except NotesError as error:
            sys.exit(f"Not added: {error}")
        print(json.dumps(checkpoint.model_dump(mode="json", exclude_none=True), indent=2))
        if args.write:
            try:
                write_catalogue(add_to_catalogue(catalogue, checkpoint, log))
            except NotesError as error:
                sys.exit(f"Not added: {error}")
            print(f"Added {checkpoint.checkpoint_id} to {cameras.CATALOGUE_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
