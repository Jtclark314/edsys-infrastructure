# Master Dashboard deployment

This stack uses the existing installed Homepage image, pinned by digest, for
the combined EdSys and Foothills launcher. It has no application records,
database, credentials, Docker socket or host-media access. The generated
navigation and UI source belong in
`/home/jeremy/code/homepage-config/master/`; follow that directory's README
for the source build, coverage check and config deployment.

## Access and boundaries

- LAN: `http://192.168.50.50:3022/`
- Private Tailnet: `http://100.87.137.47:3022/`
- Local health/readback: `http://127.0.0.1:3022/api/services`
- Config: `/srv/homepage-master/config`
- Shared icons, read-only: `/srv/homepage/icons`
- Container: `homepage-master`
- Network: Docker host networking, matching the existing Workhorse dashboard;
  no new subnet is allocated.

Homepage listens on host port 3022, using the established Workhorse networking
pattern. Its allowed-host list is restricted to the configured LAN, Tailnet,
loopback and 9950x names on that port. There is no direct Docker publication
bound to the dynamic Tailnet address, so boot recovery does not depend on
`tailscale0` being present. No WAN forwarding, public hostname, Cloudflare route,
Funnel, new authentication path or separate systemd proxy is added.

Homepage's inherited health check uses its configured port 3022 and
`/api/healthcheck`. Master entries are launchers only: generated config must
not copy Docker widgets or privileged monitoring configuration from the
individual dashboards. Normal per-service authentication remains at each
destination.

## Deploy only this stack

Build/check and install the reviewed source-generated config first using
`homepage-config/master/README.md`. Both bind-mount directories must exist;
Compose will not silently create missing host paths.

```bash
cd /srv/edsys/edsys-infrastructure
docker compose -f docker/homepage-master/compose.yaml config --quiet
docker compose -f docker/homepage-master/compose.yaml up -d --pull never
```

The image digest is already installed on the 9950x. Do not pull or substitute a
new Homepage version as part of a config deployment. `restart: unless-stopped`
provides ordinary Docker restart recovery. The source container-recovery
manifest adds this existing container to the operator-visibility tier; it is
not a Docker-socket consumer.

After the container exists and passes its own checks, back up the installed
`/etc/edsys-container-recovery/manifest.yaml` privately, confirm that its existing
content matches the source baseline, and install only the reviewed manifest
delta. Do not rerun the broad recovery installer or invoke recovery/audit to
test this service: those actions can affect unrelated containers or emit
notifications.

## Verification

```bash
docker inspect --format '{{.State.Health.Status}}' homepage-master
curl --fail --silent http://127.0.0.1:3022/api/services
curl --fail --silent http://192.168.50.50:3022/api/services
curl --fail --silent http://100.87.137.47:3022/api/services
```

Verify generated item coverage, browser rendering and links using the source
README. A config check or container start does not prove a full-host reboot;
record reboot acceptance separately when it is actually performed. No host or
Docker daemon restart is part of this deployment.

## Backup and narrow rollback

The durable backup is the reviewed Git source, its generation inputs and this
deployment directory. There is no independent app database or uploaded content
to protect. Before changes, retain private copies of changed live config and
the recovery manifest. Logs under live config remain runtime artifacts outside
Git.

For a config rollback, restore only the saved Master config files and restart
`homepage-master`. To retire this new dashboard, stop only its container and
remove its single recovery-manifest entry and navigation links in the same
reviewed retirement change. Do not remove shared icons or another dashboard's
files. Restoring the
previous reviewed image is a separate explicit image change, not an automatic
config rollback.
