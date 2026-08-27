from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "verify-backup.py"
SPEC = importlib.util.spec_from_file_location("verify_automation_backup", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


RUN_ID = "20260822T120000Z"


def write_fixture(root: Path) -> None:
    (root / "node-red").mkdir(parents=True)
    (root / "node-red" / "flows.json").write_text("[]\n", encoding="utf-8")
    (root / "influx").mkdir()
    (root / "influx" / "backup.bin").write_bytes(b"influx-backup")
    artifacts = ["influx/backup.bin", "node-red/flows.json"]
    manifest = {
        "schema": MODULE.MANIFEST_SCHEMA,
        "run_id": RUN_ID,
        "created_utc": "2026-08-22T12:00:05.123456Z",
        "hostname": "edcore-automation",
        "compose_project": "edsys-edcore-automation",
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "images": ["example.invalid/automation@sha256:" + "1" * 64],
        "services": MODULE.EXPECTED_SERVICES,
    }
    (root / "MANIFEST.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    hashed = [*artifacts, "MANIFEST.json"]
    lines = []
    for relative in sorted(hashed):
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        lines.append(f"{digest}  ./{relative}\n")
    (root / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def test_valid_backup(tmp_path):
    write_fixture(tmp_path)
    manifest = MODULE.verify_backup(tmp_path, RUN_ID)
    assert manifest["artifact_count"] == 2


def test_rejects_payload_tamper(tmp_path):
    write_fixture(tmp_path)
    (tmp_path / "node-red" / "flows.json").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(MODULE.VerificationError, match="SHA-256 mismatch"):
        MODULE.verify_backup(tmp_path, RUN_ID)


def test_rejects_uninventoried_file(tmp_path):
    write_fixture(tmp_path)
    (tmp_path / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(MODULE.VerificationError, match="artifact inventory mismatch"):
        MODULE.verify_backup(tmp_path, RUN_ID)


def test_rejects_symlink(tmp_path):
    write_fixture(tmp_path)
    (tmp_path / "link").symlink_to("node-red/flows.json")
    with pytest.raises(MODULE.VerificationError, match="non-regular file"):
        MODULE.verify_backup(tmp_path, RUN_ID)


def test_rejects_wrong_service_contract(tmp_path):
    write_fixture(tmp_path)
    manifest_path = tmp_path / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["services"] = ["mosquitto"]
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    with pytest.raises(MODULE.VerificationError, match="service set mismatch"):
        MODULE.verify_backup(tmp_path, RUN_ID)


def test_rejects_checksum_traversal(tmp_path):
    write_fixture(tmp_path)
    with (tmp_path / "SHA256SUMS").open("a", encoding="utf-8") as handle:
        handle.write(f"{'0' * 64}  ./../outside\n")
    with pytest.raises(MODULE.VerificationError, match="canonical relative path"):
        MODULE.verify_backup(tmp_path, RUN_ID)
