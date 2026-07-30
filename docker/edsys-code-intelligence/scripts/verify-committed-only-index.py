#!/usr/bin/env python3
"""Prove that the pinned Zoekt indexer excludes dirty, untracked, and ignored data."""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import tempfile
import uuid
from pathlib import Path

IMAGE = (
    "ghcr.io/sourcegraph/zoekt@"
    "sha256:0bf4af966897c2fd493e2b0826440e17d5640e8c4d8579c7e5cac28f084da75a"
)
JEREMY = pwd.getpwnam("jeremy")


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        check=False,
        capture_output=capture,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {(result.stderr or result.stdout)[-2000:]}"
        )
    return result


def search(index: Path, value: str) -> list[dict]:
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--volume",
            f"{index}:/index:ro",
            "--entrypoint",
            "zoekt",
            IMAGE,
            "-index_dir",
            "/index",
            "-jsonl",
            value,
        ]
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def main() -> None:
    token = uuid.uuid4().hex
    canaries = {
        "committed": f"EDS_COMMITTED_CANARY_{token}",
        "dirty": f"EDS_DIRTY_CANARY_{token}",
        "untracked": f"EDS_UNTRACKED_CANARY_{token}",
        "ignored": f"EDS_IGNORED_CANARY_{token}",
    }
    base = Path("/home/jeremy/projects")
    with tempfile.TemporaryDirectory(prefix=".edsys-index-proof-", dir=base) as temporary:
        root = Path(temporary)
        repository = root / "synthetic-repo"
        index = root / "index"
        repository.mkdir(mode=0o755)
        index.mkdir(mode=0o755)
        index.chmod(0o755)
        run(["git", "-C", str(repository), "init", "-q"])
        run(["git", "-C", str(repository), "config", "user.name", "EdSys Test"])
        run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "user.email",
                "index-proof@example.invalid",
            ]
        )
        tracked = repository / "tracked.txt"
        tracked.write_text(canaries["committed"] + "\n", encoding="utf-8")
        (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        run(["git", "-C", str(repository), "add", "tracked.txt", ".gitignore"])
        run(["git", "-C", str(repository), "commit", "-qm", "committed index proof"])

        tracked.write_text(
            canaries["committed"] + "\n" + canaries["dirty"] + "\n",
            encoding="utf-8",
        )
        (repository / "untracked.txt").write_text(
            canaries["untracked"] + "\n", encoding="utf-8"
        )
        (repository / "ignored.txt").write_text(
            canaries["ignored"] + "\n", encoding="utf-8"
        )
        for path in sorted(root.rglob("*"), reverse=True):
            os.chown(path, JEREMY.pw_uid, JEREMY.pw_gid)
        os.chown(root, JEREMY.pw_uid, JEREMY.pw_gid)

        run(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--user",
                f"{JEREMY.pw_uid}:{JEREMY.pw_gid}",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--volume",
                f"{repository}:/repo:ro",
                "--volume",
                f"{index}:/index:rw",
                IMAGE,
                "zoekt-git-index",
                "-index",
                "/index",
                "-branches",
                "HEAD",
                "-submodules=false",
                "-incremental=true",
                "/repo",
            ]
        )
        shards = list(index.glob("*.zoekt"))
        assert shards and all(path.stat().st_mode & 0o004 for path in shards), (
            "index shards are not readable by the search service account"
        )
        assert search(index, canaries["committed"]), "committed canary was not indexed"
        assert not search(index, canaries["dirty"]), "dirty working-tree content was indexed"
        assert not search(index, canaries["untracked"]), "untracked content was indexed"
        assert not search(index, canaries["ignored"]), "ignored content was indexed"
        print(
            json.dumps(
                {
                    "committed_present": True,
                    "dirty_absent": True,
                    "untracked_absent": True,
                    "ignored_absent": True,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
