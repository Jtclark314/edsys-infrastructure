#!/usr/bin/env python3
"""Export live S3 objects through its API into a self-contained verified archive.

The export is an object-level recovery set captured over a stated interval, not a
cross-database point-in-time snapshot. A stopped native checkpoint is a separate
commissioning gate. Runtime identities and raw object names remain private.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from s3_migrate import HEADERS, client, private_json, serializable, source_configuration

IDENTITY = Path("/opt/edsys-workhorse/langfuse/seaweedfs-s3.json")
STAGE = Path("/srv/edsys-backup/staging/langfuse-s3")
RECOVERY_MANIFESTS = Path("/opt/edsys-workhorse/langfuse/recovery-manifests")


class HashingReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
        self.count = 0

    def read(self, size=-1):
        blob = self.source.read(size)
        self.digest.update(blob)
        self.count += len(blob)
        return blob


def add_bytes(archive, name, value):
    info = tarfile.TarInfo(name)
    info.mode = 0o600
    info.size = len(value)
    archive.addfile(info, io.BytesIO(value))


def archive_verify(path):
    objects = {}
    manifest = None
    auxiliary = set()
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            if (
                not member.isfile()
                or member.name.startswith("/")
                or ".." in Path(member.name).parts
            ):
                raise RuntimeError("Unexpected archive member")
            stream = archive.extractfile(member)
            if member.name == "manifest.json":
                manifest = json.load(stream)
            elif member.name.startswith("objects/"):
                digest = hashlib.sha256()
                count = 0
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    count += len(chunk)
                if member.name in objects:
                    raise RuntimeError("Duplicate object archive member")
                objects[member.name] = {"sha256": digest.hexdigest(), "bytes": count}
            elif member.name not in (
                "identity.private.json",
                "bucket-configurations.private.json",
                "source.private.json",
            ):
                raise RuntimeError("Unexpected archive member")
            else:
                if member.name in auxiliary:
                    raise RuntimeError("Duplicate recovery metadata member")
                json.load(stream)
                auxiliary.add(member.name)
    if (
        manifest is None
        or auxiliary
        != {
            "identity.private.json",
            "bucket-configurations.private.json",
            "source.private.json",
        }
        or len(objects) != len(manifest["objects"])
        or objects
        != {
            x["member"]: {"sha256": x["sha256"], "bytes": x["bytes"]}
            for x in manifest["objects"]
        }
    ):
        raise RuntimeError("Object export archive integrity mismatch")
    return {
        "objects": len(objects),
        "bytes": sum(x["bytes"] for x in objects.values()),
        "bucket_count": len(manifest["buckets"]),
    }


def export(phase_evidence=None):
    if os.geteuid() != 0 or not Path("/mnt/ai-store").is_mount():
        raise RuntimeError("Run as root with AI Store mounted")
    os.umask(0o077)
    if phase_evidence is None and RECOVERY_MANIFESTS.is_dir():
        phase_evidence = RECOVERY_MANIFESTS
    started = datetime.now(timezone.utc).isoformat()
    credentials = json.loads(IDENTITY.read_text())["identities"][0]["credentials"][0]
    raw = subprocess.check_output(
        ["docker", "inspect", "workhorse-langfuse-seaweedfs"], timeout=30
    )
    container = json.loads(raw)[0]
    if not container["State"]["Running"]:
        raise RuntimeError("SeaweedFS must be running for an API object export")
    address = next(iter(container["NetworkSettings"]["Networks"].values()))["IPAddress"]
    storage = client(
        f"http://{address}:8333", credentials["accessKey"], credentials["secretKey"]
    )
    buckets = [b["Name"] for b in storage.list_buckets()["Buckets"]]
    configuration = {
        bucket: source_configuration(storage, bucket) for bucket in buckets
    }
    STAGE.mkdir(parents=True, mode=0o700, exist_ok=True)
    work_parent = STAGE / "tmp"
    work_parent.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="export-", dir=work_parent))
    destination = directory / "objects.tar"
    records = []
    source = {
        "container": container["Name"],
        "image_id": container["Image"],
        "image_reference": container["Config"]["Image"],
        "started_utc": started,
        "consistency": "Each object is fetched atomically through S3; export spans an interval and is not an application-wide point-in-time checkpoint.",
    }
    with tarfile.open(destination, "w:") as archive:
        for bucket in buckets:
            for page in storage.get_paginator("list_objects_v2").paginate(
                Bucket=bucket
            ):
                for entry in page.get("Contents", []):
                    key = entry["Key"]
                    response = storage.get_object(Bucket=bucket, Key=key)
                    if any(
                        field in response
                        for field in (
                            "ServerSideEncryption",
                            "SSEKMSKeyId",
                            "SSECustomerAlgorithm",
                            "ObjectLockMode",
                            "ObjectLockLegalHoldStatus",
                        )
                    ):
                        response["Body"].close()
                        raise RuntimeError(
                            "Encrypted or locked object needs a separate backup policy"
                        )
                    member = (
                        "objects/"
                        + hashlib.sha256(json.dumps([bucket, key]).encode()).hexdigest()
                    )
                    info = tarfile.TarInfo(member)
                    info.size = response["ContentLength"]
                    info.mode = 0o600
                    reader = HashingReader(response["Body"])
                    try:
                        archive.addfile(info, reader)
                    finally:
                        response["Body"].close()
                    if reader.count != response["ContentLength"]:
                        raise RuntimeError("Export object stream size mismatch")
                    tags = storage.get_object_tagging(Bucket=bucket, Key=key).get(
                        "TagSet", []
                    )
                    records.append(
                        {
                            "bucket": bucket,
                            "key": key,
                            "member": member,
                            "sha256": reader.digest.hexdigest(),
                            "bytes": reader.count,
                            "observed_last_modified": response.get("LastModified"),
                            "observed_etag": response.get("ETag"),
                            "headers": {
                                name: response[name]
                                for name in HEADERS
                                if name in response
                            },
                            "metadata": response.get("Metadata", {}),
                            "tags": tags,
                        }
                    )
        manifest = {
            "schema": 1,
            "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "buckets": buckets,
            "objects": records,
            "source": source,
        }
        add_bytes(
            archive,
            "manifest.json",
            json.dumps(manifest, default=serializable, sort_keys=True).encode(),
        )
        add_bytes(
            archive,
            "bucket-configurations.private.json",
            json.dumps(configuration, default=serializable, sort_keys=True).encode(),
        )
        add_bytes(archive, "identity.private.json", IDENTITY.read_bytes())
        add_bytes(
            archive, "source.private.json", json.dumps(source, sort_keys=True).encode()
        )
    result = archive_verify(destination)
    with destination.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    private_json(
        directory / "receipt.json",
        {
            **result,
            "archive_sha256": digest,
            "archive_bytes": destination.stat().st_size,
            "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    if phase_evidence is not None:
        manifest_dir = directory / "migration-manifests"
        manifest_dir.mkdir(mode=0o700)
        for name in (
            "initial-summary.json",
            "final-summary.json",
            "canary.private.json",
            "source-config.private.json",
            "bucket-configurations.private.json",
            "additional-source-configurations.private.json",
            "additional-source-configurations-summary.json",
            "dual-interface-verification.json",
            "api-restore-canary-summary.json",
            "mime-inference-canary.json",
            "objects.private.sqlite",
            "native-restore-summary.json",
            "post-recreate-verification.json",
            "native-workflow.json",
            "rehearsal-cleanup.json",
            "commissioning-scope.json",
        ):
            source_path = phase_evidence / name
            if source_path.is_file():
                shutil.copyfile(source_path, manifest_dir / name)
    final = STAGE / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    directory.rename(final)
    link = STAGE / (".current-" + uuid.uuid4().hex)
    link.symlink_to(final.name)
    os.replace(link, STAGE / "current")
    completed = sorted(
        [
            p
            for p in STAGE.iterdir()
            if p.is_dir() and not p.is_symlink() and (p / "receipt.json").is_file()
        ],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in completed[2:]:
        archive_verify(old / "objects.tar")
        shutil.rmtree(old)
    print(
        json.dumps({"stage": str(final), **result, "archive_sha256": digest}),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--migration-evidence", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            print(json.dumps(archive_verify(args.verify)))
        else:
            export(args.migration_evidence)
    except Exception as error:
        print(
            json.dumps({"error": "s3_backup_failed", "type": type(error).__name__}),
            flush=True,
        )
        raise SystemExit(1)
