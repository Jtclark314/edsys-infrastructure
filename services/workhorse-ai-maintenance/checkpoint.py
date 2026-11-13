#!/usr/bin/env python3
"""Create a quiesced, private Langfuse application recovery checkpoint."""

from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request


def inspect(name: str) -> dict:
    return json.loads(subprocess.check_output(["docker", "inspect", name], timeout=30))[
        0
    ]


def connection() -> tuple[str, str]:
    container = inspect("workhorse-langfuse-clickhouse")
    env = dict(
        value.split("=", 1) for value in container["Config"]["Env"] if "=" in value
    )
    ip = next(
        net["IPAddress"]
        for net in container["NetworkSettings"]["Networks"].values()
        if net["IPAddress"]
    )
    auth = base64.b64encode(
        (env["CLICKHOUSE_USER"] + ":" + env["CLICKHOUSE_PASSWORD"]).encode()
    ).decode()
    return "http://" + ip + ":8123/", auth


def query(url: str, auth: str, sql: str):
    request = urllib.request.Request(
        url, data=sql.encode(), headers={"Authorization": "Basic " + auth}
    )
    return urllib.request.urlopen(request, timeout=180)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("checkpoint destination already exists")
    for name in ["workhorse-langfuse", "workhorse-langfuse-worker"]:
        if inspect(name)["State"]["Running"]:
            raise SystemExit(
                "stop both Langfuse application writers before checkpointing"
            )
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700)
    with (output / "postgres.sql").open("wb") as stream:
        subprocess.run(
            [
                "docker",
                "exec",
                "workhorse-langfuse-postgres",
                "pg_dumpall",
                "-w",
                "-U",
                "postgres",
            ],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.PIPE,
            timeout=180,
            check=True,
        )
    url, auth = connection()
    with query(url, auth, "SYSTEM FLUSH ASYNC INSERT QUEUE") as response:
        response.read()
    with query(
        url,
        auth,
        "SELECT name, engine, create_table_query FROM system.tables WHERE database='default' ORDER BY name FORMAT JSON",
    ) as response:
        tables = json.load(response)["data"]
    receipts = []
    for table in tables:
        name = table["name"]
        if "/" in name or name in [".", ".."]:
            raise RuntimeError("unsafe table filename")
        quoted = "`" + name.replace("`", "\\`") + "`"
        ddl = output / (name + ".sql")
        ddl.write_text(table["create_table_query"] + ";\n")
        item = {"table": name, "engine": table["engine"], "ddl": ddl.name}
        if table["engine"] not in ["View", "MaterializedView", "Dictionary"]:
            filename = output / (name + ".native")
            with (
                query(
                    url, auth, "SELECT * FROM default." + quoted + " FORMAT Native"
                ) as response,
                filename.open("wb") as stream,
            ):
                shutil.copyfileobj(response, stream)
            suffix = (
                " FINAL"
                if any(term in table["engine"] for term in ["Replacing", "Collapsing"])
                else ""
            )
            with query(
                url,
                auth,
                "SELECT count() FROM default."
                + quoted
                + suffix
                + " FORMAT TabSeparated",
            ) as response:
                count = int(response.read().strip())
            item.update(
                file=filename.name,
                rows=count,
                bytes=filename.stat().st_size,
                sha256=hashlib.file_digest(filename.open("rb"), "sha256").hexdigest(),
            )
        receipts.append(item)
    files = {}
    for path in output.iterdir():
        if path.is_file():
            with path.open("rb") as stream:
                files[path.name] = {
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                }
    manifest = {
        "writers_stopped": True,
        "tables": receipts,
        "files": files,
        "scope": "Langfuse PostgreSQL roles/database and all default ClickHouse application tables; diagnostic system logs stay in the retained original volume",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "tables": len(receipts),
                "files": len(files),
                "bytes": sum(info["bytes"] for info in files.values()),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
