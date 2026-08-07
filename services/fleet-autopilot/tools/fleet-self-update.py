#!/usr/bin/env python3
"""Externally watched, atomic Fleet host-agent self-update transaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMMANDS = ("edsys-fleet", "edsys-fleet-worker", "edsys-proxmox-mcp")
UNITS = (
    "edsys-fleet-worker.service",
    "edsys-fleet-collect.service",
    "edsys-fleet-collect.timer",
    "edsys-fleet-benchmark-daily.service",
    "edsys-fleet-benchmark-daily.timer",
    "edsys-fleet-benchmark-weekly.service",
    "edsys-fleet-benchmark-weekly.timer",
    "edsys-fleet-backup.service",
    "edsys-fleet-backup.timer",
)
TIMER_UNITS = tuple(name for name in UNITS if name.endswith(".timer"))


class UpdateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(argv: list[str], *, timeout: int = 300, check: bool = True, env=None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise UpdateError(f"Command failed ({argv[0]}): {detail or result.returncode}")
    return result


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def checkpoint_sha256(checkpoint: dict[str, Any], backup_dir: Path) -> str:
    digest = hashlib.sha256(
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode()
    )
    for path in sorted(backup_dir.rglob("*")):
        relative = path.relative_to(backup_dir).as_posix()
        digest.update(relative.encode())
        if path.is_symlink():
            digest.update(b"symlink\0" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"file\0" + hashlib.sha256(path.read_bytes()).digest())
        elif path.is_dir():
            digest.update(b"directory\0")
    return digest.hexdigest()


def read_state(run_dir: Path) -> dict[str, Any]:
    value = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise UpdateError("Invalid self-update state")
    return value


def source_identity(source: Path) -> dict[str, Any]:
    repo = Path(run(["git", "-C", str(source), "rev-parse", "--show-toplevel"]).stdout.strip())
    relative = source.resolve().relative_to(repo.resolve())
    branch = run(["git", "-C", str(repo), "branch", "--show-current"]).stdout.strip()
    commit = run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
    upstream = run(["git", "-C", str(repo), "rev-parse", "origin/main"]).stdout.strip()
    dirty = run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", str(relative)],
        check=False,
    ).stdout.strip()
    if branch != "main" or commit != upstream or dirty:
        raise UpdateError("Fleet source must be clean authoritative origin/main before promotion")
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", commit, str(relative)],
        capture_output=True,
        timeout=120,
        check=True,
    ).stdout
    return {
        "repo": str(repo),
        "relative_path": str(relative),
        "branch": branch,
        "commit": commit,
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
    }


def sign_manifest(manifest_path: Path, install_root: Path) -> dict[str, Any]:
    key = install_root / "deployment-signing-key"
    if not key.exists():
        run(
            [
                "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                "edsys-fleet-deployment", "-f", str(key),
            ]
        )
    os.chmod(key, 0o600)
    os.chmod(Path(f"{key}.pub"), 0o600)
    signature = Path(f"{manifest_path}.sig")
    signature.unlink(missing_ok=True)
    run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "edsys-fleet", str(manifest_path)])
    public = Path(f"{key}.pub").read_text(encoding="utf-8").strip()
    allowed = manifest_path.parent / "allowed-signers"
    allowed.write_text(f"edsys-fleet {public}\n", encoding="utf-8")
    os.chmod(allowed, 0o600)
    with manifest_path.open("rb") as payload:
        verify = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I",
                "edsys-fleet", "-n", "edsys-fleet", "-s", str(signature),
            ],
            stdin=payload,
            text=False,
            capture_output=True,
            timeout=30,
            check=False,
        )
    if verify.returncode != 0:
        raise UpdateError("Fleet candidate manifest signature verification failed")
    return {
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "signature_sha256": hashlib.sha256(signature.read_bytes()).hexdigest(),
        "verified": True,
    }


def snapshot_path(path: Path, backup_dir: Path, key: str) -> dict[str, Any]:
    if path.is_symlink():
        return {"type": "symlink", "target": os.readlink(path)}
    if path.exists():
        destination = backup_dir / key
        if path.is_dir():
            shutil.copytree(path, destination, symlinks=True)
            kind = "directory"
        else:
            shutil.copy2(path, destination)
            kind = "file"
        return {"type": kind, "backup": str(destination)}
    return {"type": "absent"}


def restore_path(path: Path, record: dict[str, Any]) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)
    kind = record["type"]
    if kind == "symlink":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(record["target"])
    elif kind == "file":
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(record["backup"], path)
    elif kind == "directory":
        shutil.copytree(record["backup"], path, symlinks=True)


def atomic_symlink(target: str, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def assert_no_active_mutation(state_root: Path) -> None:
    database = state_root / "fleet-control.sqlite"
    if not database.exists():
        return
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    try:
        rows = connection.execute(
            "SELECT id, action, state FROM jobs WHERE state IN "
            "('pending','running','awaiting_agent') AND action NOT IN ('inspect','verify')"
        ).fetchall()
    finally:
        connection.close()
    if rows:
        raise UpdateError("A Fleet mutation is active; self-update is blocked")


def install_units(source: Path, unit_dir: Path) -> None:
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name in UNITS:
        shutil.copy2(source / "systemd" / name, unit_dir / name)
        os.chmod(unit_dir / name, 0o644)


def switch_to_release(release: Path, install_root: Path, bin_dir: Path) -> None:
    atomic_symlink(str(release), install_root / "current")
    for name in COMMANDS:
        atomic_symlink(str(install_root / "current" / "bin" / name), bin_dir / name)


def systemd_refresh(*, restart: bool) -> None:
    run(["systemctl", "--user", "daemon-reload"], timeout=30)
    available_timers = [
        name
        for name in TIMER_UNITS
        if run(["systemctl", "--user", "cat", name], check=False, timeout=20).returncode == 0
    ]
    run(
        ["systemctl", "--user", "enable", "edsys-fleet-worker.service", *available_timers],
        timeout=60,
    )
    if restart:
        run(["systemctl", "--user", "restart", "edsys-fleet-worker.service"], timeout=90)
        for timer in available_timers:
            run(["systemctl", "--user", "start", timer], timeout=30)


def validate_release(release: Path, state_root: Path) -> dict[str, Any]:
    cli = release / "bin" / "edsys-fleet"
    db = json.loads(run([str(cli), "db-check"], timeout=60).stdout)
    if db.get("status") != "ok":
        raise UpdateError("Fleet database check failed")
    run([str(cli), "collect"], timeout=180)
    snapshot = json.loads((state_root / "snapshot.json").read_text(encoding="utf-8"))
    if snapshot.get("policy_version") != "2.0.0":
        raise UpdateError("Fleet policy v2 snapshot was not produced")
    components = list(snapshot.get("components") or [])
    if not components or not all(item.get("implemented") for item in components):
        raise UpdateError("Fleet component registry is incomplete")
    active = run(
        ["systemctl", "--user", "is-active", "edsys-fleet-worker.service"],
        timeout=30,
    ).stdout.strip()
    if active != "active":
        raise UpdateError("Fleet worker did not remain active")
    return {
        "db_check": "ok",
        "policy_version": snapshot.get("policy_version"),
        "components": len(components),
        "worker": active,
        "finalizer": (snapshot.get("finalizer") or {}).get("status"),
    }


def restore_checkpoint(
    run_dir: Path,
    *,
    terminal_status: str = "rolled_back",
    allow_accepted: bool = False,
) -> dict[str, Any]:
    state = read_state(run_dir)
    if (
        (state.get("status") == "accepted" and not allow_accepted)
        or str(state.get("status") or "").startswith("rolled_back")
    ):
        return state
    paths = state["paths"]
    for name, record in state["checkpoint"]["units"].items():
        restore_path(Path(paths["unit_dir"]) / name, record)
    for name, record in state["checkpoint"]["commands"].items():
        restore_path(Path(paths["bin_dir"]) / name, record)
    restore_path(Path(paths["install_root"]) / "current", state["checkpoint"]["current"])
    systemd_refresh(restart=True)
    state.update({"status": terminal_status, "rolled_back_at": utc_now()})
    write_private_json(run_dir / "state.json", state)
    return state


def record_fleet_acceptance(
    release: Path,
    state_root: Path,
    run_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    checkpoint = dict(state["checkpoint"])
    rollback_version = str(
        (checkpoint.get("current") or {}).get("target")
        or (checkpoint.get("commands", {}).get("edsys-fleet") or {}).get("target")
        or "legacy-install"
    )
    payload = {
        "state_root": str(state_root),
        "host_id": "9950x",
        "component": "fleet-host-agent",
        "adapter": "fleet-host-agent",
        "candidate_version": str(state["candidate"]["commit"]),
        "rollback_version": rollback_version,
        "checkpoint_sha256": checkpoint_sha256(checkpoint, run_dir / "checkpoint"),
        "artifact_ref": f"private://fleet-self-update/{state['run_id']}/checkpoint",
        "run_id": state["run_id"],
        "validation": state.get("final_validation") or {},
    }
    record = run_dir / "fleet-acceptance.json"
    write_private_json(record, payload)
    code = """
