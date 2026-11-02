#!/usr/bin/env python3
"""Seed the explicitly selected local provider while the app is stopped.

The operator must retain the entire stopped datastore before this one-time
commissioning step. Later starts do not overwrite user preferences.
"""
import argparse
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--datastore", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    runtime = json.loads(subprocess.check_output(["docker", "inspect", args.container]))[0]
    if runtime["State"]["Running"]:
        raise RuntimeError("Stop the exact app container before configuring local AI")
    binds = [m for m in runtime["Mounts"] if m["Destination"] == "/datastore"]
    if len(binds) != 1 or Path(binds[0]["Source"]).resolve() != args.datastore.resolve():
        raise RuntimeError("Selected datastore does not match the stopped app")
    path = args.datastore / "changedetection.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise RuntimeError("A private regular settings file is required")
    data = json.loads(path.read_text())
    app = data["settings"]["application"]
    if app.get("api_access_token_enabled") is not True:
        raise RuntimeError("Native API key protection must remain enabled")
    config = dict(app.get("llm") or {})
    config.update(enabled=True, debug=False, model="ollama_chat/llama3.2:3b",
                  api_base=args.base_url.rstrip("/"), api_key="", provider_kind="ollama",
                  max_input_chars=12000, max_summary_tokens=512, local_token_multiplier=1,
                  override_diff_with_summary=False, restock_use_fallback_extract=False,
                  token_budget_month=1000000, max_tokens_per_count_period=100000,
                  budget_action="skip_llm", change_summary_default="")
    app["llm"] = config
    descriptor, temporary = tempfile.mkstemp(prefix=".local-ai-", dir=path.parent)
    try:
        os.fchmod(descriptor, info.st_mode & 0o777)
        os.fchown(descriptor, info.st_uid, info.st_gid)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Configured bounded local AI; API protection and unrelated settings preserved")


if __name__ == "__main__":
    main()
