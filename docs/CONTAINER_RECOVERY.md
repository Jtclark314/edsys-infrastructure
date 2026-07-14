# 9950x Docker Container Recovery

Status: deployable ordered recovery baseline for the 9950x Docker host.

The source manifest, recovery script, systemd templates, and detailed safety
notes live under `docker/container-recovery/`. The normalized Portainer, Plex,
Tautulli, and Open WebUI stack lives under `docker/9950x-core/`.

The architecture has four layers:

1. Docker live restore is disabled so a host shutdown cleanly stops containers
   before network namespaces and the external Docker data-root are unmounted.
2. A Docker systemd drop-in requires the data-root filesystem and starts the
   one-shot recovery service after every successful daemon start.
3. Existing Docker restart policies bring normal services back after a daemon
   or host restart; the controller skips healthy/running services and uses
   `docker compose start` only for pre-existing approved stopped services.
4. Compose health checks plus host HTTP gates prove readiness between tiers.

This favors deterministic host-reboot recovery over daemon-only zero-downtime
behavior. A Docker daemon restart can therefore interrupt containers, so treat
it as a reviewed maintenance action and rely on the ordered health gate before
declaring service restored. Dockerd has 120 seconds to stop all containers and
the systemd unit allows three minutes for the daemon to finish.

The audit timer is report-only. Automatic recovery can be suppressed with the
documented runtime maintenance flag. Build workloads should use a dedicated
`docker-container` Buildx builder so BuildKit does not execute inside the
production Docker daemon.

Runtime environment files, notification URLs, rollback snapshots, databases,
logs, and Docker state remain outside Git.
