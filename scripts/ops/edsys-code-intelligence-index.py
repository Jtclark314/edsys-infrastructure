#!/usr/bin/env python3
"""Build a committed-HEAD Zoekt index for explicitly allowlisted repositories."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ZOEK_IMAGE = "edsys/zoekt-minimal:2cb19912-go1.26.5"
DEFAULT_STACK_DIR = Path(
    "/srv/edsys/edsys-infrastructure/docker/edsys-code-intelligence"
)
DEFAULT_CATALOG = DEFAULT_STACK_DIR / "repositories.json"
DEFAULT_STATE_ROOT = Path("/mnt/ai-store/codex-intelligence")
LOCK_PATH = Path("/run/lock/edsys-code-intelligence-index.lock")
SAFE_SOURCE_ROOTS = (
    Path("/home/jeremy/code"),
    Path("/home/jeremy/projects"),
    Path("/srv/edsys"),
)
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
INDEX_ACCOUNT = pwd.getpwnam("jeremy")
INDEX_UID = INDEX_ACCOUNT.pw_uid
INDEX_GID = INDEX_ACCOUNT.pw_gid


class IndexErrorSafe(RuntimeError):
    """Expected operational failure with a payload-safe message."""


@dataclass(frozen=True)
class Repository:
    name: str
    configured_source: Path
    source: Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"{utc_now()} {message}", flush=True)


def run(
    args: list[str],
    *,
    timeout: float,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    completed = subprocess.run(
        args,
        check=False,
        capture_output=capture,
        text=True,
        timeout=timeout,
        env=env,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
        if detail:
            print(detail, file=sys.stderr)
        raise IndexErrorSafe(f"Command failed with exit code {completed.returncode}")
    return completed


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def git_command(source: Path, *arguments: str) -> list[str]:
    """Build a repository-specific Git command without changing global trust."""

    return [
        "git",
        "-c",
        f"safe.directory={source}",
        "-C",
        str(source),
        *arguments,
    ]


def load_catalog(path: Path) -> list[Repository]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IndexErrorSafe("Repository catalog is unavailable or invalid") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise IndexErrorSafe("Repository catalog schema version is invalid")
    raw_repositories = document.get("repositories")
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise IndexErrorSafe("Repository catalog has no repositories")

    repositories: list[Repository] = []
    seen: set[str] = set()
    for raw in raw_repositories:
        if not isinstance(raw, dict):
            raise IndexErrorSafe("Repository catalog contains a malformed entry")
        if raw.get("enabled", True) is not True:
            continue
        name = raw.get("name")
        source_value = raw.get("source")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise IndexErrorSafe("Repository catalog contains an invalid name")
        if name in seen:
            raise IndexErrorSafe("Repository catalog contains duplicate names")
        if raw.get("branch", "HEAD") != "HEAD":
            raise IndexErrorSafe("Only committed HEAD indexing is allowed")
        if not isinstance(source_value, str) or "\x00" in source_value:
            raise IndexErrorSafe(f"Repository {name} has an invalid source path")
        configured = Path(source_value)
        if not configured.is_absolute() or not configured.exists():
            raise IndexErrorSafe(f"Repository {name} source is unavailable")
        source = configured.resolve(strict=True)
        if not any(is_relative_to(source, root) for root in SAFE_SOURCE_ROOTS):
            raise IndexErrorSafe(f"Repository {name} is outside approved source roots")

        top = run(git_command(source, "rev-parse", "--show-toplevel"), timeout=30).stdout.strip()
        if Path(top).resolve(strict=True) != source:
            raise IndexErrorSafe(f"Repository {name} source is not a Git root")
        run(
            git_command(source, "rev-parse", "--verify", "HEAD^{commit}"),
            timeout=30,
        )
        repositories.append(
            Repository(name=name, configured_source=configured, source=source)
        )
        seen.add(name)

    if not repositories:
        raise IndexErrorSafe("Repository catalog has no enabled repositories")
    return repositories


def git_metadata(repository: Repository) -> dict[str, Any]:
    commit = run(git_command(repository.source, "rev-parse", "HEAD"), timeout=30).stdout.strip()
    branch = run(
        git_command(repository.source, "branch", "--show-current"), timeout=30
    ).stdout.strip()
    tracked = run(
        git_command(repository.source, "ls-files", "-z"), timeout=120
    ).stdout.count("\x00")
    return {
        "name": repository.name,
        "commit": commit[:40],
        "branch": branch[:100] or "detached",
        "tracked_files": tracked,
    }


def assert_safe_state_path(path: Path, state_root: Path) -> None:
    resolved_root = state_root.resolve()
    candidate = path.resolve(strict=False)
    if candidate == resolved_root or not is_relative_to(candidate, resolved_root):
        raise IndexErrorSafe("Refusing operation outside the code-intelligence state root")


def remove_tree(path: Path, state_root: Path) -> None:
    assert_safe_state_path(path, state_root)
    if path.exists():
        shutil.rmtree(path)


def build_index(
    repositories: list[Repository],
    target: Path,
    *,
    incremental: bool,
) -> None:
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o755)
    os.chown(target, INDEX_UID, INDEX_GID)
    for existing in target.iterdir():
        if existing.is_file():
            os.chown(existing, INDEX_UID, INDEX_GID)
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--user",
        f"{INDEX_UID}:{INDEX_GID}",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=1g,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "512",
        "--cpus",
        "24",
        "--memory",
        "16g",
        "--label",
        "com.edsys.managed=true",
        "--label",
        "com.edsys.ephemeral=true",
        "--volume",
        f"{target}:/index:rw",
    ]
    container_paths: list[str] = []
    for repository in repositories:
        container_path = f"/repos/{repository.name}"
        command.extend(["--volume", f"{repository.source}:{container_path}:ro"])
        container_paths.append(container_path)
    command.extend(
        [
            "--entrypoint",
            "zoekt-git-index",
            ZOEK_IMAGE,
            "-index",
            "/index",
            "-branches",
            "HEAD",
            "-submodules=false",
            "-require_ctags",
            f"-incremental={'true' if incremental else 'false'}",
            "-parallelism",
            "12",
            "-file_limit",
            "2097152",
            "-shard_limit",
            "104857600",
            *container_paths,
        ]
    )
    log(
        f"index_build_start mode={'incremental' if incremental else 'full'} "
        f"repositories={len(repositories)}"
    )
    completed = run(command, timeout=20 * 60)
    summary_lines = [
        line
        for line in (completed.stderr + "\n" + completed.stdout).splitlines()
        if "finished shard" in line or "attempting to index" in line
    ]
    for line in summary_lines[-100:]:
        print(line, flush=True)


def validate_index(index_dir: Path, repositories: list[Repository]) -> tuple[int, int]:
    shards = sorted(index_dir.glob("*.zoekt"))
    if not shards:
        raise IndexErrorSafe("Candidate index contains no Zoekt shards")
    total_bytes = sum(path.stat().st_size for path in shards)
    run(
        [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--volume",
            f"{index_dir}:/index:ro",
            "--entrypoint",
            "zoekt",
            ZOEK_IMAGE,
            "-index_dir",
            "/index",
            "-jsonl",
            "repo:__edsys_validation_no_match__",
        ],
        timeout=120,
    )
    shard_prefixes = {path.name.split("_v", 1)[0] for path in shards}
    missing = [repo.name for repo in repositories if repo.name not in shard_prefixes]
    if missing:
        raise IndexErrorSafe(f"Candidate index is missing repository shard: {missing[0]}")
    return len(shards), total_bytes


def compose_command(stack_dir: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(stack_dir),
        "--file",
        str(stack_dir / "compose.yaml"),
        *args,
    ]


def wait_for_zoekt(timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        result = run(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                "edsys-code-search",
            ],
            timeout=10,
            check=False,
        )
        last = result.stdout.strip()
        if result.returncode == 0 and last == "healthy":
            return
        time.sleep(2)
    raise IndexErrorSafe(f"Zoekt did not become healthy after activation; state={last or 'missing'}")


def activate_full_index(staging: Path, live: Path, state_root: Path, stack_dir: Path) -> None:
    previous = state_root / ".zoekt-previous"
    failed = state_root / ".zoekt-failed"
    remove_tree(previous, state_root)
    remove_tree(failed, state_root)
    run(
        compose_command(stack_dir, "stop", "--timeout", "30", "zoekt-search"),
        timeout=60,
        check=False,
    )
    had_live = live.exists()
    if had_live:
        live.rename(previous)
    staging.rename(live)
    try:
        run(
            compose_command(stack_dir, "up", "-d", "--no-deps", "zoekt-search"),
            timeout=120,
        )
        wait_for_zoekt()
    except Exception:
        run(
            compose_command(stack_dir, "stop", "--timeout", "15", "zoekt-search"),
            timeout=45,
            check=False,
        )
        if live.exists():
            live.rename(failed)
        if had_live and previous.exists():
            previous.rename(live)
            run(
                compose_command(stack_dir, "up", "-d", "--no-deps", "zoekt-search"),
                timeout=120,
                check=False,
            )
        remove_tree(failed, state_root)
        raise
    remove_tree(previous, state_root)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o755)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o644)
    os.replace(temporary, path)


def record_failure(status_path: Path, *, mode: str, started_at: str, exc: Exception) -> None:
    try:
        existing = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    if not existing.get("success"):
        existing = {
            "schema_version": 1,
            "success": False,
            "generated_at": started_at,
            "repositories": [],
        }
    existing["last_attempt"] = {
        "at": utc_now(),
        "success": False,
        "mode": mode,
        "error_class": type(exc).__name__,
        "message": "Refresh failed; the last successful index was preserved",
    }
    write_json_atomic(status_path, existing)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--stack-dir", type=Path, default=DEFAULT_STACK_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    started_at = utc_now()
    state_root = args.state_root.resolve()
    live = state_root / "zoekt"
    status_path = state_root / "state" / "index-status.json"
    state_root.mkdir(parents=True, exist_ok=True)
    state_root.chmod(0o755)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    with LOCK_PATH.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("index_refresh_skipped reason=already_running")
            return 0

        staging: Path | None = None
        try:
            repositories = load_catalog(args.catalog.resolve(strict=True))
            before = [git_metadata(repository) for repository in repositories]
            if args.mode == "full":
                staging = state_root / (
                    f".zoekt-build-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
                )
                assert_safe_state_path(staging, state_root)
                staging.mkdir(mode=0o755)
                build_index(repositories, staging, incremental=False)
                validate_index(staging, repositories)
                activate_full_index(staging, live, state_root, args.stack_dir)
                staging = None
            else:
                live.mkdir(parents=True, exist_ok=True)
                live.chmod(0o755)
                build_index(repositories, live, incremental=True)

            shard_count, index_bytes = validate_index(live, repositories)
            after = [git_metadata(repository) for repository in repositories]
            before_by_name = {item["name"]: item["commit"] for item in before}
            changed_during_index = [
                item["name"]
                for item in after
                if before_by_name.get(item["name"]) != item["commit"]
            ]
            duration = round(time.monotonic() - started, 3)
            payload = {
                "schema_version": 1,
                "success": True,
                "generated_at": utc_now(),
                "mode": args.mode,
                "duration_seconds": duration,
                "zoekt_image": ZOEK_IMAGE,
                "index_bytes": index_bytes,
                "shard_count": shard_count,
                "repositories": after,
                "changed_during_index": changed_during_index,
                "last_attempt": {
                    "at": utc_now(),
                    "success": True,
                    "mode": args.mode,
                },
            }
            write_json_atomic(status_path, payload)
            log(
                f"index_refresh_complete mode={args.mode} repositories={len(repositories)} "
                f"shards={shard_count} bytes={index_bytes} duration_seconds={duration} "
                f"changed_during_index={len(changed_during_index)}"
            )
            return 0
        except Exception as exc:
            if staging is not None and staging.exists():
                remove_tree(staging, state_root)
            record_failure(
                status_path,
                mode=args.mode,
                started_at=started_at,
                exc=exc,
            )
            log(
                f"index_refresh_failed mode={args.mode} error_class={type(exc).__name__}"
            )
            print(str(exc), file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
