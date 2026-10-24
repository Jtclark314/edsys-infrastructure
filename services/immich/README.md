# Immich on 9950x

Status: the existing **2.5.6** instance moved to 9950x on October 24, 2026.
Production storage/API checks, local encrypted originals restore, exact offsite
recovery and an actual isolated PostgreSQL restore/query passed. Current
acceptance belongs in `EdSys-Master/docs/IMMICH.md`. The retained family-services
application is stopped, with all four container restart policies disabled.

## Scope and storage

The owner approved moving the existing Immich instance to 9950x while preserving
version **2.5.6**, existing accounts/library, rollback and iCloud. No iCloud
collection, account change, photo deletion or application upgrade is part of
this migration. The isolated qualification copy uses disposable authentication
and a synthetic image; its database is restored from the original checkpoint
before production use.

- Complete media/upload tree: `/mnt/immich-library/upload`.
- Empty legacy library mapping: `/mnt/immich-library/legacy-library`.
- Media filesystem: ext4 identity independently verified against
  `IMMICH_MEDIA_UUID` in the root-private runtime `storage.env`.
- Internal state/configuration/recovery: `/mnt/ai-store/immich`.
- Internal filesystem: ext4 identity independently verified against
  `IMMICH_NVME_UUID` in that same private file.
- Postgres, Redis, server data and model cache remain on internal NVMe.

Private source Compose/environment, account data, image exports, originals,
database dumps, hashes, access tokens and logs never belong in this repository.
`render_compose.py` consumes a protected resolved source configuration and writes
the protected runtime Compose file at
`/mnt/ai-store/immich/config/compose.yaml`, mode 0600. It preserves application
environment values and pins all four existing image IDs with pull disabled.
The server explicitly sets `IMMICH_MEDIA_LOCATION=/usr/src/app/upload`, so
new originals use the SSD upload bind. `/data` remains only a compatibility
mapping on internal storage. A disposable candidate upload's database path
and physical host file were checked against this root and matched exactly;
the final service repeated that check successfully on the SSD.
The rendered JSON is valid Compose YAML; never print it because it includes
the original credentials. Do not run a Compose update/pull during migration.

## Service and access

CPU-only limits: server four CPU/4 GiB; Postgres two CPU/2 GiB; ML two CPU/3 GiB;
Redis half a CPU/256 MiB. This uses the existing CPU models and cache, without
claiming or configuring GPU acceleration.

Production binds only `127.0.0.1:2283` and `192.168.50.50:2283`. There is no
new public, Funnel or Tailnet listener. The final private Docker subnet is
`10.254.228.16/28`; the isolated candidate uses internal `10.254.228.0/28` with
no host publication. These scoped subnets were checked against current Docker
networks and host routes because the host's automatic pools are exhausted.
Candidate probes run from the host against its private bridge address.

Install `guard.py` root-owned under `/opt/edsys-immich/` and `immich.service`
under `/etc/systemd/system/`. Use `storage.env.example` as the template and put
the independently verified expected UUIDs into root-owned mode-0600
`/mnt/ai-store/immich/config/storage.env`; the tracked unit contains no live
filesystem identities. The unit requires and binds to both mount units,
checks exact writable local filesystem UUIDs, canonical data directories and
the media marker before each start. It also requires an existing PostgreSQL 14
cluster (`PG_VERSION` and its control file), refusing an empty database path.
The unit uses foreground Compose. Every
container has `restart: no` and bind mounts disable automatic host-path
creation. Docker cannot independently restart this stack onto missing media.
`PartOf=docker.service` and the unit's Docker Wants link propagate normal
daemon stop/restart/start through the guard. Never set container restart policy
back to `unless-stopped`.

```bash
sudo systemctl enable --now immich.service
sudo systemctl status immich.service
sudo systemctl restart immich.service
```

Do not start the production Compose project directly. The systemd guard is the
production start path. The protected service log is
`/mnt/ai-store/immich/logs/service.log`.

The old exact LAN endpoint `192.168.50.78:2283` is retained with a temporary
socket proxy on family-services. Install the source `legacy-proxy.socket` and
`.service` as `edsys-immich-legacy.socket` and `.service` only after stopping
the old Immich containers. The proxy forwards to `192.168.50.50:2283` and
preserves existing mobile/server URLs. Until clients move to the new canonical
address, they depend on the old VM and this proxy. Other family-services apps
remain on VM202.

All four retained source containers have `restart: no`. No source system/user
unit, cron/boot command, Portainer stack label or active Swarm controller was
found for Immich. Do not run the old Compose project while the new instance is
active; its original configuration remains private for deliberate rollback.

## Verification and recovery

Focused source checks:

```bash
python3 -m unittest discover -s services/immich -p 'test_guard.py' -v
python3 -m unittest discover -s services/immich -p 'test_render.py' -v
sudo python3 services/immich/test_systemd_guard.py
```

The last command creates only uniquely owned disposable mock units, exercises
real systemd start/stop/restart and missing/wrong-storage refusal, then removes
its units and state. It does not restart Docker or any production service.

Acceptance must preserve exact database asset references, original-file hashes,
accounts and source image identities; verify authenticated original downloads,
previews, a disposable upload and CPU ML processing on the isolated copy;
repeat storage/application probes after final transfer; and verify missing/wrong
storage refuses startup. A host reboot or physical USB unplug is a separate
acceptance claim. Phone acceptance remains owner verification.

Backup integration is owned by `scripts/backup/edsys-immich-local.py` and the
existing encrypted Restic framework; follow its README. Local mode is activated
and its encrypted restore verified all 433 referenced originals. The exact
snapshot was found offsite; a direct offsite DB/video restore matched, and an
isolated same-image PostgreSQL restore returned all 433 matching reference
tuples. The original schedule/retention and old recovery stages are retained. Direct
original-file backup replaces full-library archive staging. The eventual iCloud
library still requires capacity planning for the independent backup repository
and an offsite recovery check.

Rollback stops the new guarded service/proxy before restarting the retained old
source containers with their captured image/configuration identities. Preserve
all post-cutover photo/account changes before any database rollback. Old source
media/database/configuration and encrypted checkpoints are retained; do not run
both production instances or restore an old database over newer uploads.
