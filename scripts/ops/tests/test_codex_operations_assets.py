from __future__ import annotations

from pathlib import Path

OPS_ROOT = Path(__file__).parents[1]
SYSTEMD_ROOT = OPS_ROOT / "systemd"

SERVICES = {
    "morning": SYSTEMD_ROOT / "edsys-morning-brief.service",
    "weekly": SYSTEMD_ROOT / "edsys-weekly-codex-maintenance.service",
    "golden": SYSTEMD_ROOT / "edsys-rag-golden-eval.service",
}

TIMERS = {
    "morning": (
        SYSTEMD_ROOT / "edsys-morning-brief.timer",
        "OnCalendar=*-*-* 06:00:00 America/New_York",
    ),
    "weekly": (
        SYSTEMD_ROOT / "edsys-weekly-codex-maintenance.timer",
        "OnCalendar=Mon *-*-* 07:30:00 America/New_York",
    ),
    "golden": (
        SYSTEMD_ROOT / "edsys-rag-golden-eval.timer",
        "OnCalendar=Mon *-*-* 05:00:00 America/New_York",
    ),
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_missing_prerequisites_fail_instead_of_skipping() -> None:
    combined = "\n".join(read(path) for path in SERVICES.values())

    assert "ConditionPath" not in combined
    assert "AssertPathIsDirectory=/srv/edsys/EdSys-Master" in combined
    assert "AssertPathIsMountPoint=/mnt/ai-store" in read(SERVICES["weekly"])
    assert combined.count("AssertPathExists=/home/jeremy/.local/bin/codex") == 2

    golden = read(SERVICES["golden"])
    for assertion in (
        "AssertPathIsMountPoint=/mnt/ai-store",
        "AssertPathExists=/etc/edsys-secrets/edsys-rag-eval.env",
        "AssertPathExists=/srv/edsys/EdSys-Master/tools/rag-eval/edsys-rag-golden-eval.py",
        "AssertPathExists=/srv/edsys/EdSys-Master/data/rag-golden-queries.yml",
        "AssertPathExists=/home/jeremy/code/edsys-ai-portal/.venv/bin/python",
        "AssertPathExists=/home/jeremy/code/edsys-ai-portal/tools/rag_eval_runner.py",
    ):
        assert assertion in golden


def test_services_keep_private_hardening_contract() -> None:
    for path in SERVICES.values():
        service = read(path)
        for directive in (
            "UMask=0077",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "CapabilityBoundingSet=",
        ):
            assert directive in service, f"{directive} missing from {path.name}"


def test_only_golden_eval_loads_the_root_private_credential() -> None:
    morning = read(SERVICES["morning"])
    weekly = read(SERVICES["weekly"])
    golden = read(SERVICES["golden"])

    assert "EnvironmentFile=" not in morning
    assert "EnvironmentFile=" not in weekly
    assert "EnvironmentFile=/etc/edsys-secrets/edsys-rag-eval.env" in golden
    assert "WorkingDirectory=/var/lib/edsys-rag-eval" in golden
    assert "InaccessiblePaths=-/home/jeremy/code/edsys-ai-portal/.env" in golden
    assert "-/home/jeremy/.codex" in golden
    assert "--judge heuristic" in golden
    assert "--cloud-confirmed" not in golden
    assert "API_KEY" not in golden


def test_report_units_prefer_the_active_standalone_codex_cli() -> None:
    expected = (
        "Environment=PATH=/home/jeremy/.local/bin:/home/jeremy/bin:"
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )

    assert expected in read(SERVICES["morning"])
    assert expected in read(SERVICES["weekly"])
    assert expected not in read(SERVICES["golden"])
    assert (
        "ExecStart=/usr/bin/python3 /usr/local/libexec/edsys-codex-report-runner.py"
        in read(SERVICES["morning"])
    )
    assert (
        "ExecStart=/usr/bin/python3 /usr/local/libexec/edsys-codex-report-runner.py"
        in read(SERVICES["weekly"])
    )


def test_timers_are_persistent_and_pin_eastern_wall_clock() -> None:
    for path, expected_calendar in TIMERS.values():
        timer = read(path)
        assert expected_calendar in timer
        assert "Persistent=true" in timer
        assert "AccuracySec=1min" in timer


def test_installer_keeps_secret_provisioning_separate() -> None:
    installer = read(OPS_ROOT / "install-codex-operations.sh")

    assert 'install -d -m 0700 -o root -g root "${secret_dir}"' in installer
    assert 'install -m 0600 -o root -g root /dev/null "${secret_env}"' not in installer
    assert 'link_count="$(stat -c \'%h\' "${secret_env}")"' in installer
    assert 'allowed = {"PORTAL_USERNAME", "PORTAL_PASSWORD"}' in installer
    assert "transaction_active=true" in installer
    assert "restore_install_transaction" in installer
    assert 'systemctl is-enabled --quiet "${timer}"' in installer
    assert 'systemctl is-active --quiet "${timer}"' in installer
