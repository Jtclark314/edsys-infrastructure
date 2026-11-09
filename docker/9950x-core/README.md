# 9950x Core Services

This Compose project normalizes four previously standalone containers so the
ordered EdSys recovery controller can manage them declaratively: Portainer,
Plex, Tautulli, and Open WebUI.

Images are pinned to qualified digests. Plex was updated and accepted on
2026-11-01 at public 1.43.5.11079-b3b13e506.
The other core images retain their earlier recovery baseline.
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

## Plex maintenance timezone

Plex uses `TZ=America/New_York` from this Compose definition. Its persistent
Scheduled Tasks settings retain start hour `2` and end hour `5`, so maintenance
runs from 2–5 a.m. Eastern and follows daylight saving changes automatically.
Before the 2026-11-07 correction, the container used UTC and this same window
ran from 9 p.m. to midnight Eastern Standard Time. The evening activity was
progressing loudness analysis after library scans had finished.

For a timezone change, preserve the current Compose/environment definitions,
preferences and consistent database backups privately, confirm playback is
idle, validate Compose, and recreate only Plex:

```bash
docker compose -f docker/9950x-core/compose.yaml config --quiet
docker compose -f docker/9950x-core/compose.yaml up -d --no-deps --pull never --no-build --timeout 60 plex
docker exec plex date '+%Y-%m-%d %H:%M:%S %Z %z'
```

Verify server health, authenticated library/file identities, retained scheduled
hours and inactive maintenance outside the window. Existing hourly scans and
processing triggered by new media can still show activity outside maintenance.
Recovery restores the previous Plex timezone entry and recreates only Plex
with the same pinned image and persistent mounts.

## Plex Web cookie isolation

Use `http://plex.edsys.local:32400/web` for browser launchers. The private DNS
record in `plex-web-dns.conf` is installed on `edrouter-node0` as
`/etc/dnsmasq.d/05-edsys-plex-web.conf`. Both Pi-hole nodes forward this local
zone to the router. Other applications keep their existing addresses.

Cookies are shared across ports at an IP address. Accumulated analytics and
application cookies at `192.168.50.50` caused a reproducible Plex HTTP 400;
the same request without cookies returned 200. The dedicated hostname keeps
those IP-scoped cookies out of Plex requests without changing cookie settings
for other apps. Do not use the Plex hostname for other applications or set
parent-domain cookies for it. This changes browser navigation only; Plex's
container, authentication policy, libraries and native-client discovery remain
unchanged. Keep numeric-IP monitoring endpoints independent of browser links.

DNS deployment requires a private copy of the current router dnsmasq config,
an absent/conflict check for the destination, `dnsmasq --test`, and one
dnsmasq-only restart. Verify the new record through the router and both
Pi-holes, existing local/public lookups, then the Plex web and identity pages.
Do not restart the router or Plex. Update the EdSys and generated Master
dashboard links together and verify Nimo's saved launch paths.

Plex browser settings belong to an origin. For an existing signed-in browser,
transfer settings only within that browser between the exact old and new Plex
origins, with strict sender/origin checks. Never place authentication values in
URLs, console output, files, Git or RAG. Other devices may need one normal
Plex sign-in at the new address. Off-LAN DNS/client acceptance is separate.

Recovery: restore only the changed navigation fields. Remove the unchanged
`05-edsys-plex-web.conf` file, validate dnsmasq and restart only dnsmasq. Retain
newer unrelated DNS edits and browser settings. The numeric-IP web address is
a recovery route but still shares cookies with the other applications.

Protocol reference: [RFC 6265, cookie isolation](https://www.rfc-editor.org/rfc/rfc6265#section-8.5).

## Plex security maintenance — 2026-11-01

The accepted public release is 1.43.5.11079-b3b13e506-ls330. Its immutable
LinuxServer amd64 manifest matches the independent LSCR and Docker Hub results;
the candidate also reported the expected binary version in an isolated
networkless container. The previous exact image is retained in Docker and as
a hashed archive. The public
release includes compromised eMudhra CA-root distrust and library-scoping,
proxy and request-locality security fixes.

The owner approved a brief interruption of the paused LAN session. Only Plex
was recreated, with an independent rollback watchdog armed before stopping it.
A full stopped-state configuration archive, both database/sidecar sets, old
image, source and environment were retained privately with fresh hashes. Both
databases passed the old bundled SQLite's `PRAGMA quick_check` on disposable
writable copies. An initial verifier ownership rejection recovered old Plex
before promotion; the corrected disposable-directory ownership was proved on
both actual cold databases before the successful fresh transaction.

Fresh authenticated checks confirmed 1.43.5.11079, healthy state, retained
server/account identity, all four library totals, preferences, environment,
ports, restart/network settings, media filesystem identities and GPU reservation.
An authenticated 64 KiB partial-media read matched the mounted file. A bounded
isolated candidate control processed 60 synthetic H.264 frames with CUDA
hardware decoding/scaling and NVENC encoding using read-only cached vendor
codecs. No physical client playback or TV action was issued; picture/sound,
HDR tone mapping and the owner's individual client resume were not tested.
All 113 unrelated containers retained their identity, image, state and start
time. Fresh acceptance checks passed and the rollback watchdog was disarmed.

Rollback must pair the old image with its matching stopped-state configuration
and databases, preserve newer state, and use a fresh guarded transaction after
acceptance. The old live image is the normal recovery path. The retained image
archive is hashed; importing it into an empty daemon and reconstructing its
distribution digest name were not tested. Raw recovery material and private
session/authentication state remain outside Git/RAG.

Reference: [vendor public release feed](https://plex.tv/api/downloads/5.json).

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
