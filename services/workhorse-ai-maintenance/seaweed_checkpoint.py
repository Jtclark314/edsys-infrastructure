#!/usr/bin/env python3
"""Capture stopped Seaweed data and verify an isolated native restore through S3."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid

from s3_migrate import (
    client,
    hash_stream,
    normalized,
    private_json,
    source_configuration,
)

CONTAINER = "workhorse-langfuse-seaweedfs"
DATA = Path("/mnt/ai-store/langfuse/seaweedfs")
IDENTITY = Path("/opt/edsys-workhorse/langfuse/seaweedfs-s3.json")
DOCKER_WRAPPER = None


def docker(*args):
    command = ["docker", *args]
    readonly = args[0] in ("inspect", "logs") or args[:2] in (
        ("network", "ls"),
        ("network", "inspect"),
    )
    if DOCKER_WRAPPER is not None and not readonly:
        command = [sys.executable, str(DOCKER_WRAPPER), *command]
    return subprocess.check_output(command, timeout=1800)


def inspect(name):
    return json.loads(docker("inspect", name))[0]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_network_subnet(value, docker_subnets, host_routes):
    candidate = ipaddress.ip_network(value, strict=True)
    private_ranges = [
        ipaddress.ip_network(x)
        for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ]
    if (
        not isinstance(candidate, ipaddress.IPv4Network)
        or candidate.num_addresses < 4
        or not any(candidate.subnet_of(network) for network in private_ranges)
    ):
        raise ValueError("Restore network requires a usable RFC1918 IPv4 subnet")
    for value in docker_subnets:
        existing = ipaddress.ip_network(value, strict=False)
        if existing.version == 4 and candidate.overlaps(existing):
            raise ValueError("Restore subnet overlaps an existing Docker network")
    for value in host_routes:
        if value == "default":
            continue
        existing = ipaddress.ip_network(value, strict=False)
        if (
            existing.version == 4
            and existing.prefixlen
            and candidate.overlaps(existing)
        ):
            raise ValueError(
                "Restore subnet overlaps an existing non-default host route"
            )
    return str(candidate)


def inspect_restore_network(value):
    identifiers = docker("network", "ls", "--format", "{{.ID}}").decode().split()
    networks = (
        json.loads(docker("network", "inspect", *identifiers)) if identifiers else []
    )
    subnets = [
        item["Subnet"]
        for network in networks
        for item in ((network.get("IPAM") or {}).get("Config") or [])
        if item.get("Subnet")
    ]
    routes = json.loads(
        subprocess.check_output(
            ["ip", "-j", "-4", "route", "show", "table", "all"], timeout=30
        )
    )
    destinations = [route["dst"] for route in routes if route.get("dst")]
    validated = validate_network_subnet(value, subnets, destinations)
    return validated, {
        "subnet": validated,
        "docker_networks_checked": len(networks),
        "docker_subnets_checked": len(subnets),
        "host_route_records_checked": len(destinations),
        "default_routes_ignored": True,
        "overlap": False,
    }


def capture(args):
    if os.geteuid() != 0 or not Path("/mnt/ai-store").is_mount():
        raise RuntimeError("Run cold capture as root with AI Store mounted")
    state = inspect(CONTAINER)
    if state["State"]["Running"]:
        raise RuntimeError(
            "Seaweed must be stopped by the coordinated operator before capture"
        )
    if state["State"].get("OOMKilled") or state["State"].get("ExitCode") not in (
        0,
        143,
    ):
        raise RuntimeError(
            "A cleanly stopped Seaweed process is required before capture"
        )
    for writer in args.writer:
        if inspect(writer)["State"]["Running"]:
            raise RuntimeError("All production and rehearsal writers must be stopped")
    final = json.loads((args.evidence / "final-summary.json").read_text())
    if not final.get("complete_match"):
        raise RuntimeError("A complete frozen S3 reconciliation is required")
    os.umask(0o077)
    args.output.mkdir(parents=True, mode=0o700)
    identity = args.output / "identity.private.json"
    shutil.copyfile(IDENTITY, identity)
    identity.chmod(0o600)
    files = {}
    paths = sorted(DATA.rglob("*"))
    for path in paths:
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RuntimeError("Unexpected special file in stopped native data")
        if path.is_file():
            files[str(path.relative_to(DATA))] = {
                "sha256": digest(path),
                "bytes": path.stat().st_size,
                "mode": path.stat().st_mode & 0o777,
            }
    archive = args.output / "native-data.tar"
    with tarfile.open(archive, "w:", dereference=True) as tar:
        for path in paths:
            tar.add(
                path, arcname="data/" + str(path.relative_to(DATA)), recursive=False
            )
    if inspect(CONTAINER)["State"]["Running"]:
        raise RuntimeError("Production Seaweed restarted during cold capture")
    for relative, expected in files.items():
        path = DATA / relative
        if digest(path) != expected["sha256"]:
            raise RuntimeError("Native data changed during stopped capture")
    with sqlite3.connect(
        (args.evidence / "objects.private.sqlite").as_uri() + "?mode=ro", uri=True
    ) as source:
        with sqlite3.connect(args.output / "objects.private.sqlite") as destination:
            source.backup(destination)
    shutil.copyfile(
        args.evidence / "final-summary.json", args.output / "final-summary.json"
    )
    shutil.copyfile(
        args.evidence / "bucket-configurations.private.json",
        args.output / "bucket-configurations.private.json",
    )
    cleanup = args.evidence / "rehearsal-cleanup.json"
    if cleanup.is_file():
        shutil.copyfile(cleanup, args.output / cleanup.name)
    manifest = {
        "schema": 1,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "source_container": CONTAINER,
        "source_image": state["Config"]["Image"],
        "source_image_id": state["Image"],
        "command": state["Config"]["Cmd"],
        "stopped": True,
        "stopped_exit_code": state["State"]["ExitCode"],
        "stopped_finished_utc": state["State"]["FinishedAt"],
        "writers_checked": len(args.writer),
        "archive_sha256": digest(archive),
        "identity_sha256": digest(identity),
        "ledger_sha256": digest(args.output / "objects.private.sqlite"),
        "final_summary_sha256": digest(args.output / "final-summary.json"),
        "bucket_configurations_sha256": digest(
            args.output / "bucket-configurations.private.json"
        ),
        "files": files,
    }
    if cleanup.is_file():
        manifest["rehearsal_cleanup_sha256"] = digest(args.output / cleanup.name)
    private_json(args.output / "checkpoint.private.json", manifest)
    print(
        json.dumps(
            {
                "checkpoint": str(args.output),
                "native_files": len(files),
                "native_bytes": sum(x["bytes"] for x in files.values()),
                "archive_sha256": manifest["archive_sha256"],
                "stopped_capture": True,
            }
        ),
        flush=True,
    )


def restore(args):
    if os.geteuid() != 0:
        raise RuntimeError("Run isolated native restore as root")
    os.umask(0o077)
    network_subnet, network_preflight = inspect_restore_network(args.network_subnet)
    manifest = json.loads((args.checkpoint / "checkpoint.private.json").read_text())
    archive = args.checkpoint / "native-data.tar"
    identity = args.checkpoint / "identity.private.json"
    if (
        digest(archive) != manifest["archive_sha256"]
        or digest(identity) != manifest["identity_sha256"]
        or digest(args.checkpoint / "objects.private.sqlite")
        != manifest["ledger_sha256"]
        or digest(args.checkpoint / "final-summary.json")
        != manifest["final_summary_sha256"]
        or digest(args.checkpoint / "bucket-configurations.private.json")
        != manifest["bucket_configurations_sha256"]
    ):
        raise RuntimeError("Cold checkpoint integrity mismatch")
    if (
        manifest.get("rehearsal_cleanup_sha256")
        and digest(args.checkpoint / "rehearsal-cleanup.json")
        != manifest["rehearsal_cleanup_sha256"]
    ):
        raise RuntimeError("Cold checkpoint cleanup-receipt integrity mismatch")
    args.work.mkdir(parents=True, mode=0o700)
    private_json(args.work / "network-preflight.json", network_preflight)
    restored = args.work / "data"
    restored.mkdir(mode=0o700)
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            parts = Path(member.name).parts
            if (
                not parts
                or parts[0] != "data"
                or ".." in parts
                or member.issym()
                or member.islnk()
            ):
                raise RuntimeError("Unsafe native archive member")
            target = args.work / member.name
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with (
                    tar.extractfile(member) as source,
                    target.open("xb") as destination,
                ):
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                target.chmod(member.mode & 0o777)
            else:
                raise RuntimeError("Unexpected special native archive member")
            os.chown(target, 1000, 1000)
    os.chown(restored, 1000, 1000)
    for relative, expected in manifest["files"].items():
        path = restored / relative
        if digest(path) != expected["sha256"]:
            raise RuntimeError("Restored native file mismatch")
    restored_identity = args.work / "identity.private.json"
    shutil.copyfile(identity, restored_identity)
    restored_identity.chmod(0o600)
    os.chown(restored_identity, 1000, 1000)
    credentials = json.loads(restored_identity.read_text())["identities"][0][
        "credentials"
    ][0]
    suffix = uuid.uuid4().hex[:10]
    network = "edsys-seaweed-restore-" + suffix
    container = "edsys-seaweed-restore-" + suffix
    created_network = False
    created_container = False
    result = {
        "native_files_verified": len(manifest["files"]),
        "objects_verified": 0,
        "bytes_verified": 0,
        "private_network": True,
        "host_ports": False,
        "network_subnet": network_subnet,
    }
    try:
        docker(
            "network",
            "create",
            "--internal",
            "--subnet",
            network_subnet,
            "--label",
            "edsys.restore-owned=local-ai-20261112",
            network,
        )
        created_network = True
        docker(
            "create",
            "--name",
            container,
            "--network",
            network,
            "--network-alias",
            "langfuse-seaweedfs",
            "--cpus",
            "2",
            "--memory",
            "2g",
            "--label",
            "edsys.restore-owned=local-ai-20261112",
            "--mount",
            f"type=bind,src={restored},dst=/data",
            "--mount",
            f"type=bind,src={restored_identity},dst=/etc/seaweedfs/s3.json,readonly",
            manifest["source_image"],
            *manifest["command"],
        )
        created_container = True
        docker("start", container)
        storage = None
        for attempt in range(60):
            try:
                address = next(
                    iter(inspect(container)["NetworkSettings"]["Networks"].values())
                )["IPAddress"]
                storage = client(
                    f"http://{address}:8333",
                    credentials["accessKey"],
                    credentials["secretKey"],
                )
                storage.list_buckets()
                break
            except Exception:
                if attempt == 59:
                    raise RuntimeError("Restored S3 service did not become ready")
                time.sleep(2)
        expected_configuration = json.loads(
            (args.checkpoint / "bucket-configurations.private.json").read_text()
        )
        owned_extra_buckets = set(args.allow_extra_bucket)
        if {item["Name"] for item in storage.list_buckets()["Buckets"]} != (
            set(expected_configuration) | owned_extra_buckets
        ):
            raise RuntimeError("Restored bucket roster differs from frozen source")
        result["source_bucket_roster_exact"] = not owned_extra_buckets
        result["owned_extra_bucket_count"] = len(owned_extra_buckets)
        for bucket, configuration in expected_configuration.items():
            if source_configuration(storage, bucket) != configuration:
                raise RuntimeError("Restored bucket configuration mismatch")
        result["bucket_configurations_verified"] = len(expected_configuration)
        with sqlite3.connect(
            (args.checkpoint / "objects.private.sqlite").as_uri() + "?mode=ro", uri=True
        ) as ledger:
            rows = ledger.execute(
                "SELECT bucket,object_key,sha256,size,headers_metadata,tags FROM objects"
            ).fetchall()
        expected = {(row[0], row[1]) for row in rows}

        def verify(row):
            (
                bucket,
                key,
                expected_hash,
                expected_size,
                expected_metadata,
                expected_tags,
            ) = row
            response = storage.get_object(Bucket=bucket, Key=key)
            observed_hash, size = hash_stream(response["Body"])
            tags = sorted(
                storage.get_object_tagging(Bucket=bucket, Key=key).get("TagSet", []),
                key=lambda t: t["Key"],
            )
            if (
                (observed_hash, size) != (expected_hash, expected_size)
                or normalized(response) != expected_metadata
                or json.dumps(tags, sort_keys=True) != expected_tags
            ):
                raise RuntimeError(
                    "Restored object bytes/metadata/tags differ from frozen ledger"
                )
            return size

        with ThreadPoolExecutor(max_workers=2) as workers:
            for size in workers.map(verify, rows):
                result["objects_verified"] += 1
                result["bytes_verified"] += size
                if result["objects_verified"] % 5000 == 0:
                    print(json.dumps(result), flush=True)
        observed = set()
        for bucket in {row[0] for row in rows}:
            for page in storage.get_paginator("list_objects_v2").paginate(
                Bucket=bucket
            ):
                observed.update(
                    (bucket, item["Key"]) for item in page.get("Contents", [])
                )
        if observed != expected:
            raise RuntimeError("Restored object roster differs from frozen ledger")
        result["complete_match"] = True
        result["archive_sha256"] = manifest["archive_sha256"]
        private_json(args.work / "restore-summary.json", result)
        print(json.dumps(result), flush=True)
    finally:
        if created_container:
            with (args.work / "container.private.log").open("wb") as log:
                subprocess.run(
                    ["docker", "logs", container], stdout=log, stderr=subprocess.STDOUT
                )
            docker("rm", "-f", container)
        if created_network:
            docker("network", "rm", network)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docker-wrapper",
        type=Path,
        help="Optional shared Docker mutation-lock wrapper",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    cold = commands.add_parser("capture")
    cold.add_argument("--output", type=Path, required=True)
    cold.add_argument("--evidence", type=Path, required=True)
    cold.add_argument("--writer", action="append", required=True)
    isolated = commands.add_parser("restore")
    isolated.add_argument("--checkpoint", type=Path, required=True)
    isolated.add_argument("--work", type=Path, required=True)
    isolated.add_argument(
        "--network-subnet",
        required=True,
        help="Unused RFC1918 IPv4 CIDR, checked against Docker networks and non-default host routes",
    )
    isolated.add_argument(
        "--allow-extra-bucket",
        action="append",
        default=[],
        help="Explicitly owned task-only bucket retained in the proven native checkpoint",
    )
    args = parser.parse_args()
    DOCKER_WRAPPER = args.docker_wrapper
    try:
        capture(args) if args.command == "capture" else restore(args)
    except Exception as error:
        print(
            json.dumps(
                {"error": "native_checkpoint_failed", "type": type(error).__name__}
            ),
            flush=True,
        )
        raise SystemExit(1)
