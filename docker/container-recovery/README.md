# Ordered Container Recovery

This folder defines the 9950x desired container state and systemd integration
used after a Docker daemon restart or host reboot.

## Safety model

- Automatic recovery only targets explicitly listed long-running services.
- Existing containers are required before a tier is started.
- Recovery uses `docker compose start`, which can only start existing containers.
- Already-running healthy services are skipped rather than restarted or
  blocked on Docker health state reinitialization.
- Docker live restore is deliberately disabled. On a host shutdown, dockerd
  must stop containers before systemd tears down network namespaces and the
  external Docker data-root mount; restart policies and this ordered recovery
  controller restore the approved services on the next boot.
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

## Install

```bash
sudo scripts/ops/install-container-recovery.sh
sudo /usr/local/sbin/edsys-container-recovery recover --dry-run --force
sudo /usr/local/sbin/edsys-container-recovery audit
```

The installer validates `daemon.json`, enables Docker live restore with a
daemon reload, installs the recovery/audit units, and enables the audit timer.
It does not restart Docker or recreate containers.

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
