#!/usr/bin/env python3
"""One-shot, interactive-Windows client for the pinned official Visio MCP.

No listener, scheduler, provider credentials, or arbitrary executable selection.
Runtime jobs and receipts belong outside the source repository.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

PACKAGE = "Microsoft.Visio.Mcp@1.0.0-preview.59024787"
TOOLS = {
    "manage_visio", "manage_document", "content_search", "get_visual_model_schema",
    "create_diagram", "read_diagram", "update_diagram", "validate_diagram",
}


def assert_interactive_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Run on Nimo's native interactive Windows desktop")
    session = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise RuntimeError("Cannot determine Windows session")
    active = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    if not session.value or session.value != active:
        raise RuntimeError("Refusing service/SSH/non-console Windows session")


def write_json(path: Path, value: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def decode(result: dict) -> object:
    texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except (ValueError, TypeError):
            return {"text": texts[0]}
    return result.get("structuredContent", result)


def values_for_key(value: object, key: str) -> list:
    found = []
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                found.append(item)
            else:
                found.extend(values_for_key(item, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(values_for_key(item, key))
    return found


def resolve(value: object, results: dict) -> object:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            step, key = value["$ref"].split(".", 1)
            found = values_for_key(results[step], key)
            unique = {json.dumps(v, sort_keys=True): v for v in found}
            if len(unique) != 1:
                raise ValueError(f"Reference {value['$ref']} has {len(unique)} unique values")
            return next(iter(unique.values()))
        return {k: resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, results) for v in value]
    return value


class Client:
    def __init__(self, root: Path, timeout: float):
        self.timeout = timeout
        self.next_id = 0
        self.responses: queue.Queue = queue.Queue()
        self.stderr = (root / "server.stderr.log").open("w", encoding="utf-8")
        self.noise = (root / "server.stdout-nonprotocol.log").open("w", encoding="utf-8")
        env = dict(os.environ, VISIO_APP_VISIBLE="true", VISIO_MCP_ROOT=str(root))
        self.process = subprocess.Popen(
            [r"C:\Program Files\dotnet\dotnet.exe", "tool", "exec", PACKAGE, "--yes"],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
                if isinstance(message, dict) and message.get("jsonrpc") == "2.0":
                    self.responses.put(message)
                else:
                    self.noise.write(line)
                    self.noise.flush()
            except ValueError:
                self.noise.write(line)
                self.noise.flush()
        self.responses.put({"_eof": True})

    def send(self, method: str, params=None, request_id=None):
        obj = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            obj["params"] = params
        if request_id is not None:
            obj["id"] = request_id
        self.process.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params=None):
        self.next_id += 1
        current_id = self.next_id
        self.send(method, params, current_id)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for {method}; inspect before retry")
            try:
                message = self.responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"Timed out waiting for {method}; inspect before retry") from exc
            if message.get("_eof"):
                raise RuntimeError(f"MCP process exited ({self.process.poll()}); inspect logs")
            if message.get("id") == current_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError(json.dumps(message["error"]))
                return message["result"]
            if "method" in message and "id" in message:
                self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                    "error": {"code": -32601, "message": "Client feature not supported"}}) + "\n")
                self.process.stdin.flush()

    def call(self, name: str, arguments: dict):
        if name not in TOOLS:
            raise ValueError(f"Unapproved MCP tool: {name}")
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        parsed = decode(result)
        if result.get("isError") or (isinstance(parsed, dict) and parsed.get("ok") is False):
            raise RuntimeError(json.dumps({"tool": name, "result": parsed}, ensure_ascii=False))
        return parsed

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            self.process.terminate()  # Only this explicitly spawned server process.
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.stderr.close()
        self.noise.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    opts = parser.parse_args()
    assert_interactive_windows()
    job_path = opts.job.resolve(strict=True)
    job = json.loads(job_path.read_text(encoding="utf-8-sig"))
    if job.get("purpose") != "visio-evaluation":
        raise ValueError("Job must explicitly declare purpose=visio-evaluation")
    root = job_path.parent / (job_path.stem + "-" + uuid.uuid4().hex[:8])
    root.mkdir(exist_ok=False)
    receipt = {"package": PACKAGE, "startedUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "status": "running", "steps": [], "results": {}}
    receipt_path = root / "receipt.json"
    write_json(receipt_path, receipt)
    client = None
    created = set()
    closed = set()
    try:
        client = Client(root, opts.timeout)
        receipt["initialize"] = client.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "edsys-visio-evaluation", "version": "0.1.0"}})
        client.send("notifications/initialized")
        catalog = client.request("tools/list")
        write_json(root / "tools.json", catalog)
        names = {t["name"] for t in catalog["tools"]}
        if names != TOOLS:
            raise RuntimeError(f"Unexpected tool catalog: {sorted(names)}")
        for step in job.get("steps", []):
            name = step["id"]
            if name in receipt["results"]:
                raise ValueError("Duplicate step ID")
            args = resolve(step.get("arguments", {}), receipt["results"])
            pending = {"id": name, "tool": step["tool"], "arguments": args, "status": "running"}
            receipt["steps"].append(pending)
            write_json(receipt_path, receipt)
            result = client.call(step["tool"], args)
            receipt["results"][name] = result
            pending["status"] = "complete"
            if step["tool"] == "manage_document":
                if args["action"] == "create":
                    created.update(values_for_key(result, "diagramId"))
                elif args["action"] == "close":
                    closed.add(args["diagramId"])
            write_json(receipt_path, receipt)
        receipt["status"] = "complete"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["error"] = str(exc)
        # Preserve only drawings created by this job; never close/retry a failed mutation.
        receipt["recovery"] = []
        if client:
            for diagram_id in created - closed:
                destination = root / ("recovery-" + uuid.uuid4().hex[:8] + ".vsdx")
                try:
                    saved = client.call("manage_document", {"action": "save", "diagramId": diagram_id,
                        "outputPath": str(destination), "options": {"overwrite": False}})
                    receipt["recovery"].append(saved)
                except Exception as recovery_error:
                    receipt["recovery"].append({"error": str(recovery_error)})
    finally:
        receipt["finishedUtc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(receipt_path, receipt)
        if client:
            client.close()
    print(json.dumps({"status": receipt["status"], "receipt": str(receipt_path)}))
    return 0 if receipt["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
