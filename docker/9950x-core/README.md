# 9950x Core Services

This Compose project normalizes four previously standalone containers so the
ordered EdSys recovery controller can manage them declaratively: Portainer,
Plex, Tautulli, and Open WebUI.

Images are pinned to the digests verified during the 2026-07-12 recovery work.
Runtime environment values, including Open WebUI service credentials, remain
under `/etc/edsys-container-recovery/env/` and are never committed.

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
curl -fsS http://127.0.0.1:32400/identity
curl -fsS http://127.0.0.1:8181/status
curl -fsS http://127.0.0.1:3000/health
```

Do not run `down -v`; the external Portainer volume and application data are
recovery-sensitive runtime state.
