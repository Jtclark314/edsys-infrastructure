#!/usr/bin/python3
"""Verify installed CLI/schema behavior while refusing network/device operations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from tool_launcher import launch_spec

SOURCE = Path(__file__).resolve().parent
# This is a verification guard, not a security sandbox. It catches accidental
# operations by these trusted Python CLIs, including subprocess-based discovery.
GUARD = r'''
import os, runpy, sys

def guard(event, args):
    if event == "socket.bind" and args[1] == ("::1", 0):
        raise OSError("Offline verification disables urllib3 IPv6 availability probe")
    if event == "subprocess.Popen" and args[1] in (["xdg-user-dir", "DOCUMENTS"], ["uname", "-p"]):
        return  # Read-only local desktop/CPU metadata used by PlatformIO imports.
    if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo", "socket.sendto", "subprocess.Popen", "os.system"}:
        sys.stderr.write("OFFLINE_OPERATION_REFUSED: " + event + "\n")
        raise RuntimeError("Offline verification refused operation: " + event)
    if event in {"open", "os.listdir", "os.scandir"} and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        if (path == "/dev" or path.startswith("/dev/") and path != "/dev/null" or path.startswith("/sys/class/tty")):
            sys.stderr.write("OFFLINE_OPERATION_REFUSED: device access\n")
            raise RuntimeError("Offline verification refused device access")
sys.addaudithook(guard)
module, *args = sys.argv[1:]
sys.argv = [module, *args]
runpy.run_module(module, run_name="__main__")
'''


def guarded_cli(root: Path, name: str, args: list[str]) -> subprocess.CompletedProcess:
    argv, env = launch_spec(root, name, args, dict(os.environ))
    result = subprocess.run([argv[0], "-I", "-c", GUARD, argv[3], *argv[4:]],
                            env=env, capture_output=True, text=True, check=True, timeout=30)
    if "OFFLINE_OPERATION_REFUSED:" in result.stderr:
        raise RuntimeError("CLI attempted a blocked operation, even though it returned success")
    return result


def main() -> None:
    os.umask(0o077)
    root = Path.home() / ".local/share/edsys-esp32-tools"
    project = root / "projects/schema-only"
    project.mkdir(mode=0o700, exist_ok=True)
    config = project / "schema-only.yaml"
    # Only this fixed, credential-free fixture is copied. Other projects stay private.
    shutil.copyfile(SOURCE / "examples/schema-only.yaml", config)
    expected = {
        "edsys-esphome": (["version"], "2026.9.1"),
        "edsys-pio": (["--version"], "6.2.0"),
        "edsys-esptool": (["version"], "5.3.1"),
        "edsys-espbuilder": (["--version"], "1.14.9 (esphome 2026.9.1)"),
    }
    versions = {}
    for name, (args, version) in expected.items():
        result = guarded_cli(root, name, args)
        if version not in result.stdout:
            raise RuntimeError(f"Unexpected version for {name}")
        versions[name] = version
    result = guarded_cli(root, "edsys-esphome", ["config", str(config)])
    if "Configuration is valid!" not in result.stderr or "compile_process_limit: 2" not in result.stdout:
        raise RuntimeError("Schema validation or compile limit readback failed")
    sdk_paths = [root / "state" / engine / child
                 for engine in ("esphome-platformio", "platformio")
                 for child in ("platforms", "packages")]
    if any(path.exists() and any(path.iterdir()) for path in sdk_paths):
        raise RuntimeError("Board SDK/package state is populated; inspect before claiming empty SDK state")
    hashes = {str(path.relative_to(SOURCE)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in [SOURCE / "install.py", SOURCE / "tool_launcher.py", SOURCE / "verify_offline.py",
                           SOURCE / "requirements-esphome.lock", SOURCE / "requirements-platformio.lock",
                           SOURCE / "examples/schema-only.yaml"]}
    report = {"schema": 1, "versions": versions, "schema_validation": "passed",
              "compile_process_limit": 2, "network_and_device_audit_guard": "enabled; urllib3 loopback IPv6 probe prevented",
              "board_sdk_packages": "empty", "firmware_compiled": False, "firmware_flashed": False,
              "source_sha256": hashes}
    (root / "verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Offline CLI/schema checks passed; private report: {root / 'verification.json'}")


if __name__ == "__main__":
    main()
