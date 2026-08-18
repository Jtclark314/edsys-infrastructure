# 9950x Docker Container Recovery

Status: deployable ordered recovery baseline for the 9950x Docker host.

The source manifest, recovery script, systemd templates, and detailed safety
notes live under `docker/container-recovery/`. The normalized Portainer, Plex,
Tautulli, and Open WebUI stack lives under `docker/9950x-core/`.

The architecture has four layers:

1. Docker live restore preserves ordinary workloads across daemon-only
   maintenance and unexpected daemon loss.
2. A Docker systemd drop-in requires the data-root filesystem and starts the
   one-shot recovery service after every successful daemon start.
3. The controller detects approved Docker-socket consumers that retained the
   preceding socket inode and restarts only those consumers before tier checks.
   It skips other healthy/running services and uses `docker compose start` only
   for pre-existing approved stopped services.
4. Compose health checks plus 35 host HTTP gates prove readiness between tiers.

This preserves daemon-only continuity without leaving Docker-dependent
dashboards and telemetry attached to an obsolete Unix-socket inode. A Docker
daemon restart remains a reviewed maintenance action; rely on the ordered
health gate before declaring the service estate restored. Dockerd has 120
seconds to stop containers when required, while systemd gives the daemon three
minutes to finish.

The audit timer is report-only. Automatic recovery can be suppressed with the
documented runtime maintenance flag. Build workloads should use a dedicated
`docker-container` Buildx builder so BuildKit does not execute inside the
production Docker daemon.

Runtime environment files, notification URLs, rollback snapshots, databases,
logs, and Docker state remain outside Git.
