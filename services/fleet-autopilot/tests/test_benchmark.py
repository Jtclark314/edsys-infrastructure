from __future__ import annotations

import json
import subprocess
from pathlib import Path

from edsys_fleet.benchmark import CapabilityBenchmark


def test_daily_streak_counts_unique_scheduled_eastern_days_only() -> None:
    runs = [
        {
            "suite": "deterministic",
            "triggered_by": "systemd-daily",
            "completed_at": "2026-08-06T09:00:00+00:00",
            "status": "passed",
        },
        {
            "suite": "deterministic",
            "triggered_by": "cli",
            "completed_at": "2026-08-06T12:00:00+00:00",
            "status": "failed",
        },
        {
            "suite": "deterministic",
            "triggered_by": "systemd-daily",
            "completed_at": "2026-08-05T09:00:00+00:00",
            "status": "passed",
        },
    ]

    assert CapabilityBenchmark._scheduled_daily_streak(runs) == 2


def test_authority_probe_normalizes_terminal_and_requires_doctor_contract() -> None:
    benchmark = object.__new__(CapabilityBenchmark)
    observed_env: dict[str, str] = {}

    def fake_command(argv, timeout=60, env=None):
        del timeout
        if argv[:2] == ["codex", "doctor"]:
            observed_env.update(env or {})
            output = (
                "[ok] sandbox unrestricted fs + enabled network · approval Never\n"
                "18 ok | 3 notes | 0 warn | 0 fail ok\n"
            )
        elif argv[0] == "bash":
            output = "EDSYS_LOGIN_SHELL_OK"
        else:
            output = "ok"
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    benchmark._command = fake_command

    passed, evidence, cleanup = benchmark._authority(Path("/unused"))

    assert passed is True
    assert cleanup == "not_applicable"
    assert observed_env["TERM"] == "xterm-256color"
    assert all(evidence["codex_doctor"]["contract"].values())


def test_ultra_requires_complete_model_evidence_and_cleanup(tmp_path, monkeypatch) -> None:
    benchmark = object.__new__(CapabilityBenchmark)
    benchmark.contract = {"timeouts": {"model_seconds": 60}}
    required = [
        "browser_mcp", "proxmox_mcp", "code_intelligence_mcp", "github_mcp",
        "cloudflare_mcp", "openai_docs_mcp", "login_shell", "network", "docker",
        "nvidia_gpu", "outside_project_file", "docx", "pdf", "spreadsheet",
        "presentation", "cleanup",
    ]
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "edsys_fleet.benchmark.validate_retained_canary",
        lambda retained_dir, challenge: {
            "status": "passed",
            "workbook_sheets": 2,
            "presentation_slides": 3,
        },
    )

    def fake_command(argv, timeout=60, env=None):
        del timeout, env
        output = Path(argv[argv.index("-o") + 1])
        output.write_text("EDSYS_ULTRA_BENCHMARK_OK\n", encoding="utf-8")
        artifact_dir = output.parent
        (artifact_dir / "ultra-evidence.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "controls": [
                        {"id": item, "status": "passed", "detail": "verified"}
                        for item in required
                    ],
                    "cleanup_passed": True,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    benchmark._command = fake_command
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    passed, evidence, cleanup = benchmark._ultra(artifact_dir)

    assert passed is True
    assert cleanup == "passed"
    assert evidence["controls_passed"] is True
    assert evidence["artifact_canary"]["status"] == "passed"
    assert evidence["cleanup_passed"] is True
