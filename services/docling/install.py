#!/usr/bin/python3
"""Install only the isolated, on-demand Docling CPU toolkit for the invoking user."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
ROOT = Path.home() / ".local/share/edsys-docling"
MODELS = Path("/mnt/ai-store/models/docling")
MARKER = "EdSys isolated Docling CPU toolkit v1\n"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def own_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlink runtime: {path}")
    if path.exists():
        marker = path / ".edsys-owner"
        if (
            path.stat().st_uid != os.getuid()
            or not marker.is_file()
            or marker.is_symlink()
            or marker.read_text() != MARKER
        ):
            raise RuntimeError(f"Refusing unmarked runtime: {path}")
    else:
        path.mkdir(parents=True, mode=0o700)
        (path / ".edsys-owner").write_text(MARKER)
    path.chmod(0o700)


def run(argv: list[str], env: dict[str, str]) -> None:
    subprocess.run(argv, check=True, env=env)


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python3 services/docling/install.py")
    if os.geteuid() == 0 or sys.version_info[:2] != (3, 12):
        raise SystemExit("Run as Jeremy using system Python 3.12, without sudo")
    if not Path("/mnt/ai-store").is_mount():
        raise SystemExit("AI Store is not mounted")
    if not shutil.which("tesseract"):
        raise SystemExit("Existing Tesseract OCR is required")
    os.umask(0o077)
    launcher = Path.home() / ".local/bin/edsys-docling"
    expected_link = ROOT / "bin/edsys-docling"
    if launcher.exists() or launcher.is_symlink():
        if not launcher.is_symlink() or launcher.readlink() != expected_link:
            raise SystemExit("Refusing an unrelated edsys-docling launcher")
    own_directory(ROOT)
    # A newly created empty model cache can be adopted, never unrelated contents.
    if MODELS.exists() and not any(MODELS.iterdir()):
        (MODELS / ".edsys-owner").write_text(MARKER)
    own_directory(MODELS)
    for name in ("venvs", "releases", "bin", "cache", "state", "outputs"):
        child = ROOT / name
        if child.is_symlink():
            raise RuntimeError(f"Refusing symlink: {child}")
        child.mkdir(mode=0o700, exist_ok=True)
        child.chmod(0o700)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("The existing managed uv command is required")
    env = dict(os.environ)
    for key in list(env):
        if key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV") or key.startswith(
            ("UV_", "PIP_")
        ):
            env.pop(key, None)
    env.update(
        {
            "UV_CACHE_DIR": str(ROOT / "cache/uv"),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_CONCURRENT_BUILDS": "2",
        }
    )
    lock = SOURCE / "requirements.lock"
    lock_hash = digest(lock)
    venv = ROOT / "venvs" / ("2.150.0-" + lock_hash[:12])
    stamp = venv / ".edsys-lock-sha256"
    if venv.is_symlink() or (
        venv.exists()
        and (not stamp.is_file() or stamp.read_text().strip() != lock_hash)
    ):
        raise RuntimeError(f"Refusing incomplete/unowned environment: {venv}")
    if not venv.exists():
        run([uv, "--no-config", "venv", "--python", sys.executable, str(venv)], env)
        run(
            [
                uv,
                "--no-config",
                "pip",
                "sync",
                "--python",
                str(venv / "bin/python"),
                "--torch-backend",
                "cpu",
                "--require-hashes",
                str(lock),
            ],
            env,
        )
    run([uv, "--no-config", "pip", "check", "--python", str(venv / "bin/python")], env)
    metadata = subprocess.check_output(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            "import json,importlib.metadata as m; print(json.dumps({d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions()}))",
        ],
        text=True,
        env=env,
    )
    actual = json.loads(metadata)
    expected = dict(
        re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock.read_text(), re.M)
    )
    expected = {k.lower().replace("_", "-"): v for k, v in expected.items()}
    if (
        actual != expected
        or actual.get("docling") != "2.150.0"
        or actual.get("torch") != "2.15.0+cpu"
    ):
        raise RuntimeError(
            "Installed environment differs from complete stable CPU lock"
        )
    stamp.write_text(lock_hash + "\n")
    files = ("convert.py", "prepare_models.py", "model-pins.json", "requirements.lock")
    release_hash = hashlib.sha256(
        "".join(digest(SOURCE / name) for name in files).encode()
    ).hexdigest()
    release = ROOT / "releases" / release_hash[:16]
    release.mkdir(mode=0o700, exist_ok=True)
    for name in files:
        destination = release / name
        if destination.is_symlink():
            raise RuntimeError("Unexpected symlink in runtime release")
        shutil.copyfile(SOURCE / name, destination)
        destination.chmod(0o600)
    shutil.copyfile(SOURCE / "launcher.py", expected_link)
    expected_link.chmod(0o700)
    manifest = {
        "schema": 1,
        "docling_version": "2.150.0",
        "venv": str(venv),
        "release": str(release),
        "models": str(MODELS),
        "lock_sha256": lock_hash,
        "release_sha256": release_hash,
        "launcher_sha256": digest(SOURCE / "launcher.py"),
        "packages": actual,
    }
    pending = ROOT / "install.json.new"
    pending.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    pending.replace(ROOT / "install.json")
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if not launcher.is_symlink():
        launcher.symlink_to(expected_link)
    print(
        json.dumps(
            {
                "installed": "edsys-docling",
                "version": actual["docling"],
                "distributions": len(actual),
                "web_interface": False,
                "runtime": str(ROOT),
            }
        )
    )


if __name__ == "__main__":
    main()
