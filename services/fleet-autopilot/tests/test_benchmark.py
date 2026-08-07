from __future__ import annotations

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
