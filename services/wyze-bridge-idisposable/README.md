# IDisposable Wyze Bridge for Back Patio

This service definition pins `IDisposable/docker-wyze-bridge` 4.5.0 and exposes
one Wyze V4 camera to Frigate through a loopback-only RTSP port on `9950x`.
The upstream bridge needs direct LAN P2P for native 2560x1440 video, so the
container has a dedicated macvlan address outside the DHCP pool. A small derived
image installs an in-container ingress guard before the bridge starts: the
macvlan interface accepts established traffic and the exact camera address, but
drops new connections from every other LAN peer. Host management and Frigate
traffic use a separate isolated Docker bridge and loopback-published ports.

## Runtime boundary

- Source compose: this directory.
- Private runtime root: `/home/jeremy/wyze-bridge-idisposable` on `9950x`.
- Credentials: four mode-0600 Docker-secret files under the private runtime
  `secrets/` directory. Never copy them into this repository.
- Persistent bridge state: private runtime `config/` directory.
- Recovery: current private Frigate/bridge/watchdog checkpoint under the AI
  Store recovery tree; raw paths and credentials remain outside Git.

The runtime `.env` must set the private runtime root, the dedicated macvlan IP,
and the Back Patio camera IP. Create the two external Docker networks before
starting the stack:

```bash
docker network create -d bridge --subnet=10.255.250.0/29 wyze-idisposable-bridge
docker network create -d macvlan \
  --subnet=192.168.50.0/24 --gateway=192.168.50.1 \
  --ip-range=192.168.50.249/32 -o parent=enp7s0 wyze-idisposable-lan
```

Validate the compose and build before deployment:

```bash
docker compose --env-file .env config --quiet
docker compose --env-file .env build --pull
```

## Acceptance

1. `docker inspect` reports `running`, `healthy`, and `unless-stopped`.
2. `ffprobe rtsp://127.0.0.1:18555/back_patio_cam` reports H.264 at 2560x1440.
3. Frigate reports `back_patio_wyze` near 5 FPS with zero skipped frames.
4. A different LAN peer cannot open the container's macvlan ports 5080, 1984,
   8554, or 8889; the camera remains reachable from the container.
5. A controlled container restart restores the stream without changing the
   Front Porch legacy bridge.

The five-minute `wyze-kvs-watchdog.timer` remains the shared Frigate image/FPS
check. Its recovery action restarts both scoped Wyze bridge containers and then
Frigate; private live units and runtime configuration are not stored here.