import json, sys
from pathlib import Path
from edsys_fleet.store import FleetStore
p=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
s=FleetStore(Path(p['state_root']))
r=s.add_recovery_point({
 'host_id':p['host_id'],'component':p['component'],'version':p['rollback_version'],
 'checksum':p['checkpoint_sha256'],'checkpoint_type':'fleet-self-update-checkpoint',
 'artifact_ref':p['artifact_ref'],'compatible':True,'verified':True,'accepted':True,
 'last_working':True,'metadata':{'run_id':p['run_id']},
})
q=s.qualify_adapter(adapter=p['adapter'],host_id=p['host_id'],component=p['component'],
 version=p['candidate_version'],rollback_rehearsed=True,
 evidence={'run_id':p['run_id'],'recovery_point_id':r['id'],'validation':p['validation']})
print(json.dumps({'recovery_point_id':r['id'],'qualification':q['status']},sort_keys=True))
"""
    result = run([str(release / "bin" / "python"), "-c", code, str(record)], timeout=90)
    value = json.loads(result.stdout)
    if value.get("qualification") != "qualified" or not value.get("recovery_point_id"):
        raise UpdateError("Fleet could not persist its self-update qualification evidence")
    return value


def disarm_watchdog(unit: str) -> None:
    for suffix in (".timer", ".service"):
        run(["systemctl", "--user", "stop", f"{unit}{suffix}"], check=False, timeout=30)
        run(["systemctl", "--user", "reset-failed", f"{unit}{suffix}"], check=False, timeout=30)


def apply(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    install_root = args.install_root.expanduser().resolve()
    state_root = args.state_root.expanduser().resolve()
    bin_dir = args.bin_dir.expanduser().resolve()
    unit_dir = args.unit_dir.expanduser().resolve()
    if not (source / "pyproject.toml").is_file() or not (source / "systemd").is_dir():
        raise UpdateError("Invalid Fleet source tree")
    assert_no_active_mutation(state_root)
    identity = source_identity(source)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{identity['commit'][:10]}"
    run_dir = args.run_root.expanduser().resolve() / run_id
    run_dir.mkdir(parents=True, mode=0o700)
    backup_dir = run_dir / "checkpoint"
    backup_dir.mkdir(mode=0o700)
    releases = install_root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / run_id
    if release.exists():
        raise UpdateError("Fleet release already exists")

    checkpoint = {
        "current": snapshot_path(install_root / "current", backup_dir, "current"),
        "commands": {
            name: snapshot_path(bin_dir / name, backup_dir, f"command-{name}")
            for name in COMMANDS
        },
        "units": {
            name: snapshot_path(unit_dir / name, backup_dir, f"unit-{name}")
            for name in UNITS
        },
    }
    state = {
        "schema_version": 1,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": "staging",
        "created_at": utc_now(),
        "candidate": identity,
        "paths": {
            "source": str(source),
            "release": str(release),
            "install_root": str(install_root),
            "state_root": str(state_root),
            "bin_dir": str(bin_dir),
            "unit_dir": str(unit_dir),
        },
        "checkpoint": checkpoint,
    }
    write_private_json(run_dir / "state.json", state)
    run([sys.executable, "-m", "venv", str(release)], timeout=120)
    requirements = run_dir / "requirements.lock"
    run(
        [
            "uv", "export", "--project", str(source), "--frozen", "--no-dev",
            "--no-emit-project", "--format", "requirements-txt",
            "--output-file", str(requirements),
        ],
        timeout=120,
    )
    os.chmod(requirements, 0o600)
    run(
        [
            str(release / "bin" / "pip"), "install", "--disable-pip-version-check",
            "--require-hashes", "-r", str(requirements),
        ],
        timeout=600,
    )
    run(
        [
            str(release / "bin" / "pip"), "install", "--disable-pip-version-check",
            "--no-deps", str(source),
        ],
        timeout=600,
    )
    manifest = run_dir / "candidate-manifest.json"
    write_private_json(
        manifest,
        {
            "schema_version": 1,
            "run_id": run_id,
            "component": "fleet-host-agent",
            "host": "9950x",
            "candidate": identity,
            "dependency_lock_sha256": hashlib.sha256(
                (source / "uv.lock").read_bytes()
            ).hexdigest(),
            "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
            "staged_release_sha256": checkpoint_sha256({}, release),
        },
    )
    state["candidate_signature"] = sign_manifest(manifest, install_root)
    write_private_json(run_dir / "state.json", state)
    validation_root = run_dir / "validation-state"
    validation_env = dict(os.environ)
    validation_env["EDSYS_FLEET_STATE_ROOT"] = str(validation_root)
    run([str(release / "bin" / "edsys-fleet"), "db-check"], timeout=60, env=validation_env)

    helper = run_dir / "fleet-self-update.py"
    shutil.copy2(Path(__file__), helper)
    os.chmod(helper, 0o700)
    watchdog = f"edsys-fleet-self-update-{run_id}".lower()
    state.update({"status": "checkpointed", "watchdog_unit": watchdog})
    write_private_json(run_dir / "state.json", state)
    run(
        [
            "systemd-run", "--user", f"--unit={watchdog}", "--on-active=45min",
            "--timer-property=AccuracySec=5s", sys.executable, str(helper),
            "watchdog", "--run-dir", str(run_dir),
        ],
        timeout=60,
    )

    try:
        install_units(source, unit_dir)
        switch_to_release(release, install_root, bin_dir)
        systemd_refresh(restart=True)
        first = validate_release(release, state_root)
        state.update({"status": "qualification_rollback", "first_validation": first})
        write_private_json(run_dir / "state.json", state)

        # Mandatory real rehearsal: restore the exact prior links/units, prove
        # the old worker starts, then reapply and independently validate v2.
        for name, record in checkpoint["units"].items():
            restore_path(unit_dir / name, record)
        for name, record in checkpoint["commands"].items():
            restore_path(bin_dir / name, record)
        restore_path(install_root / "current", checkpoint["current"])
        systemd_refresh(restart=True)
        prior_active = run(
            ["systemctl", "--user", "is-active", "edsys-fleet-worker.service"], timeout=30
        ).stdout.strip()
        if prior_active != "active":
            raise UpdateError("Prior Fleet worker failed the rollback rehearsal")

        install_units(source, unit_dir)
        switch_to_release(release, install_root, bin_dir)
        systemd_refresh(restart=True)
        final = validate_release(release, state_root)
        state.update(
            {
                "status": "accepted",
                "accepted_at": utc_now(),
                "rollback_rehearsed": True,
                "prior_worker_after_rollback": prior_active,
                "final_validation": final,
            }
        )
        state["fleet_record"] = record_fleet_acceptance(
            release, state_root, run_dir, state
        )
        write_private_json(run_dir / "state.json", state)
        disarm_watchdog(watchdog)
        return state
    except Exception:
        try:
            restore_checkpoint(run_dir)
        finally:
            disarm_watchdog(watchdog)
        raise


def watchdog(args: argparse.Namespace) -> dict[str, Any]:
    state = read_state(args.run_dir)
    if state.get("status") == "accepted":
        return state
    return restore_checkpoint(args.run_dir, terminal_status="rolled_back_by_watchdog")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    deploy = sub.add_parser("apply")
    deploy.add_argument("--source", type=Path, required=True)
    deploy.add_argument(
        "--install-root", type=Path,
        default=Path.home() / ".local/share/edsys-fleet-autopilot",
    )
    deploy.add_argument(
        "--run-root", type=Path,
        default=Path.home() / ".local/state/edsys-fleet-self-update",
    )
    deploy.add_argument(
        "--state-root", type=Path,
        default=Path("/opt/edsys-workhorse/edsys-ai-portal/data/fleet"),
    )
    deploy.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    deploy.add_argument(
        "--unit-dir", type=Path, default=Path.home() / ".config/systemd/user"
    )
    watch = sub.add_parser("watchdog")
    watch.add_argument("--run-dir", type=Path, required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--run-dir", type=Path, required=True)
    status = sub.add_parser("status")
    status.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "apply":
            value = apply(args)
        elif args.command == "watchdog":
            value = watchdog(args)
        elif args.command == "rollback":
            value = restore_checkpoint(
                args.run_dir,
                terminal_status="rolled_back_manually",
                allow_accepted=True,
            )
            disarm_watchdog(str(value.get("watchdog_unit") or ""))
        else:
            value = read_state(args.run_dir)
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, UpdateError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
