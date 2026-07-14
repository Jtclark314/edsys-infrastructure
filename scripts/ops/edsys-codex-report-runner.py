#!/usr/bin/env python3
"""Run read-only Codex reports and keep their output private and bounded.

The report generators remain source-controlled in EdSys-Master.  This wrapper
provides the operational pieces they intentionally do not own: overlap
prevention, atomic report/status publication, private permissions, timeouts,
and bounded retention.  Report bodies are never written to the systemd
journal.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os

# Child commands are fixed in REPORTS, resolved below a validated repository
# root, invoked without a shell, and never accepted from command-line input.
import subprocess  # nosec B404
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EDSYS_MASTER_ROOT = Path("/srv/edsys/EdSys-Master")
DEFAULT_STATE_ROOT = Path("/var/lib/edsys-codex-reports")


@dataclass(frozen=True)
class ReportDefinition:
    name: str
    commands: tuple[tuple[str, ...], ...]
    retention: int
    timeout_seconds: int


REPORTS = {
    "morning-brief": ReportDefinition(
        name="morning-brief",
        commands=(("tools/codex-hub/edsys-morning-brief.py",),),
        retention=31,
        timeout_seconds=15 * 60,
    ),
    "weekly-maintenance": ReportDefinition(
        name="weekly-maintenance",
        commands=(
            ("tools/codex-hub/edsys-weekly-codex-maintenance.py",),
            ("tools/codex-hub/edsys-rag-memory-hygiene-check.py",),
        ),
        retention=13,
        timeout_seconds=20 * 60,
    ),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one EdSys read-only Codex report with private atomic output."
    )
    parser.add_argument("report", choices=sorted(REPORTS))
    parser.add_argument(
        "--edsys-master-root",
        type=Path,
        default=Path(os.environ.get("EDSYS_MASTER_ROOT", DEFAULT_EDSYS_MASTER_ROOT)),
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(os.environ.get("EDSYS_CODEX_REPORT_ROOT", DEFAULT_STATE_ROOT)),
    )
    parser.add_argument("--retention", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    return parser.parse_args(argv)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeError(
            f"Atomic-write parent is not a safe directory: {path.parent}"
        )
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp_path.unlink(missing_ok=True)


def validate_root(root: Path, definition: ReportDefinition) -> None:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError(f"EdSys-Master root is not a safe directory: {root}")
    for command in definition.commands:
        script = root / command[0]
        if not script.is_file() or script.is_symlink():
            raise RuntimeError(
                f"Required report generator is not a regular file: {script}"
            )


def report_command(root: Path, command: tuple[str, ...]) -> list[str]:
    script = root / command[0]
    return [sys.executable, str(script), *command[1:]]


def timeout_output(value: str | bytes | None) -> str:
    """Normalize TimeoutExpired output without rendering bytes as a repr."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_generators(
    root: Path, definition: ReportDefinition, timeout_seconds: int
) -> tuple[int, str, str]:
    sections: list[str] = []
    diagnostics: list[str] = []
    overall_rc = 0
    deadline = time.monotonic() + timeout_seconds

    for index, command in enumerate(definition.commands):
        remaining = max(1, int(deadline - time.monotonic()))
        if remaining <= 1 and index:
            overall_rc = 124
            diagnostics.append(
                "Overall report timeout expired before all generators ran."
            )
            break
        try:
            completed = subprocess.run(  # nosec B603
                report_command(root, command),
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=remaining,
                check=False,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            stdout = completed.stdout.rstrip()
            stderr = completed.stderr.rstrip()
            if stdout:
                sections.append(stdout)
            if stderr:
                diagnostics.append(f"[{command[0]}]\n{stderr}")
            if completed.returncode and overall_rc == 0:
                overall_rc = completed.returncode
        except subprocess.TimeoutExpired as exc:
            overall_rc = 124
            timed_out_stdout = timeout_output(exc.stdout).rstrip()
            timed_out_stderr = timeout_output(exc.stderr).rstrip()
            if timed_out_stdout:
                sections.append(timed_out_stdout)
            timeout_diagnostic = f"[{command[0]}]\nTimed out after {remaining} seconds."
            if timed_out_stderr:
                timeout_diagnostic += f"\n{timed_out_stderr}"
            diagnostics.append(timeout_diagnostic)
            break

    return (
        overall_rc,
        "\n\n---\n\n".join(sections).rstrip() + "\n",
        "\n\n".join(diagnostics).rstrip(),
    )


def prune_history(report_dir: Path, retention: int) -> list[str]:
    if retention < 1:
        raise ValueError("retention must be at least 1")
    candidates = sorted(
        (
            path
            for path in report_dir.glob("20??????T??????Z.md")
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    removed: list[str] = []
    for path in candidates[retention:]:
        stderr_path = path.with_suffix(".stderr.txt")
        path.unlink()
        stderr_path.unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def ensure_private_directory(path: Path, label: str) -> Path:
    """Create a private directory while refusing symlinked path components."""

    if not path.is_absolute():
        raise RuntimeError(f"{label} must be an absolute path: {path}")
    if path.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    resolved_before_create = path.resolve(strict=False)
    if resolved_before_create != path:
        raise RuntimeError(
            f"{label} contains a symlink or non-canonical component: {path}"
        )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path:
        raise RuntimeError(f"{label} is not a safe directory: {path}")
    os.chmod(path, 0o700)
    return path


def run(args: argparse.Namespace) -> int:
    definition = REPORTS[args.report]
    retention = args.retention if args.retention is not None else definition.retention
    timeout_seconds = (
        args.timeout if args.timeout is not None else definition.timeout_seconds
    )
    if retention < 1 or timeout_seconds < 1:
        raise ValueError("retention and timeout must both be positive")

    if args.edsys_master_root.is_symlink():
        raise RuntimeError(
            f"EdSys-Master root must not be a symlink: {args.edsys_master_root}"
        )
    root = args.edsys_master_root.resolve(strict=True)
    validate_root(root, definition)

    state_root = ensure_private_directory(args.state_root, "State root")
    report_dir = ensure_private_directory(
        state_root / definition.name, "Report directory"
    )

    lock_path = report_dir / ".run.lock"
    if lock_path.is_symlink():
        raise RuntimeError(f"Run lock must not be a symlink: {lock_path}")
    with lock_path.open("a+b") as lock_handle:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                f"{definition.name}: another run is active; refusing overlap.",
                file=sys.stderr,
            )
            return 75

        started = utc_now()
        started_monotonic = time.monotonic()
        run_id = started.strftime("%Y%m%dT%H%M%SZ")
        report_path = report_dir / f"{run_id}.md"
        stderr_path = report_dir / f"{run_id}.stderr.txt"
        if (
            report_path.exists()
            or report_path.is_symlink()
            or stderr_path.exists()
            or stderr_path.is_symlink()
        ):
            raise RuntimeError(f"Refusing to overwrite existing run artifact: {run_id}")

        rc, report, diagnostics = run_generators(root, definition, timeout_seconds)
        atomic_write(report_path, report.encode("utf-8"))
        atomic_write(report_dir / "latest.md", report.encode("utf-8"))
        if diagnostics:
            atomic_write(stderr_path, (diagnostics + "\n").encode("utf-8"))
        else:
            stderr_path.unlink(missing_ok=True)

        removed = prune_history(report_dir, retention)
        completed = utc_now()
        status = {
            "schema_version": 1,
            "report": definition.name,
            "run_id": run_id,
            "started_at": started.replace(microsecond=0).isoformat(),
            "completed_at": completed.replace(microsecond=0).isoformat(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 3),
            "exit_code": rc,
            "state": "success" if rc == 0 else "failed",
            "report_path": str(report_path),
            "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
            "report_bytes": len(report.encode("utf-8")),
            "diagnostics_path": str(stderr_path) if diagnostics else None,
            "diagnostics_bytes": len(diagnostics.encode("utf-8")) if diagnostics else 0,
            "retention": retention,
            "pruned_count": len(removed),
        }
        atomic_write(
            report_dir / "status.json",
            (json.dumps(status, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        # Deliberately print metadata only; report content remains private.
        print(json.dumps(status, separators=(",", ":"), sort_keys=True))
        return rc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"{args.report}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
