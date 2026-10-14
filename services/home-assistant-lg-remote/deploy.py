#!/usr/bin/env python3
"""Save only the approved remote extension using the existing native HA route."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "home-assistant-voice"))
from configure import client, ws
from build import update_dashboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-state", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.private_state.mkdir(mode=0o700, parents=True, exist_ok=True)
    binding = json.loads(args.binding.read_text())
    api = client()
    registry = ws(api, {"type": "config/entity_registry/get", "entity_id": binding["entity_id"]})
    if registry["platform"] != "webostv" or registry["device_id"] != binding["device_id"] or registry["config_entry_id"] != binding["config_entry_id"] or registry.get("disabled_by"):
        raise RuntimeError("The reviewed TV mapping changed")
    current = ws(api, {"type": "lovelace/config", "url_path": "edsys-media"})
    baseline = args.private_state / "media-before.json"
    if baseline.exists():
        if json.loads(baseline.read_text()) != current:
            raise RuntimeError("Dashboard changed since baseline; inspect before merging")
    else:
        baseline.write_text(json.dumps(current)); baseline.chmod(0o600)
    state = api({"operation": "state", "method": "GET", "path": "api/states/" + binding["entity_id"]})
    candidate = update_dashboard(current, binding["entity_id"], state.get("attributes", {}).get("source_list", []))
    path = args.private_state / "media-candidate.json"
    path.write_text(json.dumps(candidate)); path.chmod(0o600)
    if not args.apply:
        print(json.dumps({"candidate_ready": True, "existing_views_retained": True, "actuator_calls": 0})); return
    if ws(api, {"type": "lovelace/config", "url_path": "edsys-media"}) != current:
        raise RuntimeError("Dashboard changed during preparation")
    ws(api, {"type": "lovelace/config/save", "url_path": "edsys-media", "config": candidate})
    if ws(api, {"type": "lovelace/config", "url_path": "edsys-media"}) != candidate:
        raise RuntimeError("Dashboard readback differs; inspect without replay")
    print(json.dumps({"dashboard_saved_and_verified": True, "actuator_calls": 0, "existing_views_retained": True}))


if __name__ == "__main__": main()
