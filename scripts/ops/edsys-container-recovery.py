#!/usr/bin/env python3
"""Ordered, non-destructive Docker Compose recovery for the 9950x EdSys host."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml


DEFAULT_MANIFEST = Path("/etc/edsys-container-recovery/manifest.yaml")
LOCK_PATH = Path("/run/lock/edsys-container-recovery.lock")
STATUS_PATH = Path("/run/edsys/container-recovery-status.json")
STATE_PATH = Path("/var/lib/edsys-container-recovery/last-recovery.json")


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}", flush=True)


def run(
    command: list[str],
    *,
    check: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=check,
        timeout=timeout,
        env={**os.environ, "COMPOSE_IGNORE_ORPHANS": "true"},
    )


def load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("manifest must be a mapping with version: 1")
    if not isinstance(data.get("tiers"), list) or not data["tiers"]:
        raise ValueError("manifest must contain at least one recovery tier")
    return data


def acquire_lock(wait_seconds: int) -> Any:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("w", encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                log("another recovery or audit run already owns the lock; exiting")
                raise SystemExit(0)
            time.sleep(1)
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def write_status(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATUS_PATH)


def notify(manifest: dict[str, Any], event: str, detail: str) -> None:
    notify_file = Path(
        manifest.get("notification_url_file", "/etc/edsys-container-recovery/notify-url")
    )
    if not notify_file.exists():
        log("out-of-band notification URL is not configured; journal/status evidence retained")
        return
    url = notify_file.read_text(encoding="utf-8").strip()
    if not url:
        return
    body = json.dumps(
        {
            "source": "9950x",
            "component": "edsys-container-recovery",
            "event": event,
            "detail": detail,
        }
    ).encode("utf-8")
    try:
        request = Request(url, data=body, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            log(f"out-of-band notification returned HTTP {response.status}")
    except Exception as exc:  # notification must not hide recovery outcome
        log(f"out-of-band notification failed: {type(exc).__name__}")


def docker_ready(wait_seconds: int) -> bool:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        result = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            log(f"Docker API ready: {result.stdout.strip()}")
            return True
        time.sleep(2)
    return False


def exact_mount_ready(path: str) -> bool:
    result = run(["findmnt", "--mountpoint", path, "--noheadings"], timeout=10)
    return result.returncode == 0


def container_index() -> dict[tuple[str, str], list[dict[str, Any]]]:
    result = run(["docker", "ps", "-aq"], check=True, timeout=15)
    ids = result.stdout.split()
    if not ids:
        return {}
    inspected = json.loads(run(["docker", "inspect", *ids], check=True, timeout=60).stdout)
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in inspected:
        labels = item.get("Config", {}).get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        service = labels.get("com.docker.compose.service")
        if project and service:
            index.setdefault((project, service), []).append(item)
    return index


def compose_prefix(project: dict[str, Any]) -> list[str]:
    command = ["docker", "compose", "--ansi", "never", "--project-name", project["name"]]
    for compose_file in project["files"]:
        command.extend(["--file", compose_file])
    return command


def project_preflight(project: dict[str, Any], index: dict[tuple[str, str], list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    for compose_file in project["files"]:
        if not Path(compose_file).is_file():
            errors.append(f"missing Compose file: {compose_file}")
    for service in project.get("services", []):
        if not index.get((project["name"], service)):
            errors.append(f"missing existing container for {project['name']}/{service}")
    return errors


def project_state_errors(
    project: dict[str, Any],
    index: dict[tuple[str, str], list[dict[str, Any]]],
    *,
    require_healthy: bool = False,
) -> list[str]:
    errors: list[str] = []
    for service in project.get("services", []):
        items = index.get((project["name"], service), [])
        if not items:
            errors.append(f"{project['name']}/{service}: missing")
            continue
        if not any(item.get("State", {}).get("Status") == "running" for item in items):
            errors.append(f"{project['name']}/{service}: not running")
            continue
        for item in items:
            health = (item.get("State", {}).get("Health") or {}).get("Status")
            if health == "unhealthy":
                errors.append(f"{project['name']}/{service}: unhealthy")
            elif require_healthy and health == "starting":
                errors.append(f"{project['name']}/{service}: health starting")
    return errors


def services_needing_start(
    project: dict[str, Any], index: dict[tuple[str, str], list[dict[str, Any]]]
) -> list[str]:
    stopped: list[str] = []
    for service in project.get("services", []):
        items = index.get((project["name"], service), [])
        if not items or not any(item.get("State", {}).get("Status") == "running" for item in items):
            stopped.append(service)
    return stopped


def wait_for_started_project(project: dict[str, Any], services: list[str]) -> list[str]:
    deadline = time.monotonic() + int(project.get("wait_timeout_seconds", 180))
    last_errors: list[str] = []
    selected = {**project, "services": services}
    while time.monotonic() < deadline:
        last_errors = project_state_errors(
            selected, container_index(), require_healthy=True
        )
        if not last_errors:
            return []
        time.sleep(2)
    return last_errors


def probe(check: dict[str, Any]) -> tuple[bool, str]:
    codes = set(int(code) for code in check.get("codes", [200]))
    retries = int(check.get("retries", 12))
    delay = float(check.get("delay_seconds", 5))
    timeout = float(check.get("timeout_seconds", 5))
    url = check["url"]
    last = "no response"
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers={"User-Agent": "EdSys-container-recovery/1.0"})
            with urlopen(request, timeout=timeout) as response:
                code = int(response.status)
        except HTTPError as exc:
            code = int(exc.code)
        except (URLError, TimeoutError, OSError) as exc:
            last = type(exc).__name__
            code = 0
        if code in codes:
            return True, f"{url} HTTP {code} attempt {attempt}"
        if code:
            last = f"HTTP {code}"
        if attempt < retries:
            time.sleep(delay)
    return False, f"{url} failed after {retries} attempts ({last})"


def cooldown_active(manifest: dict[str, Any]) -> bool:
    cooldown = int(manifest.get("cooldown_seconds", 300))
    if not STATE_PATH.exists():
        return False
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        age = time.time() - float(state.get("started_epoch", 0))
    except Exception:
        return False
    return 0 <= age < cooldown


def record_attempt(mode: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"mode": mode, "started_epoch": time.time()}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def audit(manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not docker_ready(int(manifest.get("docker_wait_seconds", 60))):
        return False, ["Docker API did not become ready"]
    for mount in manifest.get("required_mounts", []):
        if not exact_mount_ready(mount):
            errors.append(f"required mount is not mounted: {mount}")
    index = container_index()
    for tier in manifest["tiers"]:
        for mount in tier.get("required_mounts", []):
            if not exact_mount_ready(mount):
                errors.append(f"tier {tier['name']} mount missing: {mount}")
        for project in tier.get("projects", []):
            errors.extend(project_state_errors(project, index))
            for check in project.get("healthchecks", []):
                ok, detail = probe({**check, "retries": check.get("audit_retries", 1)})
                log(("PASS " if ok else "FAIL ") + detail)
                if not ok:
                    errors.append(detail)
    return not errors, errors


def recover(manifest: dict[str, Any], *, dry_run: bool, force: bool) -> tuple[bool, list[str]]:
    maintenance_flag = Path(
        manifest.get("maintenance_flag", "/run/edsys/container-recovery.disabled")
    )
    if maintenance_flag.exists() and not force:
        log(f"maintenance flag present at {maintenance_flag}; automatic recovery suppressed")
        return True, []
    if cooldown_active(manifest) and not force:
        log("recovery cooldown is active; refusing a restart storm")
        return True, []
    if not dry_run:
        record_attempt("recover")
    if not docker_ready(int(manifest.get("docker_wait_seconds", 60))):
        return False, ["Docker API did not become ready"]
    errors: list[str] = []
    for mount in manifest.get("required_mounts", []):
        if not exact_mount_ready(mount):
            errors.append(f"required mount is not mounted: {mount}")
    if errors:
        return False, errors

    for tier_number, tier in enumerate(manifest["tiers"], start=1):
        tier_name = tier["name"]
        log(f"tier {tier_number} start: {tier_name}")
        tier_errors: list[str] = []
        for mount in tier.get("required_mounts", []):
            if not exact_mount_ready(mount):
                tier_errors.append(f"tier {tier_name} mount missing: {mount}")
        index = container_index()
        for project in tier.get("projects", []):
            preflight = project_preflight(project, index)
            if preflight:
                tier_errors.extend(preflight)
                continue
            stopped_services = services_needing_start(project, index)
            command = compose_prefix(project) + ["start", *stopped_services]
            if stopped_services:
                log("PLAN " + shlex.join(command))
            else:
                log(f"SKIP {project['name']}: selected services already running")
            if dry_run:
                continue
            if stopped_services:
                result = run(command, timeout=int(project.get("command_timeout_seconds", 120)))
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
                    tier_errors.append(
                        f"{project['name']}: Compose returned {result.returncode}: "
                        + (detail[0] if detail else "no detail")
                    )
                    continue
                tier_errors.extend(wait_for_started_project(project, stopped_services))
            else:
                tier_errors.extend(project_state_errors(project, index))
            for check in project.get("healthchecks", []):
                ok, detail = probe(check)
                log(("PASS " if ok else "FAIL ") + detail)
                if not ok:
                    tier_errors.append(detail)
        if tier_errors:
            errors.extend(tier_errors)
            log(f"tier {tier_name} failed with {len(tier_errors)} error(s)")
            if tier.get("blocking", True):
                break
        else:
            log(f"tier {tier_name} complete")
    return not errors, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "recover"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true", help="print and preflight without mutation")
    parser.add_argument("--force", action="store_true", help="override maintenance flag and cooldown")
    args = parser.parse_args()

    lock_handle = acquire_lock(60 if args.mode == "recover" else 0)
    try:
        manifest = load_manifest(args.manifest)
        if args.mode == "audit":
            ok, errors = audit(manifest)
        else:
            ok, errors = recover(manifest, dry_run=args.dry_run, force=args.force)
    except Exception as exc:
        ok = False
        errors = [f"{type(exc).__name__}: {exc}"]

    payload = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": args.mode,
        "dry_run": args.dry_run,
        "ok": ok,
        "errors": errors,
    }
    write_status(payload)
    if errors:
        for error in errors:
            log("ERROR " + error)
    event = "success" if ok else "failure"
    notify(manifest if "manifest" in locals() else {}, event, "; ".join(errors) or "all tiers healthy")
    lock_handle.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
