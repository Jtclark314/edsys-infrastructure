# 9950x Full-Host Reboot Acceptance

The one-shot reboot acceptance gate records a private pre-boot baseline, waits
for ordered Docker recovery on the next boot, and verifies the host before an
operator treats the reboot as accepted.

It checks:

- a different kernel boot ID;
- AI Store, Docker data-root, and EdSys Share mounts;
- core SSH, Tailnet, Samba, Docker, containerd, and Netdata services;
- EdSys Share mount validation and exact Tailnet SMB socket posture;
- exact AI Tailnet proxy listeners and health paths;
- ordered container-recovery audit, container identity continuity, and no
  unhealthy containers;
- critical backup, Drive mirror, Git sync, Codex, and monitoring timers;
- NVIDIA visibility, the accepted Codex version, accepted package versions,
  and absence of newly failed systemd services.

Private run records live under `/var/lib/edsys-reboot-acceptance/` and must not
enter Git or RAG.

## Install and arm

```bash
sudo scripts/ops/install-reboot-acceptance.sh
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
sudo /usr/local/sbin/edsys-reboot-acceptance arm --run "${run_id}"
sudo systemctl reboot
```

The service is enabled but normally skipped because its private pending marker
does not exist. A successful run removes that marker. A failed run preserves it
and its private evidence for diagnosis or an explicit retry.

## Review

```bash
sudo /usr/local/sbin/edsys-reboot-acceptance status
sudo systemctl status edsys-reboot-acceptance.service --no-pager
```

Host-local success does not prove a controller path. Complete acceptance from
an approved controller by verifying key-only SSH to `9950x`, the expected Codex
version, and the required LAN/Tailnet application paths.
