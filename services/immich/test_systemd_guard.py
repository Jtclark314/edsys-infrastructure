#!/usr/bin/env python3
"""Disposable systemd dependency probe; never starts/stops Docker or Immich.

Run explicitly as root. Only uniquely named probe units and private /run state
are created. Mount identities are fixtures, while the actual guard checks and
systemd stop/start/restart propagation run for real.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def ctl(*args, check=True):
    return subprocess.run(["systemctl", *args], check=check, capture_output=True, text=True, timeout=15)


def until(predicate, reason, seconds=12):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.1)
    raise RuntimeError(reason)


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run this isolated probe as root")
    os.umask(0o077)
    base = Path(tempfile.mkdtemp(prefix="immich-guard-probe-", dir="/run"))
    prefix = base.name
    daemon = prefix + "-daemon.service"
    app = prefix + "-app.service"
    unit_dir = Path("/run/systemd/system")
    files = [unit_dir / daemon, unit_dir / app]
    guard_source = Path(__file__).with_name("guard.py").resolve()
    media, nvme = base / "media", base / "nvme"
    for path in [media / "upload", media / "legacy-library", *[nvme / "immich" / n for n in
                 ("postgres", "redis", "config", "server-data", "model-cache", "logs")]]:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    (media / ".edsys-immich-volume").write_text("expected-media")
    postgres = nvme / "immich" / "postgres"
    (postgres / "PG_VERSION").write_text("14\n")
    (postgres / "global").mkdir()
    (postgres / "global" / "pg_control").write_bytes(bytes(8192))
    media_identity = base / "media-identity"
    media_identity.write_text("expected-media")
    storage_environment = base / "storage.env"
    expected_environment = "IMMICH_MEDIA_UUID=expected-media\nIMMICH_NVME_UUID=expected-nvme\n"
    storage_environment.write_text(expected_environment)
    storage_environment.chmod(0o600)
    counter = base / "starts"
    counter.write_text("0")
    guard = base / "probe_guard.py"
    guard.write_text(f'''import importlib.util
import sys
from pathlib import Path
source=Path({str(guard_source)!r})
spec=importlib.util.spec_from_file_location("actual_guard",source)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
media={str(media)!r}
nvme={str(nvme)!r}
def fixture_identity(target):
    value=Path({str(media_identity)!r}).read_text().strip() if target==media else "expected-nvme"
    return {{"target":target,"uuid":value,"fstype":"ext4","options":"rw,relatime"}}
module.mount_identity=fixture_identity
try:
    module.check(media,sys.argv[1],nvme,sys.argv[2])
except (module.Refused,OSError):
    raise SystemExit(1)
''')
    worker = base / "worker.py"
    worker.write_text(f'''from pathlib import Path
import time
p=Path({str(counter)!r})
p.write_text(str(int(p.read_text())+1))
while True: time.sleep(60)
''')
    daemon_text = """[Unit]
Description=Disposable Immich mock daemon
[Service]
Type=simple
ExecStart=/usr/bin/sleep infinity
"""
    app_text = f"""[Unit]
Description=Disposable Immich guarded dependency
Requires={daemon}
After={daemon}
PartOf={daemon}
StartLimitIntervalSec=5
StartLimitBurst=3
[Service]
Type=simple
EnvironmentFile={storage_environment}
ExecStartPre=/usr/bin/python3 {guard} ${{IMMICH_MEDIA_UUID}} ${{IMMICH_NVME_UUID}}
ExecStart=/usr/bin/python3 {worker}
Restart=on-failure
RestartSec=0.2
[Install]
WantedBy={daemon}
"""
    checks = {}
    try:
        for path, content in zip(files, [daemon_text, app_text]):
            if path.exists():
                raise RuntimeError("probe_unit_name_collision")
            path.write_text(content)
            path.chmod(0o644)
        ctl("daemon-reload")
        ctl("--runtime", "enable", app)
        def count():
            return int(counter.read_text())
        def active():
            return ctl("is-active", app, check=False).stdout.strip() == "active"
        ctl("start", daemon)
        until(lambda: active() and count() == 1, "daemon_start_did_not_start_guarded_app")
        checks["daemon_start_starts_guarded_app"] = True
        ctl("restart", daemon)
        until(lambda: active() and count() == 2, "daemon_restart_did_not_restart_guarded_app")
        checks["daemon_restart_rechecks_guard"] = True
        ctl("stop", daemon)
        until(lambda: not active(), "daemon_stop_did_not_stop_app")
        checks["daemon_stop_stops_app"] = True
        ctl("start", daemon)
        until(lambda: active() and count() == 3, "later_daemon_start_did_not_start_app")
        checks["later_daemon_start_starts_app"] = True
        media_identity.unlink()
        ctl("restart", daemon)
        until(lambda: ctl("is-failed", app, check=False).stdout.strip() == "failed",
              "missing_mount_guard_did_not_fail")
        if count() != 3:
            raise RuntimeError("missing_mount_started_app")
        checks["missing_storage_refuses_app"] = True
        media_identity.write_text("wrong-media")
        ctl("reset-failed", app)
        ctl("restart", daemon)
        until(lambda: ctl("is-failed", app, check=False).stdout.strip() == "failed",
              "wrong_uuid_guard_did_not_fail")
        if count() != 3:
            raise RuntimeError("wrong_uuid_started_app")
        checks["wrong_uuid_refuses_app"] = True
        media_identity.write_text("expected-media")
        ctl("reset-failed", app)
        ctl("restart", daemon)
        until(lambda: active() and count() == 4, "valid_storage_did_not_recover")
        checks["valid_storage_recovers_after_reset"] = True
        ctl("stop", daemon)
        storage_environment.unlink()
        ctl("start", daemon)
        until(lambda: ctl("is-failed", app, check=False).stdout.strip() == "failed",
              "missing_environment_did_not_fail")
        if count() != 4:
            raise RuntimeError("missing_environment_started_app")
        checks["missing_private_environment_refuses_app"] = True
        storage_environment.write_text(expected_environment.replace("expected-media", "wrong-expected-media"))
        ctl("reset-failed", app)
        ctl("restart", daemon)
        until(lambda: ctl("is-failed", app, check=False).stdout.strip() == "failed",
              "wrong_environment_uuid_did_not_fail")
        if count() != 4:
            raise RuntimeError("wrong_environment_uuid_started_app")
        checks["environment_uuid_values_are_checked"] = True
        storage_environment.write_text(expected_environment)
        ctl("reset-failed", app)
        ctl("restart", daemon)
        until(lambda: active() and count() == 5, "restored_environment_did_not_recover")
        checks["valid_environment_recovers_after_reset"] = True
    finally:
        ctl("stop", app, daemon, check=False)
        ctl("--runtime", "disable", app, check=False)
        for path in files:
            path.unlink(missing_ok=True)
        wants = unit_dir / (daemon + ".wants")
        if wants.is_dir() and not any(wants.iterdir()):
            wants.rmdir()
        ctl("reset-failed", app, daemon, check=False)
        ctl("daemon-reload", check=False)
        shutil.rmtree(base)
    print(json.dumps({"status": "passed", "checks": checks}, sort_keys=True))


if __name__ == "__main__":
    main()
