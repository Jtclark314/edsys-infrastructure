#!/usr/bin/env python3
"""Render a private, same-image Immich stack from an existing resolved config."""

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


def bind(source: Path, target: str) -> dict:
    return {"type": "bind", "source": str(source), "target": target, "bind": {"create_host_path": False}}


def render(source: dict, state: Path, media: Path, candidate: bool) -> dict:
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
        row["image"] = IMAGES[service]
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
    args = parser.parse_args()
    os.umask(0o077)
    data = render(json.loads(args.source.read_text()), args.state, args.media, args.candidate)
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.output.open("w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    args.output.chmod(0o600)
    print("Private Immich configuration rendered")


if __name__ == "__main__":
    main()
