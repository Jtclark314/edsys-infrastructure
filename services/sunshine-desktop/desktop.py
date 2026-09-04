#!/usr/bin/env python3
"""Pinned X11 Sunshine supervisor and reversible single-display preparation.

Run unprivileged in the physical console's explicit DISPLAY/XAUTHORITY context.
Only the Desktop application is supported. Private layout state stays outside Git.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

STATE = Path(os.environ.get("EDSYS_SUNSHINE_STATE", "/home/jeremy/.local/state/edsys-sunshine"))
XRANDR = "/usr/bin/xrandr"
CONFIG = "/home/jeremy/.config/sunshine/sunshine.conf"
NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")


def parse_layout(text: str, require_primary: bool = True) -> list[dict]:
    outputs, current = [], None
    for line in text.splitlines():
        header = re.match(r"^(\S+) (connected|disconnected)\b(.*)", line)
        if header:
            name, connected, detail = header.groups()
            if not NAME.fullmatch(name):
                raise ValueError("Unexpected output name")
            mode = re.search(r"\b(\d+)x(\d+)([+-]\d+)([+-]\d+)\b", detail)
            current = {"name": name, "connected": connected == "connected", "active": bool(mode)}
            if mode:
                after = detail[mode.end():].strip()
                # Unusual rotations/reflections need their own acceptance; do not guess.
                if not after.startswith("(normal "):
                    raise ValueError("Only normal, unreflected displays are qualified")
                current.update(mode=f"{mode[1]}x{mode[2]}", x=int(mode[3]), y=int(mode[4]),
                               primary=" primary " in f" {detail} ", rate=None)
            outputs.append(current)
        elif current and current["active"] and line[:1].isspace() and "*" in line:
            rate = re.search(r"(\d+(?:\.\d+)?)\*", line)
            if rate:
                current["rate"] = rate[1]
    if not outputs or any(o["active"] and not o.get("rate") for o in outputs):
        raise ValueError("Incomplete XRandR layout")
    if require_primary and len([o for o in outputs if o.get("primary")]) != 1:
        raise ValueError("Exactly one active primary display is required")
    return outputs


def layout(require_primary: bool = True) -> list[dict]:
    result = subprocess.run([XRANDR, "--query"], capture_output=True, text=True, check=True, timeout=10)
    return parse_layout(result.stdout, require_primary)


def identity_scaling() -> None:
    text = subprocess.run([XRANDR, "--verbose"], capture_output=True, text=True, check=True, timeout=10).stdout
    matches = list(re.finditer(r"Transform:\s*([\d. -]+)\n\s*([\d. -]+)\n\s*([\d. -]+)", text))
    if not matches:
        raise ValueError("Display transforms could not be verified")
    for match in matches:
        values = [float(v) for row in match.groups() for v in row.split()]
        if values != [1, 0, 0, 0, 1, 0, 0, 0, 1]:
            raise ValueError("Non-identity display scaling is not qualified")


def single_args(outputs: list[dict]) -> list[str]:
    primary = next(o for o in outputs if o.get("primary"))
    args = [XRANDR, "--output", primary["name"], "--mode", "1920x1080", "--rate", "60",
            "--pos", "0x0", "--rotate", "normal", "--primary"]
    for output in outputs:
        if output["active"] and output["name"] != primary["name"]:
            args += ["--output", output["name"], "--off"]
    return args


def restore_args(saved: list[dict], current: list[dict]) -> list[str]:
    connected = {o["name"] for o in current if o["connected"]}
    if any(o["active"] and o["name"] not in connected for o in saved):
        raise ValueError("A saved active display is absent; retain the recovery snapshot")
    args = [XRANDR]
    for output in saved:
        if not NAME.fullmatch(output["name"]):
            raise ValueError("Invalid saved output name")
        if output["name"] not in connected:
            continue
        args += ["--output", output["name"]]
        if output["active"]:
            if not re.fullmatch(r"\d+x\d+", output["mode"]) or not re.fullmatch(r"\d+(\.\d+)?", output["rate"]):
                raise ValueError("Invalid saved mode")
            args += ["--mode", output["mode"], "--rate", output["rate"], "--pos",
                     f"{int(output['x'])}x{int(output['y'])}", "--rotate", "normal"]
            if output["primary"]:
                args += ["--primary"]
        else:
            args += ["--off"]
    return args


def signature(outputs: list[dict]) -> list[tuple]:
    return sorted((o["name"], o["mode"], o["x"], o["y"], o["primary"], round(float(o["rate"]), 1))
                  for o in outputs if o["active"])


def locked_action(action: str) -> None:
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "layout.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        snapshot = STATE / "layout.json"
        if action == "status":
            print(json.dumps({"saved_layout": snapshot.exists(), "active": signature(layout())}))
        elif action == "acquire":
            if snapshot.exists():
                raise RuntimeError("An unrecovered layout snapshot exists; restore before a new stream")
            before = layout()
            identity_scaling()
            args = single_args(before)
            subprocess.run(args + ["--dryrun"], check=True, capture_output=True, timeout=10)
            record = {"schema": 1, "created": time.time(), "layout": before}
            temp = STATE / "layout.new"
            with temp.open("w") as f:
                json.dump(record, f)
                f.flush()
                os.fsync(f.fileno())
            temp.replace(snapshot)
            try:
                subprocess.run(args, check=True, capture_output=True, timeout=12)
                actual = [o for o in layout() if o["active"]]
                if len(actual) != 1 or actual[0]["mode"] != "1920x1080" or actual[0]["x"] or actual[0]["y"]:
                    raise RuntimeError("Single-display acceptance failed")
            except Exception:
                subprocess.run(restore_args(before, layout(False)), check=True, capture_output=True, timeout=12)
                if signature(before) == signature(layout()):
                    snapshot.unlink()
                raise
            print("EDSYS: single 1080p display acquired", flush=True)
        elif action == "restore" and snapshot.exists():
            saved = json.loads(snapshot.read_text())
            if saved.get("schema") != 1:
                raise ValueError("Unknown layout snapshot format")
            subprocess.run(restore_args(saved["layout"], layout(False)), check=True, capture_output=True, timeout=12)
            if signature(saved["layout"]) != signature(layout()):
                raise RuntimeError("Display restoration did not match saved layout")
            snapshot.unlink()
            print("EDSYS: original monitor layout restored", flush=True)


def disconnect_event(line: str) -> bool:
    return bool(re.search(r"\]: Info: CLIENT DISCONNECTED\s*$", line))


def supervise() -> None:
    # GDM auto-login already exists; do not create/alter it. Wait for its X11 session.
    for _ in range(90):
        try:
            layout()
            break
        except (OSError, ValueError, subprocess.SubprocessError):
            time.sleep(1)
    else:
        raise RuntimeError("Physical console X11 session unavailable")
    locked_action("restore")
    child = subprocess.Popen(["/usr/bin/sunshine", CONFIG], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, bufsize=0)
    stopped = False

    def stop(_signum=None, _frame=None):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    pending = b""
    connected = False
    end_at = None
    try:
        while child.poll() is None and not stopped:
            for key, _ in selector.select(timeout=0.5):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                pending += chunk
                while b"\n" in pending:
                    raw, pending = pending.split(b"\n", 1)
                    line = raw.decode(errors="replace")
                    print(line, flush=True)
                    if "]: Info: CLIENT CONNECTED" in line:
                        connected = True
                    if disconnect_event(line):
                        end_at = time.monotonic() + 2
            if end_at is not None and time.monotonic() >= end_at:
                print("EDSYS: session disconnected; recycle host and restore layout", flush=True)
                break
            snapshot = STATE / "layout.json"
            if not connected and snapshot.exists() and time.time() - snapshot.stat().st_mtime > 45:
                print("EDSYS: unconnected launch timed out; restoring layout", flush=True)
                break
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        locked_action("restore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "acquire", "restore", "supervise"])
    args = parser.parse_args()
    try:
        supervise() if args.action == "supervise" else locked_action(args.action)
    except Exception as exc:
        print(f"EDSYS: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
