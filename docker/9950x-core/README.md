# 9950x Core Services

This Compose project normalizes four previously standalone containers so the
ordered EdSys recovery controller can manage them declaratively: Portainer,
Plex, Tautulli, and Open WebUI.

Images are pinned to qualified digests. Plex was advanced independently to
the mature public security-fixed 1.43.4.10903 release on 2026-10-20; the other
core images retain their earlier recovery baseline.
Runtime environment values, including Open WebUI service credentials, remain
under `/etc/edsys-container-recovery/env/` and are never committed.
Open WebUI remains defined with loopback/LAN publication for reversible use,
but is cold-stopped for the owner-approved 14-30 day observation begun on
2026-08-22. Its prior exact Tailnet proxy instance is disabled during that
window; the retained proxy framework is documented in
`../../scripts/network/README.md`.

Plex receives the host NVIDIA GPU through a Compose device reservation. The
host therefore requires a working NVIDIA driver and NVIDIA Container Toolkit.
Plex hardware acceleration and hardware-accelerated video encoding remain
application settings in the persistent `/srv/plex-config` runtime state.

## Plex security maintenance — 2026-10-20

Plex 1.43.1.10611 was below the vendor's 1.43.3 security-fix threshold.
The scoped upgrade selected public 1.43.4.10903-e5521bd8c-ls328 rather than
the newer release promoted to public on the maintenance date. Its exact
LinuxServer amd64 digest is pinned in this Compose file. The official Docker
Hub mirror and LSCR resolved the same digest; the mirror recovered a registry
retry/rate-limit failure without substituting an unofficial image.

Authenticated idle gates included playing/paused sessions, clients,
transcodes and activities. A full stopped-state configuration checkpoint,
old image archive and prior Compose/environment definitions were preserved
privately. Both library databases passed the old bundled Plex SQLite's
`PRAGMA quick_check` on disposable writable copies of the complete cold
database/sidecar sets. An initial read-only isolated verifier returned an
unable-to-open error; the old container was restored before migration, and
verification passed on disposable writable copies before the successful retry.
Do not use immutable
or lock-bypassing SQLite options to work around live/WAL access problems.

Only Plex was recreated, using `up -d --no-deps --pull never --no-build plex`.
Live identity and authenticated library reads report 1.43.4.10903; health,
server/account identity, all four library counts, mounts, ports, environment,
restart policy, GPU access and hardware-transcoding preferences were retained.
Unrelated container identities/start times and Tautulli were unchanged. No
client playback or TV action was issued. New physical picture/sound and a
hardware-transcoding playback test remain unperformed to preserve owner use.

Rollback requires the retained old image **and matching stopped-state
configuration**, with Plex stopped and displaced newer state preserved.
An image-only downgrade after a database migration is not the recovery plan.
Private recovery material remains outside Git/RAG. The stopped-state recovery
set is included in a fresh full encrypted local backup, with an isolated
verified file restore. Existing encrypted backups were not pruned. The new
offsite copy awaits the existing mirror schedule; prior offsite backups remain.

Vendor references: [security notice](https://forums.plex.tv/t/important-security-update-for-plex-media-server-v1-43-2-and-earlier/942319),
[public 1.43.4 release](https://forums.plex.tv/t/plex-media-server/30447/717).

## Storage and backup

- Portainer: external Docker volume `portainer_data`.
- Plex: `/srv/plex-config` plus media mounts under `/mnt/media`.
- Tautulli: `/srv/tautulli/config`.
- Open WebUI: `/srv/ssd1/docker/volumes/openwebui/_data`.

These paths retain the existing runtime state. Backups continue through the
existing EdSys backup definitions; this stack does not copy or move data.

## Validation

```bash
docker compose -f docker/9950x-core/compose.yaml config --quiet
docker compose -f docker/9950x-core/compose.yaml up -d --pull never --no-build
docker compose -f docker/9950x-core/compose.yaml ps
docker inspect --format '{{json .HostConfig.DeviceRequests}}' plex
docker exec plex nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
curl -fsS http://127.0.0.1:32400/identity
curl -fsS http://127.0.0.1:8181/status
```

Do not expect the Open WebUI health path during the current observation. If the
owner later approves reactivation, restore the retained service intentionally
and validate `http://127.0.0.1:3000/health` before restoring any monitoring or
Tailnet proxy state.

For an end-to-end Plex check, force a temporary video conversion from a client
and verify that the Plex Dashboard displays `(hw)` for the video transcode.
Direct Play does not invoke the transcoder, and audio-only conversion remains a
CPU workload.

Do not run `down -v`; the external Portainer volume and application data are
recovery-sensitive runtime state.
