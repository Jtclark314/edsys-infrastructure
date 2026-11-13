#!/usr/bin/env python3
"""Copy a non-versioned private S3 store with content verification and safe resume.

Credentials, object names, bucket configuration and migration state stay in the
operator-selected private evidence directory. Standard output is aggregate only.
No source mutation is implemented. Final mode requires explicitly quiesced writers.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import urlencode

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
    connect_timeout=10,
    read_timeout=120,
    retries={"mode": "standard", "max_attempts": 4},
    max_pool_connections=8,
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)
HEADERS = (
    "ContentType",
    "CacheControl",
    "ContentDisposition",
    "ContentEncoding",
    "ContentLanguage",
    "Expires",
    "WebsiteRedirectLocation",
)
NO_CONFIG = {
    "NoSuchBucketPolicy",
    "NoSuchCORSConfiguration",
    "NoSuchLifecycleConfiguration",
    "ServerSideEncryptionConfigurationNotFoundError",
    "ObjectLockConfigurationNotFoundError",
    "NoSuchTagSet",
}
CONFIGURATIONS = (
    ("policy", "get_bucket_policy", "put_bucket_policy", "Policy"),
    ("cors", "get_bucket_cors", "put_bucket_cors", "CORSRules"),
    (
        "lifecycle",
        "get_bucket_lifecycle_configuration",
        "put_bucket_lifecycle_configuration",
        "Rules",
    ),
    (
        "encryption",
        "get_bucket_encryption",
        "put_bucket_encryption",
        "ServerSideEncryptionConfiguration",
    ),
    ("tags", "get_bucket_tagging", "put_bucket_tagging", "TagSet"),
)


def client(endpoint, access, secret):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=CONFIG,
    )


def serializable(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(type(value).__name__)


def private_json(path, data):
    path.write_text(
        json.dumps(data, default=serializable, sort_keys=True, indent=2) + "\n"
    )
    path.chmod(0o600)


def normalized(head):
    headers = {key: head[key] for key in HEADERS if key in head}
    return json.dumps(
        {"headers": headers, "metadata": head.get("Metadata", {})},
        default=serializable,
        sort_keys=True,
    )


def hash_stream(body, target=None):
    digest = hashlib.sha256()
    size = 0
    try:
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            if target is not None:
                target.write(chunk)
    finally:
        body.close()
    return digest.hexdigest(), size


def source_configuration(source, bucket):
    versioning = source.get_bucket_versioning(Bucket=bucket)
    if versioning.get("Status"):
        raise RuntimeError(
            "Versioned or suspended source bucket needs a separate history-preserving migration"
        )
    configuration = {"versioning": {}, "configs": {}}
    acl = source.get_bucket_acl(Bucket=bucket)
    grants = acl.get("Grants", [])
    if (
        len(grants) != 1
        or grants[0].get("Permission") != "FULL_CONTROL"
        or grants[0].get("Grantee", {}).get("Type") != "CanonicalUser"
    ):
        raise RuntimeError(
            "Non-default bucket ACL requires explicit compatibility review"
        )
    configuration["acl_private_owner_only"] = True
    for method in (
        "get_bucket_notification_configuration",
        "get_bucket_logging",
        "get_bucket_website",
    ):
        try:
            extra = getattr(source, method)(Bucket=bucket)
            extra.pop("ResponseMetadata", None)
            if extra:
                raise RuntimeError(
                    "Non-default bucket notifications/logging/website need separate compatibility review"
                )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {
                "NoSuchWebsiteConfiguration",
                "NoSuchBucketNotificationConfiguration",
                "NotImplemented",
            }:
                raise
    for label, getter, _setter, field in CONFIGURATIONS:
        try:
            result = getattr(source, getter)(Bucket=bucket)
            configuration["configs"][label] = result[field]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in NO_CONFIG:
                raise
    try:
        result = source.get_object_lock_configuration(Bucket=bucket)
        if result.get("ObjectLockConfiguration"):
            raise RuntimeError(
                "Object-lock configuration requires a separately reviewed migration"
            )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in NO_CONFIG:
            raise
    return configuration


def destination_configuration(destination, bucket, configuration):
    try:
        destination.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            raise
        destination.create_bucket(Bucket=bucket)
    if destination.get_bucket_versioning(Bucket=bucket).get("Status"):
        raise RuntimeError("Destination must also be non-versioned")
    for label, _getter, setter, field in CONFIGURATIONS:
        if label in configuration["configs"]:
            value = configuration["configs"][label]
            if label == "cors":
                params = {"CORSConfiguration": {"CORSRules": value}}
            elif label == "lifecycle":
                params = {"LifecycleConfiguration": {"Rules": value}}
            elif label == "tags":
                params = {"Tagging": {"TagSet": value}}
            else:
                params = {field: value}
            getattr(destination, setter)(Bucket=bucket, **params)


def migrate(args):
    os.umask(0o077)
    args.evidence.mkdir(parents=True, mode=0o700, exist_ok=True)
    if args.evidence.is_symlink() or args.evidence.stat().st_mode & 0o077:
        raise RuntimeError("Evidence directory must be private (0700)")
    phase_lock = (args.evidence / "phase.lock").open("a")
    fcntl.flock(phase_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    identities = json.loads(args.credentials.read_text())["identities"]
    credentials = identities[0]["credentials"][0]
    source = client(args.source, credentials["accessKey"], credentials["secretKey"])
    destination = client(
        args.destination, credentials["accessKey"], credentials["secretKey"]
    )
    if args.source == args.destination:
        raise RuntimeError("Source and destination must differ")
    if args.phase == "final" and not args.writers_quiesced:
        raise RuntimeError(
            "Final reconciliation requires explicitly quiesced application writers"
        )
    buckets = [b["Name"] for b in source.list_buckets()["Buckets"]]
    configurations = {
        bucket: source_configuration(source, bucket) for bucket in buckets
    }
    private_json(args.evidence / "bucket-configurations.private.json", configurations)
    for bucket, configuration in configurations.items():
        destination_configuration(destination, bucket, configuration)
    ledger = sqlite3.connect(args.evidence / "objects.private.sqlite")
    ledger.execute(
        "CREATE TABLE IF NOT EXISTS objects (bucket TEXT, object_key TEXT, fingerprint TEXT, sha256 TEXT, size INTEGER, PRIMARY KEY(bucket,object_key))"
    )
    columns = {row[1] for row in ledger.execute("PRAGMA table_info(objects)")}
    for name in ("headers_metadata", "tags"):
        if name not in columns:
            ledger.execute(f"ALTER TABLE objects ADD COLUMN {name} TEXT")
    ledger.commit()
    summary = {
        "phase": args.phase,
        "bucket_count": len(buckets),
        "objects_seen": 0,
        "objects_copied": 0,
        "objects_unchanged": 0,
        "bytes_verified": 0,
        "source_mutated": False,
    }
    started = time.monotonic()
    seen = set()

    def copy_one(item):
        bucket, metadata, prior = item
        key = metadata["Key"]
        fingerprint = json.dumps(
            {k: metadata.get(k) for k in ("Size", "ETag", "LastModified")},
            default=serializable,
            sort_keys=True,
        )
        if args.phase == "initial" and prior is not None and prior[0] == fingerprint:
            return (
                bucket,
                key,
                fingerprint,
                prior[1],
                prior[2],
                False,
                False,
                prior[3],
                prior[4],
            )
        with tempfile.SpooledTemporaryFile(
            max_size=8 * 1024 * 1024, dir=args.evidence
        ) as temporary:
            original = source.get_object(Bucket=bucket, Key=key)
            if any(
                field in original
                for field in (
                    "ServerSideEncryption",
                    "SSEKMSKeyId",
                    "SSECustomerAlgorithm",
                    "ObjectLockMode",
                    "ObjectLockLegalHoldStatus",
                )
            ):
                original["Body"].close()
                raise RuntimeError(
                    "Encrypted or locked object requires separate preservation review"
                )
            digest, size = hash_stream(original["Body"], temporary)
            if size != original["ContentLength"]:
                raise RuntimeError("Source stream size mismatch")
            tags = sorted(
                source.get_object_tagging(Bucket=bucket, Key=key).get("TagSet", []),
                key=lambda t: t["Key"],
            )
            copied = True
            try:
                existing = destination.get_object(Bucket=bucket, Key=key)
                target_digest, target_size = hash_stream(existing["Body"])
                target_tags = sorted(
                    destination.get_object_tagging(Bucket=bucket, Key=key).get(
                        "TagSet", []
                    ),
                    key=lambda t: t["Key"],
                )
                copied = (
                    digest != target_digest
                    or size != target_size
                    or normalized(original) != normalized(existing)
                    or tags != target_tags
                )
            except ClientError as exc:
                if (
                    exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                    != 404
                ):
                    raise
            if copied:
                temporary.seek(0)
                options = {key: original[key] for key in HEADERS if key in original}
                options["Metadata"] = original.get("Metadata", {})
                options["Tagging"] = urlencode({t["Key"]: t["Value"] for t in tags})
                # One streamed upload, bounded memory. Langfuse objects are below S3's 5-GiB PutObject limit.
                if size > 5 * 1024**3:
                    raise RuntimeError("Object needs reviewed multipart handling")
                destination.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=temporary,
                    ContentLength=size,
                    **options,
                )
                observed = destination.get_object(Bucket=bucket, Key=key)
                observed_digest, observed_size = hash_stream(observed["Body"])
                observed_tags = sorted(
                    destination.get_object_tagging(Bucket=bucket, Key=key).get(
                        "TagSet", []
                    ),
                    key=lambda t: t["Key"],
                )
                if (
                    observed_digest != digest
                    or observed_size != size
                    or normalized(original) != normalized(observed)
                    or tags != observed_tags
                ):
                    raise RuntimeError(
                        "Destination content, metadata or tag verification mismatch"
                    )
            return (
                bucket,
                key,
                fingerprint,
                digest,
                size,
                copied,
                True,
                normalized(original),
                json.dumps(tags, sort_keys=True),
            )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for bucket in buckets:
            for page in source.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, PaginationConfig={"PageSize": 500}
            ):
                items = []
                for metadata in page.get("Contents", []):
                    prior = ledger.execute(
                        "SELECT fingerprint,sha256,size,headers_metadata,tags FROM objects WHERE bucket=? AND object_key=?",
                        (bucket, metadata["Key"]),
                    ).fetchone()
                    items.append((bucket, metadata, prior))
                    seen.add((bucket, metadata["Key"]))
                for (
                    bucket_name,
                    key,
                    fingerprint,
                    digest,
                    size,
                    copied,
                    verified,
                    headers_metadata,
                    tags,
                ) in pool.map(copy_one, items):
                    ledger.execute(
                        "INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)",
                        (
                            bucket_name,
                            key,
                            fingerprint,
                            digest,
                            size,
                            headers_metadata,
                            tags,
                        ),
                    )
                    summary["objects_seen"] += 1
                    summary["objects_copied"] += int(copied)
                    summary["objects_unchanged"] += int(not copied)
                    summary["bytes_verified"] += size if verified else 0
                ledger.commit()
                print(
                    json.dumps(
                        {
                            **summary,
                            "elapsed_seconds": round(time.monotonic() - started, 2),
                        }
                    ),
                    flush=True,
                )
    if args.phase == "final":
        extra = []
        for bucket in buckets:
            for page in destination.get_paginator("list_objects_v2").paginate(
                Bucket=bucket
            ):
                for item in page.get("Contents", []):
                    if (bucket, item["Key"]) not in seen:
                        extra.append({"bucket": bucket, "key": item["Key"]})
        private_json(args.evidence / "destination-extras.private.json", extra)
        for item in extra:
            prior = ledger.execute(
                "SELECT sha256,size FROM objects WHERE bucket=? AND object_key=?",
                (item["bucket"], item["key"]),
            ).fetchone()
            if prior is None:
                raise RuntimeError(
                    "Destination has an unowned extra object; explicit review required"
                )
            response = destination.get_object(Bucket=item["bucket"], Key=item["key"])
            digest, size = hash_stream(response["Body"])
            if (digest, size) != prior:
                raise RuntimeError(
                    "An extra destination object changed since migration; explicit review required"
                )
            destination.delete_object(Bucket=item["bucket"], Key=item["key"])
            ledger.execute(
                "DELETE FROM objects WHERE bucket=? AND object_key=?",
                (item["bucket"], item["key"]),
            )
        ledger.commit()
        destination_buckets = {b["Name"] for b in destination.list_buckets()["Buckets"]}
        permitted_extras = set(args.allow_extra_bucket)
        if destination_buckets - permitted_extras != set(buckets):
            raise RuntimeError(
                "Destination has unexpected buckets; explicit review required"
            )
        for bucket in buckets:
            target_configuration = source_configuration(destination, bucket)
            if target_configuration != configurations[bucket]:
                raise RuntimeError(
                    "Destination bucket configuration differs from source"
                )
        summary["obsolete_migrated_objects_removed"] = len(extra)
        summary["owned_separate_buckets"] = len(destination_buckets & permitted_extras)
        verification_digest = hashlib.sha256()
        for record in ledger.execute(
            "SELECT bucket,object_key,sha256,size FROM objects ORDER BY bucket,object_key"
        ):
            if (record[0], record[1]) in seen:
                verification_digest.update(
                    json.dumps(record, ensure_ascii=True).encode()
                )
                verification_digest.update(b"\n")
        summary["verified_manifest_sha256"] = verification_digest.hexdigest()
        summary["complete_match"] = True
    summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
    summary["ledger_total_objects"], summary["ledger_total_bytes"] = ledger.execute(
        "SELECT COUNT(*),COALESCE(SUM(size),0) FROM objects"
    ).fetchone()
    private_json(args.evidence / (args.phase + "-summary.json"), summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--phase", choices=("initial", "final"), required=True)
    parser.add_argument("--writers-quiesced", action="store_true")
    parser.add_argument(
        "--allow-extra-bucket",
        action="append",
        default=[],
        help="Explicitly owned separate destination bucket; never included in source equality",
    )
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    try:
        migrate(parser.parse_args())
    except ClientError as error:
        # Never print an object key, request payload, credential or unredacted response.
        print(
            json.dumps(
                {
                    "error": "s3_operation_failed",
                    "code": error.response.get("Error", {}).get("Code", "unknown"),
                }
            ),
            flush=True,
        )
        raise SystemExit(1)
    except Exception as error:
        # HTTP failures may contain continuation URLs and private object names.
        print(
            json.dumps({"error": "migration_failed", "type": type(error).__name__}),
            flush=True,
        )
        raise SystemExit(1)
