#!/usr/bin/env python3
"""Render guarded private Immich configuration with explicit reviewed upgrade profiles."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path


IMAGES = {
    "immich-server": "sha256:aa163d2e1cc2b16a9515dd1fef901e6f5231befad7024f093d7be1f2da14341a",
    "database": "sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23",
    "redis": "sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf",
    "immich-machine-learning": "sha256:b213fa3c82d27a21a299c46ffbb38a091f18384db1ad67d409a3b34fe0fce556",
}
NAMES = {"immich-server": "server", "database": "postgres", "redis": "redis", "immich-machine-learning": "machine-learning"}
LIMITS = {"immich-server": (4, "4g"), "database": (2, "2g"), "redis": (0.5, "256m"), "immich-machine-learning": (2, "3g")}


# Exact public linux/amd64 manifests resolved read-only from GHCR on 2026-10-24 Eastern.
# Database and Redis keep the existing qualified image IDs.
UPGRADE_IMAGES = {
    "server": "ghcr.io/immich-app/immich-server:v3.3.5@sha256:1ab81816384ce8e782fdbdb2b7703ab68fd12442bc311a53118dc0d74c2ced24",
    "cpu": "ghcr.io/immich-app/immich-machine-learning:v3.3.5@sha256:1270449e47e1fcd703661c4d728b7904d89aacfb715662c26116cc9dd73f266b",
    "cuda": "ghcr.io/immich-app/immich-machine-learning:v3.3.5-cuda@sha256:24ed8ee229b5ed2c9dbfd923a1e17276f97beeefc58992953b2afe2dc860a723",
}


def images_for(profile: dict | None) -> dict:
    images = dict(IMAGES)
    if profile is None:
        return images
    if (set(profile) != {"version", "backend", "model_revision"}
            or profile["version"] != "v3.3.5"
            or profile["backend"] not in ("cpu", "cuda")
            or profile["model_revision"] != "v2"):
        raise ValueError("unapproved_upgrade_profile")
    images["immich-server"] = UPGRADE_IMAGES["server"]
    images["immich-machine-learning"] = UPGRADE_IMAGES[profile["backend"]]
    return images


def bind(source: Path, target: str) -> dict:
    return {"type": "bind", "source": str(source), "target": target, "bind": {"create_host_path": False}}


def render(source: dict, state: Path, media: Path, candidate: bool, profile: dict | None = None) -> dict:
    if profile is None:
        reviewed = {image.rsplit("@", 1)[-1] for image in UPGRADE_IMAGES.values()}
        reviewed_tags = {image.split("@", 1)[0] for image in UPGRADE_IMAGES.values()}
        application = source.get("services", {})
        if any(application.get(role, {}).get("image", "").rsplit("@", 1)[-1] in reviewed
               or application.get(role, {}).get("image", "").split("@", 1)[0] in reviewed_tags
               for role in ("immich-server", "immich-machine-learning")):
            raise ValueError("explicit_upgrade_profile_required")
    images = images_for(profile)
    if set(source.get("services", {})) != set(IMAGES):
        raise ValueError("unexpected_source_services")
    result = copy.deepcopy(source)
    result.pop("name", None)
    result.pop("volumes", None)
    # The host's automatic Docker pools are exhausted. These two small, reviewed
    # subnets avoid existing Docker, LAN and Tailnet routes without global edits.
    subnet = "10.254.228.0/28" if candidate else "10.254.228.16/28"
    result["networks"] = {"default": {"internal": candidate, "ipam": {"config": [{"subnet": subnet}]}}}
    for service, row in result["services"].items():
        row["image"] = images[service]
        row["pull_policy"] = "never"
        prefix = "immich-candidate-" if candidate else "immich-"
        row["container_name"] = prefix + NAMES[service]
        row["restart"] = "no"
        row["networks"] = {"default": {}}
        row["cpus"], row["mem_limit"] = LIMITS[service]
        row["pids_limit"] = 512
        for field in ("deploy", "devices", "device_cgroup_rules", "runtime", "gpus", "ports"):
            row.pop(field, None)
        if service == "database":
            row["volumes"] = [bind(state / "postgres", "/var/lib/postgresql/data")]
            row["shm_size"] = "256m"
        elif service == "redis":
            row["volumes"] = [bind(state / "redis", "/data")]
        elif service == "immich-machine-learning":
            row["volumes"] = [bind(media / "upload", "/usr/src/app/upload"), bind(state / "model-cache", "/cache")]
            if profile is not None:
                env = row.setdefault("environment", {})
                for name in list(env):
                    if name.startswith("MACHINE_LEARNING_PRELOAD__"):
                        del env[name]
                env.update({
                    "MACHINE_LEARNING_MODEL_REVISION": "v2",
                    "MACHINE_LEARNING_WORKERS": "1",
                    "MACHINE_LEARNING_REQUEST_THREADS": "1",
                    "MACHINE_LEARNING_MODEL_INTER_OP_THREADS": "1",
                    "MACHINE_LEARNING_MODEL_INTRA_OP_THREADS": "2",
                    "MACHINE_LEARNING_MODEL_TTL": "60",
                    "MACHINE_LEARNING_MODEL_TTL_POLL_S": "10",
                    "MACHINE_LEARNING_MAX_BATCH_SIZE__FACIAL_RECOGNITION": "1",
                    "MACHINE_LEARNING_MAX_BATCH_SIZE__OCR": "1",
                })
                if profile["backend"] == "cuda":
                    row["deploy"] = {"resources": {"reservations": {"devices": [{
                        "driver": "nvidia", "device_ids": ["0"], "capabilities": ["gpu"],
                    }]}}}
        else:
            # Keep new originals on the same SSD root as migrated originals;
            # /data is an explicit compatibility mount on the internal NVMe.
            row.setdefault("environment", {})["IMMICH_MEDIA_LOCATION"] = "/usr/src/app/upload"
            row["volumes"] = [
                bind(media / "upload", "/usr/src/app/upload"),
                bind(media / "legacy-library", "/usr/src/app/library"),
                bind(state / "server-data", "/data"),
            ]
            if not candidate:
                row["ports"] = [
                    {"target": 2283, "published": "2283", "host_ip": "127.0.0.1", "protocol": "tcp"},
                    {"target": 2283, "published": "2283", "host_ip": "192.168.50.50", "protocol": "tcp"},
                ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--profile", type=Path, help="Explicit reviewed version/backend profile")
    args = parser.parse_args()
    os.umask(0o077)
    profile = json.loads(args.profile.read_text()) if args.profile else None
    data = render(json.loads(args.source.read_text()), args.state, args.media, args.candidate, profile)
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.output.open("w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    args.output.chmod(0o600)
    print("Private Immich configuration rendered")


if __name__ == "__main__":
    main()
