#!/usr/bin/env python3
"""Run a bounded, ephemeral native Codex job with only the pilot MCP settings."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tomllib
import uuid

from mcp_batch import assert_interactive_windows, write_json


def invoke(root, prompt, run, receipt, read_only):
    assert_interactive_windows()
    config = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8-sig"))
    server = config["mcp_servers"]["visio-local"]
    codex = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
    args = [str(codex), "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
            "--sandbox", "read-only", "--json", "-C", str(root),
            "--disable", "plugins", "--disable", "apps",
            "--output-last-message", str(run / "answer.md")]
    # A workspace selection takes precedence; global authentication is never copied.
    base_path = Path.home() / ".codex" / "config.toml"
    base = tomllib.loads(base_path.read_text(encoding="utf-8-sig"))
    selected_model = config.get("model", base.get("model"))
    if isinstance(selected_model, str):
        args += ["-c", "model=" + json.dumps(selected_model)]
    for key in ("command", "args", "startup_timeout_sec", "tool_timeout_sec"):
        args += ["-c", "mcp_servers.visio-local." + key + "=" + json.dumps(server[key])]
    for key, value in server.get("env", {}).items():
        args += ["-c", "mcp_servers.visio-local.env." + key + "=" + json.dumps(value)]
    if read_only:
        args += ["-c", "mcp_servers.visio-local.enabled_tools=" + json.dumps(
            ["manage_visio", "read_diagram", "content_search", "get_visual_model_schema"])]
    with (run / "events.jsonl").open("w", encoding="utf-8") as out, \
            (run / "stderr.log").open("w", encoding="utf-8") as err:
        process = subprocess.Popen(args + ["-"], stdin=subprocess.PIPE,
            text=True, encoding="utf-8", stdout=out, stderr=err, cwd=root)
        receipt["processId"] = process.pid
        write_json(run / "receipt.json", receipt)
        process.communicate(prompt.read_text(encoding="utf-8-sig"))
        return process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", type=Path)
    parser.add_argument("--read-only", action="store_true", help="Omit drawing-write MCP tools")
    opts = parser.parse_args()
    root = Path.home() / "Documents" / "VisioMcp" / "EdSysPilot"
    prompt = opts.prompt.resolve()
    if not prompt.is_relative_to(root.resolve()):
        raise ValueError("Prompt must be staged inside the pilot workspace")
    run = root / "jobs" / (prompt.stem + "-codex-" + uuid.uuid4().hex[:8])
    run.mkdir(exist_ok=False)
    receipt = {"status": "running", "startedUtc": dt.datetime.now(dt.timezone.utc).isoformat(), "workerPid": os.getpid(),
               "prompt": prompt.name, "readOnly": opts.read_only}
    write_json(run / "receipt.json", receipt)
    code = 1
    try:
        code = invoke(root, prompt, run, receipt, opts.read_only)
        if code == 0 and (not (run / 'answer.md').is_file() or not (run / 'answer.md').stat().st_size):
            code = 1
            receipt['error'] = 'Codex exited without a final answer; inspect actual outputs'
        receipt.update(status="complete" if code == 0 else "failed", exitCode=code)
    except Exception as exc:
        receipt.update(status="failed", error=str(exc), exitCode=code)
    finally:
        receipt["finishedUtc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(run / "receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "receipt": str(run / "receipt.json")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
