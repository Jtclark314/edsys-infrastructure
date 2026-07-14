from __future__ import annotations

import importlib.util
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "edsys-codex-report-runner.py"
SPEC = importlib.util.spec_from_file_location("edsys_codex_report_runner", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_master(tmp_path: Path, *, morning_rc: int = 0) -> Path:
    root = tmp_path / "EdSys-Master"
    scripts = root / "tools" / "codex-hub"
    scripts.mkdir(parents=True)
    (scripts / "edsys-morning-brief.py").write_text(
        f"import sys\nprint('# brief')\nraise SystemExit({morning_rc})\n",
        encoding="utf-8",
    )
    (scripts / "edsys-weekly-codex-maintenance.py").write_text(
        "print('# weekly')\n", encoding="utf-8"
    )
    (scripts / "edsys-rag-memory-hygiene-check.py").write_text(
        "print('# hygiene')\n", encoding="utf-8"
    )
    return root


def args(
    root: Path,
    state: Path,
    report: str = "morning-brief",
    retention: int = 2,
    timeout: int | None = None,
):
    argv = [
        report,
        "--edsys-master-root",
        str(root),
        "--state-root",
        str(state),
        "--retention",
        str(retention),
    ]
    if timeout is not None:
        argv.extend(("--timeout", str(timeout)))
    return MODULE.parse_args(argv)


def test_success_writes_private_atomic_report_and_status(tmp_path: Path) -> None:
    root = make_master(tmp_path)
    state = tmp_path / "state"

    assert MODULE.run(args(root, state)) == 0

    report_dir = state / "morning-brief"
    status = json.loads((report_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "success"
    assert status["exit_code"] == 0
    assert status["report_bytes"] == len(b"# brief\n")
    assert status["diagnostics_bytes"] == 0
    assert (report_dir / "latest.md").read_text(encoding="utf-8") == "# brief\n"
    assert not list(report_dir.glob("*.stderr.txt"))
    assert os.stat(report_dir / "latest.md").st_mode & 0o777 == 0o600
    assert os.stat(report_dir / "status.json").st_mode & 0o777 == 0o600
    assert os.stat(report_dir / ".run.lock").st_mode & 0o777 == 0o600
    assert os.stat(state).st_mode & 0o777 == 0o700
    assert os.stat(report_dir).st_mode & 0o777 == 0o700


def test_weekly_combines_maintenance_and_hygiene(tmp_path: Path) -> None:
    root = make_master(tmp_path)
    state = tmp_path / "state"

    assert MODULE.run(args(root, state, "weekly-maintenance")) == 0
    report = (state / "weekly-maintenance" / "latest.md").read_text(encoding="utf-8")
    assert "# weekly" in report
    assert "# hygiene" in report
    assert "\n---\n" in report


def test_failed_generator_is_recorded_and_returned(tmp_path: Path) -> None:
    root = make_master(tmp_path, morning_rc=7)
    state = tmp_path / "state"

    assert MODULE.run(args(root, state)) == 7
    status = json.loads(
        (state / "morning-brief" / "status.json").read_text(encoding="utf-8")
    )
    assert status["state"] == "failed"
    assert status["exit_code"] == 7


def test_timeout_is_failed_and_keeps_diagnostics_private(tmp_path: Path) -> None:
    root = make_master(tmp_path)
    script = root / "tools" / "codex-hub" / "edsys-morning-brief.py"
    script.write_text(
        "import sys, time\n"
        "print('# partial brief', flush=True)\n"
        "print('private timeout diagnostic', file=sys.stderr, flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"

    assert MODULE.run(args(root, state, timeout=1)) == 124

    report_dir = state / "morning-brief"
    status = json.loads((report_dir / "status.json").read_text(encoding="utf-8"))
    diagnostics = Path(status["diagnostics_path"])
    assert status["state"] == "failed"
    assert diagnostics.stat().st_mode & 0o777 == 0o600
    assert "Timed out after 1 seconds." in diagnostics.read_text(encoding="utf-8")
    assert "private timeout diagnostic" in diagnostics.read_text(encoding="utf-8")


def test_retention_prunes_old_reports_and_diagnostics(tmp_path: Path) -> None:
    report_dir = tmp_path / "state" / "morning-brief"
    report_dir.mkdir(parents=True)
    for stamp in ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]:
        (report_dir / f"{stamp}.md").write_text(stamp, encoding="utf-8")
        (report_dir / f"{stamp}.stderr.txt").write_text("private", encoding="utf-8")

    removed = MODULE.prune_history(report_dir, 2)

    assert removed == ["20260101T000000Z.md"]
    assert not (report_dir / "20260101T000000Z.stderr.txt").exists()
    assert len(list(report_dir.glob("20??????T??????Z.md"))) == 2


def test_refuses_symlinked_state_root(tmp_path: Path) -> None:
    root = make_master(tmp_path)
    real_state = tmp_path / "real-state"
    real_state.mkdir()
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(real_state, target_is_directory=True)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        MODULE.run(args(root, linked_state))


def test_nonblocking_lock_refuses_overlap(tmp_path: Path) -> None:
    root = make_master(tmp_path)
    state = tmp_path / "state"
    report_dir = state / "morning-brief"
    report_dir.mkdir(parents=True)
    lock_path = report_dir / ".run.lock"

    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert MODULE.run(args(root, state)) == 75

    assert not (report_dir / "status.json").exists()


def test_same_second_run_cannot_overwrite_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_master(tmp_path)
    state = tmp_path / "state"
    fixed = datetime(2026, 7, 13, 23, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(MODULE, "utc_now", lambda: fixed)

    assert MODULE.run(args(root, state)) == 0
    report_path = state / "morning-brief" / "20260713T235959Z.md"
    original = report_path.read_bytes()

    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        MODULE.run(args(root, state))

    assert report_path.read_bytes() == original


def test_timeout_output_decodes_bytes_without_repr() -> None:
    assert MODULE.timeout_output(b"partial\xff") == "partial\ufffd"
