from __future__ import annotations

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
