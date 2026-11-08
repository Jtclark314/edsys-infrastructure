#!/usr/bin/env python3
"""Bounded Visio jobs through the existing Nimo interactive dispatcher; no service."""
import argparse
import importlib.util
import json
import re
from pathlib import Path, PureWindowsPath
import subprocess
import uuid

spec = importlib.util.spec_from_file_location("nimo_desktop", Path(__file__).resolve().parents[1] / "nimo-desktop-control" / "controller.py")
nimo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nimo)


def paths():
    value = json.loads(nimo.remote("@{home=[Environment]::GetFolderPath('UserProfile');python=(Get-Command python -ErrorAction Stop).Source}|ConvertTo-Json"))
    return str(PureWindowsPath(value["home"]) / "Documents" / "VisioMcp" / "EdSysPilot"), value["python"]


def copy(source, target):
    c = nimo.config()
    subprocess.run(["scp", "-q", "-F", c["ssh_config"], "-o", "BatchMode=yes", source, target], check=True, timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    run = sub.add_parser("run")
    run.add_argument("--prompt", type=Path, required=True)
    run.add_argument("--read-only", action="store_true", help="Exclude drawing-write MCP tools")
    collect = sub.add_parser("collect")
    collect.add_argument("--destination", type=Path, required=True)
    collect.add_argument("--job", help="Collect this job's answer/receipt instead of all drawing artifacts")
    collect.add_argument("--evidence", action="store_true", help="Include private tool-event evidence for the selected job")
    opts = parser.parse_args()
    if opts.action == "run":
        state = nimo.request("status")
        if state.get("paused") or not state.get("interactive"):
            raise RuntimeError("Nimo must be signed in, interactive, and unpaused")
        prompt = opts.prompt.resolve(strict=True)
        if not prompt.is_file() or prompt.stat().st_size > 256000:
            raise ValueError("Prompt must be a text file below 256 KB")
        prompt.read_text(encoding="utf-8")
        root, python = paths()
        job_name = "handoff-" + uuid.uuid4().hex + ".txt"
        target = str(PureWindowsPath(root) / "jobs" / job_name)
        c = nimo.config()
        copy(str(prompt), c["host"] + ":" + target.replace("\\", "/"))
        # Use launch rather than a captured PowerShell Start-Process: long child jobs
        # can keep the dispatcher's captured pipes open until the child exits.
        arguments = [str(PureWindowsPath(root) / "run_native_codex.py"), target]
        if opts.read_only:
            arguments.append("--read-only")
        value = nimo.request("launch", executable=python, arguments=arguments)
        print(json.dumps({"status": "started", "job": job_name, "processId": value["process_id"],
                          "note": "Inspect status and collect results; launch is not completion."}))
    elif opts.action == "status":
        state = nimo.request("status")
        root, _ = paths()
        script = "$r=" + nimo.literal(root) + ";@(Get-ChildItem ($r+'/jobs') -Filter receipt.json -Recurse | ForEach-Object {$x=Get-Content $_.FullName -Raw|ConvertFrom-Json;$state=$x.status;if($state -eq 'running' -and $x.workerPid -and -not (Get-Process -Id $x.workerPid -ErrorAction SilentlyContinue)){$state='inspection-required'};@{job=$_.Directory.Name;status=$state;exitCode=$x.exitCode;answerAvailable=(Test-Path (Join-Path $_.DirectoryName 'answer.md'))}})|ConvertTo-Json -Depth 4"
        jobs = json.loads(nimo.remote(script) or "[]")
        print(json.dumps({"interactive": state.get("interactive"), "paused": state.get("paused"), "jobs": jobs}, indent=2))
    else:
        root, _ = paths()
        destination = opts.destination.resolve()
        destination.mkdir(parents=True, mode=0o700, exist_ok=True)
        c = nimo.config()
        if opts.job:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", opts.job):
                raise ValueError("Invalid job directory name")
            job_dir = destination / opts.job
            job_dir.mkdir(mode=0o700, exist_ok=True)
            remote = c["host"] + ":" + root.replace("\\", "/") + "/jobs/" + opts.job + "/"
            copy(remote + "receipt.json", str(job_dir / "receipt.json"))
            receipt = json.loads((job_dir / "receipt.json").read_text(encoding="utf-8-sig"))
            available = json.loads(nimo.remote('Test-Path -LiteralPath ' + nimo.literal(str(PureWindowsPath(root) / 'jobs' / opts.job / 'answer.md')) + '|ConvertTo-Json'))
            if available:
                copy(remote + "answer.md", str(job_dir / "answer.md"))
            if opts.evidence and receipt.get('processId'):
                copy(remote + "events.jsonl", str(job_dir / "events.jsonl"))
            print(json.dumps({"status": "collected", "jobStatus": receipt.get("status"), "directory": str(job_dir)}))
        else:
            if opts.evidence:
                raise ValueError('--evidence requires --job to avoid mixing unrelated runs')
            subprocess.run(["scp", "-q", "-r", "-F", c["ssh_config"], "-o", "BatchMode=yes",
                            c["host"] + ":" + root.replace("\\", "/") + "/artifacts", str(destination)],
                           check=True, timeout=60)
            print(json.dumps({"status": "collected", "directory": str(destination / "artifacts")}))


if __name__ == "__main__":
    main()
