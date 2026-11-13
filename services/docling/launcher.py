#!/usr/bin/python3
"""Private on-demand Docling launcher: one bounded CPU job, no service/listener."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    os.umask(0o077)
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "install.json").read_text())
    if len(sys.argv) == 2 and sys.argv[1] in ("doctor", "--version"):
        print(
            json.dumps(
                {
                    "version": manifest["docling_version"],
                    "cpu_only": True,
                    "venv": manifest["venv"],
                    "models": manifest["models"],
                    "models_prepared": (
                        Path(manifest["models"]) / "models.json"
                    ).is_file(),
                    "web_interface": False,
                    "limits": {
                        "cpu_quota": "400%",
                        "memory_max": "6G",
                        "swap_max": 0,
                        "parallel_jobs": 1,
                    },
                },
                indent=2,
            )
        )
        return
    if len(sys.argv) == 2 and sys.argv[1] == "prepare-models":
        script, args = "prepare_models.py", []
        offline = False
    else:
        script, args = "convert.py", sys.argv[1:]
        offline = True
    # Never put account/provider credentials or unrelated session state in process arguments.
    env = {
        key: os.environ[key]
        for key in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE")
        if key in os.environ
    }
    env.update(
        {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "4",
            "NUMEXPR_NUM_THREADS": "4",
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HOME": manifest["models"] + "/huggingface",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
            "DOCLING_ARTIFACTS_PATH": manifest["models"] + "/artifacts",
            "DOCLING_ROOT": str(root),
            "DOCLING_MODEL_ROOT": manifest["models"],
            "XDG_CACHE_HOME": str(root / "cache"),
            "PYTHONNOUSERSITE": "1",
        }
    )
    if offline:
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    with (root / "state/job.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another Docling job is active; retry when it finishes")
        command = [
            "systemd-run",
            "--user",
            "--wait",
            "--collect",
            "--pipe",
            "--quiet",
            "-p",
            "CPUQuota=400%",
            "-p",
            "MemoryMax=6G",
            "-p",
            "MemorySwapMax=0",
            "-p",
            "Nice=10",
        ]
        if offline:
            command += ["-p", "RestrictAddressFamilies=AF_UNIX"]
        # systemd user services do not inherit subprocess env; pass a clean env explicitly.
        command += ["/usr/bin/env", "-i"] + [f"{k}={v}" for k, v in env.items()]
        command += [
            manifest["venv"] + "/bin/python",
            "-I",
            str(Path(manifest["release"]) / script),
            *args,
        ]
        raise SystemExit(subprocess.run(command).returncode)


if __name__ == "__main__":
    main()
