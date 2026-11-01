#!/usr/bin/env python3
"""Create one native dashboard from a private, previously reviewed observation.

No actuator calls, integration changes, authentication changes or restarts.
After any uncertain write, inspect the native state; never rerun blindly.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "home-assistant-voice"))
from configure import client, ws
from build import URL, build, validate


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    if path.exists():
        raise RuntimeError("Private evidence already exists; inspect before continuing: " + path.name)
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)


def observation(state):
    dashboards = {p.stem: read(p) for p in (state / "before").glob("*.json")}
    return {"states": read(state / "states.json"), "registry": read(state / "registry.json"),
            "areas": json.loads(read(state / "areas.json")), "services": read(state / "services.json"),
            "pipelines": read(state / "pipelines.json"), "bindings": read(state / "bindings.json"),
            "pi": dashboards["edsys-pi"], "media": dashboards["edsys-media"], "dashboards": dashboards}


def rollback(state, execute):
    api = client()
    created = read(state / "created.json")
    metadata = ws(api, {"type": "lovelace/dashboards/list"})
    selected = [d for d in metadata if d["url_path"] == URL]
    if len(selected) != 1 or selected[0] != created:
        raise RuntimeError("New dashboard metadata changed; review before removal")
    if ws(api, {"type": "lovelace/config", "url_path": URL}) != read(state / "readback.json"):
        raise RuntimeError("Dashboard has later edits; review before removal")
    print(json.dumps({"rollback_preview": True, "remove_only": URL, "actuator_calls": 0}), flush=True)
    if not execute:
        return
    save(state / "rollback-started.json", {"dashboard_id": created["id"]})
    ws(api, {"type": "lovelace/dashboards/delete", "dashboard_id": created["id"]})
    after = ws(api, {"type": "lovelace/dashboards/list"})
    if after != [d for d in metadata if d["url_path"] != URL]:
        raise RuntimeError("Dashboard removal readback differs; inspect without replay")
    save(state / "rollback-verified.json", {"removed_only": URL})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-state", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    state = args.private_state
    if args.rollback:
        rollback(state, args.apply)
        return
    observed = observation(state)
    candidate = build(observed)
    candidate_path = state / "candidate.json"
    if candidate_path.exists():
        if read(candidate_path) != candidate:
            raise RuntimeError("Candidate differs from the reviewed version")
    else:
        save(candidate_path, candidate)
    print(json.dumps({"candidate_ready": True, "views": len(candidate["views"]),
                      "native_cards_only": True,
                      "live_light_switch_coverage": sum(s["entity_id"].startswith(("light.", "switch.")) for s in observed["states"]),
                      "actuator_calls": 0}), flush=True)
    if not args.apply:
        return
    if (state / "created.json").exists() or (state / "apply-started.json").exists():
        raise RuntimeError("An apply attempt is recorded; inspect native state without replay")
    api = client()
    metadata = ws(api, {"type": "lovelace/dashboards/list"})
    if any(row["url_path"] == URL or row["title"] == "Master Bedroom" for row in metadata):
        raise RuntimeError("A matching dashboard already exists")
    if metadata != read(state / "dashboards-before.json"):
        raise RuntimeError("Dashboard metadata changed during preparation")
    for url, config in observed["dashboards"].items():
        if ws(api, {"type": "lovelace/config", "url_path": url}) != config:
            raise RuntimeError("Source dashboard changed: " + url)
    resources = ws(api, {"type": "lovelace/resources"})
    if resources != read(state / "resources-before.json"):
        raise RuntimeError("Frontend resources changed during preparation")
    current_states = api({"operation": "request", "method": "GET", "path": "api/states"})
    expected = {s["entity_id"] for s in observed["states"] if s["entity_id"].startswith(("light.", "switch."))}
    actual = {s["entity_id"] for s in current_states if s["entity_id"].startswith(("light.", "switch."))}
    if expected != actual:
        raise RuntimeError("Live light/switch inventory changed; rebuild from new evidence")
    validate(candidate, {s["entity_id"]: s for s in current_states}, observed["services"], observed["dashboards"])
    save(state / "apply-started.json", {"url_path": URL, "candidate": "candidate.json"})
    created = ws(api, {"type": "lovelace/dashboards/create", "url_path": URL,
                       "title": "Master Bedroom", "icon": "mdi:bed-king-outline",
                       "show_in_sidebar": True, "require_admin": False})
    save(state / "created.json", created)
    ws(api, {"type": "lovelace/config/save", "url_path": URL, "config": candidate})
    after = ws(api, {"type": "lovelace/config", "url_path": URL})
    save(state / "readback.json", after)
    if after != candidate:
        raise RuntimeError("New dashboard readback differs; inspect without replay")
    after_metadata = ws(api, {"type": "lovelace/dashboards/list"})
    if [d for d in after_metadata if d["url_path"] != URL] != metadata:
        raise RuntimeError("Existing dashboard metadata differs")
    for url, config in observed["dashboards"].items():
        if ws(api, {"type": "lovelace/config", "url_path": url}) != config:
            raise RuntimeError("Existing dashboard verification differs: " + url)
    if ws(api, {"type": "lovelace/resources"}) != resources:
        raise RuntimeError("Frontend resource verification differs")
    save(state / "verified.json", {"exact_native_readback": True,
                                   "existing_dashboards_preserved": len(metadata),
                                   "resources_preserved": True, "actuator_calls": 0})
    print((state / "verified.json").read_text(), flush=True)


if __name__ == "__main__":
    main()
