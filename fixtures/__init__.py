"""Synthetic development fixtures shaped like the shared contract.

These let each workstream run before the other's real integration exists. They are
not discoveries, routes, or camera positions anyone has checked in the field, and
the live product path must replace them with real tool results.
"""

import json
from pathlib import Path

from schemas import (
    AdventurePlan,
    AdventureRequest,
    AdventureState,
    CameraCheckpoint,
    FixtureProvenance,
    PhotoAsset,
    Record,
)

FIXTURE_DIR = Path(__file__).parent


class ScenarioFixture(Record):
    """A request, the plan made for it, and progress. Optionally, a later revision."""

    provenance: FixtureProvenance
    request: AdventureRequest
    plan: AdventurePlan
    state: AdventureState
    user_message: str | None = None  # The message that triggers the revision
    revised_plan: AdventurePlan | None = None
    revised_state: AdventureState | None = None


class CameraFixture(Record):
    provenance: FixtureProvenance
    checkpoints: list[CameraCheckpoint]
    photos: list[PhotoAsset] = []


SCENARIOS = ("start_only", "constrained_route", "revision_after_skip")


def load_scenario(name: str) -> ScenarioFixture:
    return ScenarioFixture.model_validate(json.loads((FIXTURE_DIR / f"{name}.json").read_text()))


def load_cameras() -> CameraFixture:
    return CameraFixture.model_validate(json.loads((FIXTURE_DIR / "cameras.json").read_text()))
