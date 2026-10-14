#!/usr/bin/env python3
"""Configure an isolated native HA voice candidate through existing hub access.

This does not read/export provider credentials or modify HA storage files.
--apply records an authorized action; it is not an authorization grant.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
CANDIDATE = "EdSys Voice Candidate"
TARGET = "EdSys Living Room Voice"


def client():
    spec = importlib.util.spec_from_file_location(
        "voice_ha_access", REPO / "scripts/ops/edcore-ha-api.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda payload: module.execute("pve-node3", payload)["result"]


def ws(api, command):
    result = api({"operation": "websocket", "command": command, "limit": 50})
    if isinstance(result, dict) and "items" in result:
        if result.get("limited"):
            raise RuntimeError("Read was limited; do not select from a partial inventory")
        return result["items"]
    return result


def request(api, path, data):
    return api({"operation": "request", "method": "POST", "path": path, "data": data})


def submit(api, path, flow, data):
    fields = {item["name"] for item in flow.get("data_schema", [])}
    if unknown := set(data) - fields:
        raise RuntimeError(f"Settings not supported by the native form: {sorted(unknown)}")
    return request(api, path, data)


def exactly_one(items, predicate, label):
    matches = [item for item in items if predicate(item)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {label}; observed {len(matches)}")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--private-state", type=Path, required=True)
    args = parser.parse_args()
    api = client()
    entries = ws(api, {"type": "config_entries/get"})
    entry = exactly_one(entries, lambda e: e["domain"] == "openai_conversation", "OpenAI entry")
    pipelines = ws(api, {"type": "assist_pipeline/pipeline/list"})
    original = exactly_one(pipelines["pipelines"], lambda p: p["name"] == TARGET, "target pipeline")
    subs = ws(api, {"type": "config_entries/subentries/list", "entry_id": entry["entry_id"]})
    profile = json.loads((ROOT / "profile.json").read_text())
    if any(s["title"] == profile["name"] for s in subs):
        raise RuntimeError("Candidate agent already exists; inspect before changing or retrying")
    if any(p["name"] == CANDIDATE for p in pipelines["pipelines"]):
        raise RuntimeError("Candidate pipeline already exists; inspect before changing or retrying")
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "model": profile["chat_model"],
                          "web_search": True, "local_intents_first": True,
                          "llm_device_tools": [], "speech_engines_retained": True}))
        return
    args.private_state.mkdir(mode=0o700, parents=True, exist_ok=True)
    baseline = args.private_state / "pipeline-before.json"
    baseline.write_text(json.dumps(pipelines)); baseline.chmod(0o600)
    base = "api/config/config_entries/subentries/flow"
    flow = request(api, base, {"handler": [entry["entry_id"], "conversation"]})
    flow_id = flow["flow_id"]
    step_path = base + "/" + flow_id
    flow_state = args.private_state / "flow.json"
    flow_state.write_text(json.dumps({"entry_id": entry["entry_id"], "flow_id": flow_id}))
    flow_state.chmod(0o600)
    if flow.get("step_id") != "init":
        raise RuntimeError("Unexpected initial form; inspect before proceeding")
    flow = submit(api, step_path, flow, {"name": profile["name"], "recommended": False,
                                   "prompt": (ROOT / "prompt.jinja").read_text(),
                                   "llm_hass_api": []})
    if flow.get("step_id") != "additional" or flow.get("errors"):
        raise RuntimeError("Initial settings were not accepted")
    flow = submit(api, step_path, flow, {"chat_model": profile["chat_model"],
                                   "max_tokens": profile["max_tokens"],
                                   "temperature": 1.0, "top_p": 1.0,
                                   "store_responses": False})
    if flow.get("step_id") != "model" or flow.get("errors"):
        raise RuntimeError("Model settings were not accepted")
    model_keys = set(profile) - {"name", "chat_model", "max_tokens", "store_responses"}
    flow = submit(api, step_path, flow, {key: profile[key] for key in model_keys})
    if flow.get("type") != "create_entry" or flow.get("errors"):
        raise RuntimeError("Candidate agent was not created")
    # Wait for native entity registration; never repeat the creation request.
    agent = None
    for _ in range(8):
        result = api({"operation": "entities", "domain": "conversation",
                      "filter": profile["name"], "limit": 20})
        matches = [e for e in result["entities"] if e["name"] == profile["name"]]
        if len(matches) == 1:
            agent = matches[0]["entity_id"]
            break
        time.sleep(0.5)
    if agent is None:
        raise RuntimeError("Candidate entity not registered; inspect before retry")
    data = {key: value for key, value in original.items() if key != "id"}
    data.update(name=CANDIDATE, conversation_engine=agent, prefer_local_intents=True)
    created = ws(api, {"type": "assist_pipeline/pipeline/create", **data})
    state = {"entry_id": entry["entry_id"], "agent_entity": agent,
             "candidate_pipeline": created, "target_pipeline_id": original["id"],
             "preferred_pipeline": pipelines["preferred_pipeline"]}
    path = args.private_state / "candidate.json"
    path.write_text(json.dumps(state)); path.chmod(0o600)
    print(json.dumps({"candidate_created": True, "model": profile["chat_model"],
                      "working_pipeline_unchanged": True, "provider_key_exported": False}))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, OSError) as error:
        print(f"Voice configuration stopped: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
