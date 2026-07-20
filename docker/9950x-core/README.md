# 9950x Core Services

This Compose project normalizes four previously standalone containers so the
ordered EdSys recovery controller can manage them declaratively: Portainer,
Plex, Tautulli, and Open WebUI.

Images are pinned to the digests verified during the 2026-07-12 recovery work.
Runtime environment values, including Open WebUI service credentials, remain
under `/etc/edsys-container-recovery/env/` and are never committed.
Open WebUI publishes directly only on loopback/LAN; its exact Tailnet listener
is the FreeBind proxy documented in `../../scripts/network/README.md`.

Plex receives the host NVIDIA GPU through a Compose device reservation. The
host therefore requires a working NVIDIA driver and NVIDIA Container Toolkit.
Plex hardware acceleration and hardware-accelerated video encoding remain
application settings in the persistent `/srv/plex-config` runtime state.

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
curl -fsS http://127.0.0.1:3000/health
```

For an end-to-end Plex check, force a temporary video conversion from a client
and verify that the Plex Dashboard displays `(hw)` for the video transcode.
Direct Play does not invoke the transcoder, and audio-only conversion remains a
CPU workload.

Do not run `down -v`; the external Portainer volume and application data are
recovery-sensitive runtime state.
