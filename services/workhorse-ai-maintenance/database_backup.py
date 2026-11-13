#!/usr/bin/env python3
"""Stage private, native database backups before the Langfuse S3 object export."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import uuid

OWNER = "edsys-workhorse-ai-databases-v1"


def inspect(name: str) -> dict:
    return json.loads(subprocess.check_output(["docker", "inspect", name], timeout=30))[
        0
    ]


def environment(container: dict) -> dict:
    return dict(
        value.split("=", 1) for value in container["Config"]["Env"] if "=" in value
    )


def command(args: list[str], output=None) -> bytes:
    result = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=output or subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
    )
    if result.returncode:
        raise RuntimeError("database command failed; no generation was published")
    return result.stdout if output is None else b""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def owned(path: Path) -> None:
    if path.is_symlink() or not path.is_dir() or (path / ".owner").read_text() != OWNER:
        raise RuntimeError("refusing an unowned staging generation")


def publish(incoming: Path, root: Path) -> None:
    owned(incoming)
    if incoming.parent != root:
        raise RuntimeError("incoming generation is outside the staging root")
    current, previous = root / "current", root / "previous"
    for path in [current, previous]:
        if path.exists() or path.is_symlink():
            owned(path)
    retired = root / (".retired-" + uuid.uuid4().hex)
    if previous.exists():
        previous.rename(retired)
    if current.exists():
        current.rename(previous)
    incoming.rename(current)
    if retired.exists():
        owned(retired)
        shutil.rmtree(retired)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage-root",
        type=Path,
        default=Path("/srv/edsys-backup/staging/workhorse-ai-databases"),
    )
    parser.add_argument(
        "--clickhouse-backup-root",
        type=Path,
        default=Path("/mnt/ai-store/langfuse/clickhouse-backups"),
    )
    parser.add_argument("--clickhouse", default="workhorse-langfuse-clickhouse")
    parser.add_argument("--langfuse-postgres", default="workhorse-langfuse-postgres")
    parser.add_argument("--litellm-postgres", default="workhorse-litellm-db")
    args = parser.parse_args()
    os.umask(0o077)
    root = args.stage_root
    if root.is_symlink():
        raise RuntimeError("staging root must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".stage.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        incoming = root / (".incoming-" + uuid.uuid4().hex)
        incoming.mkdir(mode=0o700)
        (incoming / ".owner").write_text(OWNER)
        started = datetime.now(timezone.utc).isoformat()
        versions = {}
        for label, name in [
            ("langfuse", args.langfuse_postgres),
            ("litellm", args.litellm_postgres),
        ]:
            role = environment(inspect(name))["POSTGRES_USER"]
            versions[label] = (
                command(
                    [
                        "docker",
                        "exec",
                        name,
                        "psql",
                        "-w",
                        "-U",
                        role,
                        "-d",
                        "postgres",
                        "-At",
                        "-c",
                        "SHOW server_version",
                    ]
                )
                .decode()
                .strip()
            )
            with (incoming / (label + "-postgres.sql")).open("wb") as stream:
                command(
                    ["docker", "exec", name, "pg_dumpall", "-w", "-U", role], stream
                )
        container = inspect(args.clickhouse)
        env = environment(container)
        ip = next(
            net["IPAddress"]
            for net in container["NetworkSettings"]["Networks"].values()
            if net["IPAddress"]
        )
        auth = base64.b64encode(
            (env["CLICKHOUSE_USER"] + ":" + env["CLICKHOUSE_PASSWORD"]).encode()
        ).decode()

        def query(sql: str) -> bytes:
            request = urllib.request.Request(
                "http://" + ip + ":8123/",
                data=sql.encode(),
                headers={"Authorization": "Basic " + auth},
            )
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.read()

        versions["clickhouse"] = query("SELECT version()").decode().strip()
        filename = "edsys-" + uuid.uuid4().hex + ".tar"
        result = query(
            "BACKUP DATABASE default TO Disk('edsys_backups', '" + filename + "')"
        )
        if b"BACKUP_CREATED" not in result:
            raise RuntimeError("native ClickHouse backup did not complete")
        source = args.clickhouse_backup_root / filename
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("native backup is not a regular file")
        target = incoming / "langfuse-clickhouse.tar"
        shutil.copyfile(source, target)
        if digest(source) != digest(target):
            raise RuntimeError("native backup copy checksum mismatch")
        source.unlink()
        files = {
            p.name: {"bytes": p.stat().st_size, "sha256": digest(p)}
            for p in incoming.iterdir()
            if p.is_file() and p.name != ".owner"
        }
        manifest = {
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "versions": versions,
            "files": files,
            "consistency": "Each PostgreSQL dump and the native ClickHouse database backup is consistent independently; this is not an atomic cross-service snapshot. Run the append-only S3 export afterward.",
        }
        (incoming / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        publish(incoming, root)
        print(
            json.dumps(
                {
                    "ok": True,
                    "stage": str(root / "current"),
                    "databases": 3,
                    "bytes": sum(item["bytes"] for item in files.values()),
                }
            )
        )


if __name__ == "__main__":
    main()
