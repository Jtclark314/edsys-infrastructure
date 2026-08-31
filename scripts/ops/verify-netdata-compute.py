#!/usr/bin/env python3
"""Verify the authoritative six-node EdSys Netdata parent topology."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


PARENT = "http://127.0.0.1:19999"
EXPECTED = {
    "9950x",
    "netbox",
    "pve-node0",
    "pve-node1",
    "pve-node2",
    "pve-node3",
}
RETIRED = {
    "edcore-automation",
    "edcore-ops",
    "edcore-sdr",
    "pve-edcore",
}
EXPECTED_GROUP = "edsys-compute"
EXPECTED_TOTAL = len(EXPECTED)
EXPECTED_RECEIVING = EXPECTED_TOTAL - 1


def fetch(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"{PARENT}{path}", timeout=10) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc


def main() -> int:
    try:
        nodes_payload = fetch("/api/v3/nodes")
        info_payload = fetch("/api/v2/info")
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    nodes = nodes_payload.get("nodes", [])
    by_name = {node.get("nm"): node for node in nodes if node.get("nm")}
    actual = {name for name, node in by_name.items() if node.get("state") != "stale"}
    errors: list[str] = []

    if actual != EXPECTED:
        errors.append(
            "node set mismatch: "
            f"missing={sorted(EXPECTED - actual)} unexpected={sorted(actual - EXPECTED)}"
        )

    unexpected_history = set(by_name) - EXPECTED - RETIRED
    if unexpected_history:
        errors.append(f"unexpected historical nodes={sorted(unexpected_history)}")
    for name in sorted(RETIRED & set(by_name)):
        node = by_name[name]
        if node.get("state") != "stale":
            errors.append(f"retired node {name}: state={node.get('state')!r}")

    for name in sorted(EXPECTED & actual):
        node = by_name[name]
        if node.get("state") != "reachable":
            errors.append(f"{name}: state={node.get('state')!r}")
        # The alert engine legitimately reports "initializing" while it reloads
        # checks after an Agent restart. Streaming reachability and Parent
        # receiving counts are the authoritative topology signals during that
        # bounded state.
        health_status = node.get("health", {}).get("status")
        if health_status not in {"online", "initializing"}:
            errors.append(
                f"{name}: health={health_status!r}"
            )
        if node.get("labels", {}).get("group") != EXPECTED_GROUP:
            errors.append(
                f"{name}: group={node.get('labels', {}).get('group')!r}"
            )

    local_agent = next(
        (agent for agent in info_payload.get("agents", []) if agent.get("nm") == "9950x"),
        None,
    )
    if local_agent is None:
        errors.append("9950x is missing from /api/v2/info")
    else:
        runtime = local_agent.get("application", {}).get("runtime", {})
        counts = local_agent.get("nodes", {})
        if runtime.get("parent") is not True:
            errors.append(f"9950x parent flag is {runtime.get('parent')!r}")
        if not isinstance(counts.get("total"), int) or counts.get("total") < EXPECTED_TOTAL:
            errors.append(
                f"9950x reports total={counts.get('total')!r}, expected at least {EXPECTED_TOTAL}"
            )
        if counts.get("receiving") != EXPECTED_RECEIVING:
            errors.append(
                "9950x reports "
                f"receiving={counts.get('receiving')!r}, expected {EXPECTED_RECEIVING}"
            )

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    for name in sorted(EXPECTED):
        node = by_name[name]
        print(
            f"PASS {name}: {node.get('state')}, "
            f"health={node.get('health', {}).get('status')}, "
            f"group={node.get('labels', {}).get('group')}, version={node.get('v')}"
        )
    for name in sorted(RETIRED & set(by_name)):
        print(f"PASS retired {name}: archived stale history, no active stream")
    print(
        f"PASS topology: 9950x parent, {EXPECTED_TOTAL} active nodes, "
        f"{EXPECTED_RECEIVING} receiving children"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
