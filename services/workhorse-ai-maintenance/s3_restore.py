#!/usr/bin/env python3
"""Restore a verified private S3 export into new buckets, with read-back checks."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import tarfile
import tempfile
from urllib.parse import urlencode, urlsplit

from s3_backup import archive_verify
from s3_migrate import (
    client,
    destination_configuration,
    hash_stream,
    normalized,
    private_json,
    source_configuration,
)


def private_destination(value):
    endpoint = urlsplit(value)
    address = ipaddress.ip_address(endpoint.hostname or "")
    allowed = address.is_loopback or any(
        address in ipaddress.ip_network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )
    if (
        endpoint.scheme not in ("http", "https")
        or endpoint.username
        or endpoint.password
        or endpoint.path not in ("", "/")
        or endpoint.query
        or endpoint.fragment
        or not allowed
    ):
        raise ValueError("Destination must be an explicit private or loopback IP")
    return value.rstrip("/")


def restore(archive_path, endpoint, report_path):
    os.umask(0o077)
    endpoint = private_destination(endpoint)
    if report_path.exists():
        raise RuntimeError("Restore report must be a new private path")
    report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    verified = archive_verify(archive_path)
    with tarfile.open(archive_path, "r:") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        configuration = json.load(
            archive.extractfile("bucket-configurations.private.json")
        )
        identity = json.load(archive.extractfile("identity.private.json"))
        credentials = identity["identities"][0]["credentials"][0]
        if not credentials["accessKey"] or not credentials["secretKey"]:
            raise RuntimeError("Archive identity has no usable credentials")
        buckets = set(manifest["buckets"])
        expected = {(x["bucket"], x["key"]) for x in manifest["objects"]}
        if (
            set(configuration) != buckets
            or len(expected) != len(manifest["objects"])
            or any(bucket not in buckets for bucket, _key in expected)
        ):
            raise RuntimeError("Archive bucket/object manifest mismatch")
        storage = client(endpoint, credentials["accessKey"], credentials["secretKey"])
        existing = {b["Name"] for b in storage.list_buckets()["Buckets"]}
        if existing & buckets:
            raise RuntimeError("Refusing to overwrite existing archive bucket names")
        for bucket, settings in configuration.items():
            destination_configuration(storage, bucket, settings)
        result = {"objects_verified": 0, "bytes_verified": 0}
        for entry in manifest["objects"]:
            with tempfile.SpooledTemporaryFile(
                max_size=8 * 1024 * 1024, dir=report_path.parent
            ) as body:
                with archive.extractfile(entry["member"]) as source:
                    actual_hash, size = hash_stream(source, body)
                if (actual_hash, size) != (entry["sha256"], entry["bytes"]):
                    raise RuntimeError("Archive payload changed during restore")
                body.seek(0)
                parameters = {
                    "Bucket": entry["bucket"],
                    "Key": entry["key"],
                    "Body": body,
                    "ContentLength": entry["bytes"],
                    "Metadata": entry["metadata"],
                    **entry["headers"],
                }
                if entry["tags"]:
                    parameters["Tagging"] = urlencode(
                        [(tag["Key"], tag["Value"]) for tag in entry["tags"]]
                    )
                storage.put_object(**parameters)
            response = storage.get_object(Bucket=entry["bucket"], Key=entry["key"])
            observed_hash, observed_size = hash_stream(response["Body"])
            tags = storage.get_object_tagging(
                Bucket=entry["bucket"], Key=entry["key"]
            ).get("TagSet", [])
            if (
                (observed_hash, observed_size) != (entry["sha256"], entry["bytes"])
                or normalized(response)
                != normalized({**entry["headers"], "Metadata": entry["metadata"]})
                or sorted(tags, key=lambda t: t["Key"])
                != sorted(entry["tags"], key=lambda t: t["Key"])
            ):
                raise RuntimeError("Restored bytes/headers/metadata/tags mismatch")
            result["objects_verified"] += 1
            result["bytes_verified"] += observed_size
            if result["objects_verified"] % 5000 == 0:
                print(json.dumps(result), flush=True)
        observed = set()
        for bucket in buckets:
            if source_configuration(storage, bucket) != configuration[bucket]:
                raise RuntimeError("Restored bucket configuration mismatch")
            for page in storage.get_paginator("list_objects_v2").paginate(
                Bucket=bucket
            ):
                observed.update(
                    (bucket, item["Key"]) for item in page.get("Contents", [])
                )
        if observed != expected or result["objects_verified"] != verified["objects"]:
            raise RuntimeError("Restored object roster mismatch")
    result.update({"complete_match": True, "bucket_count": len(buckets)})
    private_json(report_path, result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        restore(args.archive, args.destination, args.report)
    except Exception as error:
        print(
            json.dumps({"error": "s3_restore_failed", "type": type(error).__name__}),
            flush=True,
        )
        raise SystemExit(1)
