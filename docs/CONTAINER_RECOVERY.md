# 9950x Docker Container Recovery

Status: deployable ordered recovery baseline for the 9950x Docker host.

The source manifest, recovery script, systemd templates, and detailed safety
notes live under `docker/container-recovery/`. The normalized Portainer, Plex,
Tautulli, and Open WebUI stack lives under `docker/9950x-core/`.

The architecture has four layers:

1. Docker live restore limits the impact of a daemon crash.
2. A Docker systemd drop-in requires the data-root filesystem and starts the
   one-shot recovery service after every successful daemon start.
3. The controller skips healthy/running live-restored services and uses
   `docker compose start` only for pre-existing approved stopped services.
4. Compose health checks plus host HTTP gates prove readiness between tiers.

The audit timer is report-only. Automatic recovery can be suppressed with the
documented runtime maintenance flag. Build workloads should use a dedicated
`docker-container` Buildx builder so BuildKit does not execute inside the
production Docker daemon.

Runtime environment files, notification URLs, rollback snapshots, databases,
logs, and Docker state remain outside Git.
