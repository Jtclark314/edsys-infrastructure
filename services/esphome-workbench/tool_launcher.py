#!/usr/bin/python3
"""Unique EdSys launchers; state and builds remain in the private tools root."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

TOOLS = {
    "edsys-esphome": ("esphome", "esphome"),
    "edsys-pio": ("platformio", "platformio"),
    "edsys-esptool": ("esphome", "esptool"),
    "edsys-espbuilder": ("esphome", "esphome_device_builder"),
}


def launch_spec(root: Path, name: str, args: list[str], inherited: dict[str, str]):
    """Resolve only installer-owned environments, without enumerating devices."""
    key, module = TOOLS[name]
    manifest = json.loads((root / "install.json").read_text())
    relative = Path(manifest["environments"][key]["path"])
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("venvs",):
        raise ValueError("Invalid environment path in install manifest")
    python = root / relative / "bin/python"
    if not python.is_file():
        raise ValueError("Tool environment is missing; rerun the reviewed installer")
    env = dict(inherited)
    for variable in list(env):
        if variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV") or variable.startswith("PLATFORMIO_"):
            env.pop(variable, None)
    env.update({
        "PYTHONNOUSERSITE": "1",
        "ESPHOME_DEFAULT_COMPILE_PROCESS_LIMIT": "2",
        "PLATFORMIO_RUN_JOBS": "2",
        "PLATFORMIO_CORE_DIR": str(root / "state" / ("esphome-platformio" if key == "esphome" else "platformio")),
        "PLATFORMIO_CACHE_DIR": str(root / "cache" / ("esphome-platformio" if key == "esphome" else "platformio")),
        "PLATFORMIO_DISABLE_UPGRADE_CHECK": "true",
        "PLATFORMIO_SETTING_ENABLE_TELEMETRY": "false",
        "XDG_CACHE_HOME": str(root / "cache"),
    })
    # Upstream standalone builder otherwise listens on every interface. An
    # explicit operator-supplied --host can override this loopback default.
    if name == "edsys-espbuilder":
        args = ["--host", "127.0.0.1", *args]
    return [str(python), "-I", "-m", module, *args], env


def main() -> None:
    os.umask(0o077)
    root = Path(__file__).resolve().parent.parent
    name = Path(sys.argv[0]).name
    try:
        argv, env = launch_spec(root, name, sys.argv[1:], dict(os.environ))
    except (KeyError, ValueError, OSError) as exc:
        raise SystemExit(f"EdSys tool launcher: {exc}") from exc
    os.execve(argv[0], argv, env)


if __name__ == "__main__":
    main()
