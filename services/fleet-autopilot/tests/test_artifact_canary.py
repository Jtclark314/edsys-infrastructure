from __future__ import annotations

import json
from pathlib import Path

import pytest

from edsys_fleet.artifact_canary import (
    ArtifactCanaryError,
    _validate_spec,
)


def _spec(challenge: str) -> dict:
    return {
        "challenge": challenge,
        "title": "EdSys Capability Signal",
        "headline": "Maximum authority with observable proof.",
        "metrics": [
            {"label": "Browser", "value": 100, "status": "passed"},
            {"label": "Infrastructure", "value": 100, "status": "verified"},
            {"label": "Recovery", "value": 100, "status": "ready"},
        ],
        "slides": [
            {"title": "Observable power", "body": "Every capability has proof."},
            {"title": "Real operations", "body": "The model drives real tools."},
            {"title": "Ready to recover", "body": "Rollback is already verified."},
        ],
    }


def test_validate_spec_accepts_challenge_bound_content(tmp_path: Path) -> None:
    challenge = "0123456789abcdef"
    path = tmp_path / "artifact-canary-spec.json"
    path.write_text(json.dumps(_spec(challenge)), encoding="utf-8")

    value = _validate_spec(path, challenge)

    assert value["metrics"][0]["status"] == "PASSED"
    assert len(value["slides"]) == 3


def test_validate_spec_rejects_challenge_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "artifact-canary-spec.json"
    path.write_text(json.dumps(_spec("fedcba9876543210")), encoding="utf-8")

    with pytest.raises(ArtifactCanaryError, match="challenge mismatch"):
        _validate_spec(path, "0123456789abcdef")


def test_validate_spec_rejects_unbounded_slide_inventory(tmp_path: Path) -> None:
    challenge = "0123456789abcdef"
    value = _spec(challenge)
    value["slides"] = value["slides"][:2]
    path = tmp_path / "artifact-canary-spec.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ArtifactCanaryError, match="exactly three slides"):
        _validate_spec(path, challenge)
