#!/usr/bin/python3
"""Explicit local install; no services, board tools, or physical device access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

VERSIONS = {"esphome": "2026.9.1", "platformio": "6.2.0"}
LAUNCHERS = ("edsys-esphome", "edsys-pio", "edsys-esptool", "edsys-espbuilder")
MARKER = "EdSys isolated ESP32 tools v1\n"
SOURCE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_launcher_links(bin_dir: Path, root: Path) -> None:
    """Refuse collisions before installing anything; never replace unrelated tools."""
    for name in LAUNCHERS:
        path = bin_dir / name
        expected = root / "bin" / name
        if path.exists() or path.is_symlink():
            if not path.is_symlink() or path.readlink() != expected:
                raise RuntimeError(f"Refusing existing unrelated launcher: {path}")


def own_root(root: Path) -> None:
    if root.is_symlink():
        raise RuntimeError("Runtime root must not be a symlink")
    if root.exists():
        marker = root / ".edsys-owner"
        if root.stat().st_uid != os.getuid() or marker.is_symlink() or not marker.is_file() or marker.read_text() != MARKER:
            raise RuntimeError("Refusing unowned runtime directory")
    else:
        root.mkdir(parents=True, mode=0o700)
        (root / ".edsys-owner").write_text(MARKER)
    root.chmod(0o700)


def run(argv: list[str], env: dict[str, str]) -> None:
    subprocess.run(argv, env=env, check=True)


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        raise SystemExit("ESPHome 2026.9.1 requires Python >=3.12,<3.15")
    os.umask(0o077)
    root = Path.home() / ".local/share/edsys-esp32-tools"
    bin_dir = Path.home() / ".local/bin"
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install the existing managed uv prerequisite first")
    check_launcher_links(bin_dir, root)
    own_root(root)
    for child in ("venvs", "bin", "state", "cache", "projects"):
        path = root / child
        if path.is_symlink():
            raise RuntimeError(f"Refusing symlink in owned runtime: {path}")
        path.mkdir(exist_ok=True, mode=0o700)
    env = dict(os.environ)
    for variable in list(env):
        if variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV") or variable.startswith(("UV_", "PIP_")):
            env.pop(variable, None)
    env.update({"UV_CACHE_DIR": str(root / "cache/uv"), "UV_PYTHON_DOWNLOADS": "never",
                "UV_CONCURRENT_BUILDS": "2", "CMAKE_BUILD_PARALLEL_LEVEL": "2", "MAKEFLAGS": "-j2"})
    environments = {}
    for name, version in VERSIONS.items():
        lock = SOURCE / f"requirements-{name}.lock"
        digest = sha256(lock)
        relative = Path("venvs") / f"{name}-{version}-{digest[:12]}"
        target = root / relative
        stamp = target / ".edsys-lock-sha256"
        if target.is_symlink() or (target.exists() and (not stamp.is_file() or stamp.read_text().strip() != digest)):
            raise RuntimeError(f"Refusing incomplete/unowned environment: {target}")
        if not target.exists():
            run([uv, "--no-config", "venv", "--python", sys.executable, str(target)], env)
            # Only pinned Python packages; no board platforms, frameworks, SDKs or firmware.
            run([uv, "--no-config", "pip", "sync", "--python", str(target / "bin/python"), "--default-index", "https://pypi.org/simple", "--require-hashes", str(lock)], env)
            stamp.write_text(digest + "\n")
        run([uv, "--no-config", "pip", "check", "--python", str(target / "bin/python")], env)
        metadata_code = "import importlib.metadata as m,json; print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()},sort_keys=True))"
        result = subprocess.run([str(target / "bin/python"), "-I", "-c", metadata_code], check=True, text=True, capture_output=True, env=env)
        packages = json.loads(result.stdout)
        if packages.get(name) != version:
            raise RuntimeError(f"Unexpected {name} version")
        if name == "esphome" and packages.get("esphome-device-builder") != "1.14.9":
            raise RuntimeError("Unexpected ESPHome Device Builder version")
        environments[name] = {"path": str(relative), "version": version, "lock_sha256": digest, "packages": packages}
    launcher_source = SOURCE / "tool_launcher.py"
    for name in LAUNCHERS:
        target = root / "bin" / name
        if target.is_symlink():
            raise RuntimeError(f"Refusing symlink launcher target: {target}")
        shutil.copyfile(launcher_source, target)
        target.chmod(0o700)
    manifest = {"schema": 1, "python": sys.version.split()[0], "environments": environments,
                "launcher_sha256": sha256(launcher_source), "installer_sha256": sha256(Path(__file__).resolve())}
    temporary = root / "install.json.new"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(root / "install.json")
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in LAUNCHERS:
        path = bin_dir / name
        if not path.is_symlink():
            path.symlink_to(root / "bin" / name)
    print(f"Installed {', '.join(LAUNCHERS)}; private manifest: {root / 'install.json'}")


if __name__ == "__main__":
    main()
