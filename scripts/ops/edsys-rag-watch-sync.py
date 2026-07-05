#!/usr/bin/env python3
"""Debounced watcher for reviewed EdSys RAG summaries.

The watcher triggers the existing edsys-rag-sync.service. The five-minute timer
remains the fallback source of truth; this only lowers freshness latency.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_VAULT = Path("/mnt/ai-store/rag/docs/EdSysVault")


class DebouncedTrigger:
    def __init__(self, debounce_seconds: float, *, monotonic=time.monotonic, sleeper=time.sleep):
        self.debounce_seconds = debounce_seconds
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.last_trigger = -debounce_seconds

    def run(self, callback) -> bool:
        now = self.monotonic()
        if now - self.last_trigger < self.debounce_seconds:
            return False
        self.sleeper(self.debounce_seconds)
        callback()
        self.last_trigger = self.monotonic()
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch EdSys RAG summary notes and trigger user sync service.")
    parser.add_argument("--vault", default=str(DEFAULT_VAULT))
    parser.add_argument("--pattern", default="*RAG Summary.md")
    parser.add_argument("--service", default="edsys-rag-sync.service")
    parser.add_argument("--debounce-seconds", type=float, default=8.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
    parser.add_argument("--force-poll", action="store_true")
    parser.add_argument("--once", action="store_true", help="Trigger once and exit; useful for validation.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def trigger(service: str, *, dry_run: bool) -> None:
    command = ["systemctl", "--user", "start", service]
    if dry_run:
        print("DRY_RUN", " ".join(command), flush=True)
        return
    subprocess.run(command, check=False)


def matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(os.path.basename(path), pattern)


def snapshot(vault: Path, pattern: str) -> dict[str, tuple[int, int]]:
    state = {}
    if not vault.exists():
        return state
    for path in vault.rglob("*.md"):
        if matches(str(path), pattern):
            stat = path.stat()
            state[str(path)] = (int(stat.st_mtime), int(stat.st_size))
    return state


def watch_with_inotify(args: argparse.Namespace, vault: Path) -> None:
    process = subprocess.Popen(
        [
            "inotifywait",
            "-m",
            "-r",
            "-e",
            "close_write,moved_to,create,delete",
            "--format",
            "%w%f",
            str(vault),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    debouncer = DebouncedTrigger(args.debounce_seconds)
    assert process.stdout is not None
    for line in process.stdout:
        path = line.strip()
        if not matches(path, args.pattern):
            continue
        debouncer.run(lambda: trigger(args.service, dry_run=args.dry_run))


def watch_with_poll(args: argparse.Namespace, vault: Path) -> None:
    previous = snapshot(vault, args.pattern)
    while True:
        time.sleep(args.poll_interval_seconds)
        current = snapshot(vault, args.pattern)
        if current != previous:
            DebouncedTrigger(args.debounce_seconds).run(lambda: trigger(args.service, dry_run=args.dry_run))
            previous = snapshot(vault, args.pattern)


def main() -> int:
    args = parse_args()
    vault = Path(args.vault).expanduser().resolve()
    if args.once:
        trigger(args.service, dry_run=args.dry_run)
        return 0
    if not vault.exists():
        print(f"RAG vault does not exist: {vault}", file=sys.stderr)
        return 2
    if shutil.which("inotifywait") and not args.force_poll:
        watch_with_inotify(args, vault)
    else:
        watch_with_poll(args, vault)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
