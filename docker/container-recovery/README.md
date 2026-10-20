# Ordered Container Recovery

This folder defines the 9950x desired container state and systemd integration
used after a Docker daemon restart or host reboot.

## Safety model

- Automatic recovery only targets explicitly listed long-running services.
- Existing containers are required before a tier is started.
- Recovery uses `docker compose start`, which can only start existing containers.
- Already-running healthy services are skipped rather than restarted or
  blocked on Docker health state reinitialization, except for the explicit
  Docker-socket consumer allowlist when its bind points to a stale socket inode.
- Docker live restore remains enabled so daemon-only maintenance preserves
  ordinary workloads. After Docker returns, the controller compares the host
  socket identity with each approved consumer and restarts only stale consumers.
- Dockerd receives a 120-second container shutdown budget, while systemd gives
  the daemon three minutes to complete that work before escalation.
- Missing containers, mounts, or blocking health gates stop the sequence.
- `/run/edsys/container-recovery.disabled` suppresses recovery during maintenance.
- A five-minute cooldown prevents restart storms.
- The recurring timer is audit-only and never starts containers.
- Runtime status, notification URLs, environment files, and rollback snapshots
  remain outside Git.

The one-shot `langfuse-minio-init` service and historical AnythingLLM migration
container are intentionally absent. The legacy 9950x UniFi project is excluded
because the authoritative controller runs on `edrouter-node0`.

## Container-root socket probe

The 2026-10-20 probe correction resolves `/var/run/docker.sock` inside each
approved consumer's process root. A plain host-side
`stat /proc/PID/root/var/run/docker.sock` can follow an absolute `/var/run -> /run`
symlink into the host root and falsely report a stale consumer as current.
Hardcoding `/run/docker.sock` is also insufficient when a container has a real
`/var/run` directory.

The helper now walks pinned `O_PATH` descriptors from `/proc/PID/root`, resolves
absolute and relative symlinks inside that root, clamps `..` at the root and
fails closed on missing paths or more than 40 symlink traversals. It reads
metadata only; it does not connect to or invoke the container's Docker socket.
The existing nine-consumer allowlist, restart rules, audit-only timer, cooldown,
maintenance flag and Docker-start hook are unchanged.

Focused tests cover the original false-green host escape with real temporary
filesystem entries, `/var/run` as absolute/relative links or a real directory,
nested/final links, root confinement and loop/missing-path failures. Run them in
the existing test environment:

```bash
/home/jeremy/code/edsys-ai-portal/.venv/bin/python -m pytest -q \
  scripts/ops/tests/test_edsys_container_recovery.py
```

When updating only this helper, first back up the installed
`/usr/local/sbin/edsys-container-recovery`, validate the candidate and install
that file alone. Do not rerun the broader installer or actuate recovery just to
test the probe. A focused read-only consumer audit can identify stale consumers
without calling `recover`, restarting containers or sending a notification.
The CLI audit also writes its normal status and invokes the configured
notification path; use the probe function directly when strictly read-only
acceptance is required.

The pre-install 2026-10-20 rooted audit found seven current bindings and two
stale bindings (`telegraf-9950x` and `edsys-portal`); the old concatenated probe
falsely reported all nine as current. Those findings are distinct from helper
qualification and are not repaired by installing a Python file. No container
restart is part of the probe regression test. Those two rebinds remain deferred
to preserve current use of the Portal.

The installed five-minute timer invokes `audit`, which reports status and uses
the existing notification path without calling rebind or starting containers.
Replacing the helper does not invoke either the timer or recovery. At the next
reviewed Docker daemon start, the existing `ExecStartPost` hook starts `recover`;
unless suppressed by its existing maintenance flag, it will correctly detect
and restart stale members of the same nine-consumer allowlist before tier
checks. Plan that maintenance knowing it can restart those consumers, including
the presently deferred Portal and Telegraf bindings. No hook or policy expansion
was added to accomplish the corrected detection.

The private controller rollback checkpoint is
`/var/backups/edsys-container-recovery/2026-10-20-rooted-socket-probe/`. Restore
the saved installed helper alone if rollback is required; keep manifest and
systemd policy untouched. Raw runtime records remain in that private directory.

## Install

```bash
sudo scripts/ops/install-container-recovery.sh
sudo /usr/local/sbin/edsys-container-recovery recover --dry-run --force
sudo /usr/local/sbin/edsys-container-recovery audit
```

The installer validates `daemon.json`, keeps Docker live restore enabled, sets
bounded `json-file` defaults (`50m` with
three retained files) for newly created containers, reloads the daemon,
installs the recovery/audit units, and enables the audit timer. It does not
restart Docker or recreate containers; existing containers keep their current
logging configuration until they are recreated.

## Maintenance

```bash
sudo install -d -m 0755 /run/edsys
sudo touch /run/edsys/container-recovery.disabled
# perform reviewed maintenance
sudo rm /run/edsys/container-recovery.disabled
sudo /usr/local/sbin/edsys-container-recovery recover --dry-run --force
```

## Out-of-band notification

Place a private Home Assistant webhook or external heartbeat URL as the only
line in `/etc/edsys-container-recovery/notify-url` with mode `0600`. Until a
destination is configured, systemd journal and the runtime status JSON remain
the available evidence.
