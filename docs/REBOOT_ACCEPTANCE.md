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

Ubuntu's supported OpenSSH socket-activation posture is accepted when
`ssh.socket` is enabled/active and `ssh.service` is active; the service itself
does not need a duplicate enablement link. EdSys firewall guards that are
required by early sockets run before `network-pre.target`, while the
Tailnet-only SMB socket starts from `multi-user.target` after Tailscale. This
avoids creating a `basic.target`/`sockets.target` ordering cycle that can leave
remote listeners absent on an otherwise successful boot.

Docker live restore is deliberately disabled for full-host reboot reliability.
Dockerd stops containers before systemd removes Docker network namespaces and
unmounts `/mnt/data-500g`; restart policies plus the ordered recovery service
then restore the same container identities and gate application health. The
daemon receives a 120-second container shutdown budget within a three-minute
systemd stop window.

Private run records live under `/var/lib/edsys-reboot-acceptance/` and must not
enter Git or RAG.

Identity sets are normalized with the C locale so interactive arming and
system-service verification cannot disagree solely because of collation.

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
