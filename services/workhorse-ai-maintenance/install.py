#!/usr/bin/python3
"""Install the hash-locked S3 maintenance tools without global package changes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    os.umask(0o077)
    source = Path(__file__).resolve().parent
    root = Path.home() / ".local/share/edsys-workhorse-maintenance"
    marker = "EdSys isolated Workhorse S3 maintenance v1\n"
    if os.geteuid() == 0:
        raise RuntimeError("Install as Jeremy, not root")
    if root.is_symlink() or (
        root.exists()
        and (
            not (root / ".edsys-owner").is_file()
            or (root / ".edsys-owner").read_text() != marker
        )
    ):
        raise RuntimeError("Refusing an unrelated runtime directory")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    (root / ".edsys-owner").write_text(marker)
    (root / "venvs").mkdir(mode=0o700, exist_ok=True)
    lock = source / "requirements.lock"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    target = root / "venvs" / digest[:16]
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("Existing managed uv required")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_"))
        and key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    }
    env.update({"UV_CACHE_DIR": str(root / "cache"), "UV_PYTHON_DOWNLOADS": "never"})
    if not target.exists():
        subprocess.run(
            [uv, "--no-config", "venv", "--python", sys.executable, str(target)],
            check=True,
            env=env,
        )
        subprocess.run(
            [
                uv,
                "--no-config",
                "pip",
                "sync",
                "--python",
                str(target / "bin/python"),
                "--require-hashes",
                str(lock),
            ],
            check=True,
            env=env,
        )
    subprocess.run(
        [uv, "--no-config", "pip", "check", "--python", str(target / "bin/python")],
        check=True,
        env=env,
    )
    metadata = subprocess.check_output(
        [
            str(target / "bin/python"),
            "-I",
            "-c",
            "import importlib.metadata as m,json;print(json.dumps({d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions()}))",
        ],
        text=True,
    )
    actual = json.loads(metadata)
    expected = {
        name.lower().replace("_", "-"): version
        for name, version in re.findall(
            r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock.read_text(), re.M
        )
    }
    if actual != expected:
        raise RuntimeError("Runtime differs from complete package lock")
    link = root / "venv"
    if link.exists() and not link.is_symlink():
        raise RuntimeError("Refusing unrelated venv path")
    temporary = root / ".venv-new"
    temporary.symlink_to(target.relative_to(root))
    os.replace(temporary, link)
    (root / "install.json").write_text(
        json.dumps(
            {"lock_sha256": digest, "packages": actual, "venv": str(target)}, indent=2
        )
        + "\n"
    )
    print(
        json.dumps(
            {"runtime": str(root), "packages": len(actual), "lock_sha256": digest}
        )
    )


if __name__ == "__main__":
    main()
