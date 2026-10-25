# Immich on 9950x

Production version/backend acceptance: **3.3.5 / CUDA ML / optimized models v2; final acceptance October 25, 2026 Eastern**.
The existing 2.5.6 instance moved to 9950x on October 24, 2026 Eastern;
the subsequent authorized iCloud copy and pre-upgrade indexing were verified.
Current acceptance belongs in `EdSys-Master/docs/IMMICH.md`. The retained
family-services application is stopped with all four container restart policies
disabled. iCloud content remains unchanged; no import or processing schedule
was added.

## Scope and storage

- Originals and new uploads: `/mnt/immich-library/upload`, on the dedicated
  PNY 2 TB SSD using its retained ext4 filesystem.
- Empty legacy library mapping: `/mnt/immich-library/legacy-library`.
- PostgreSQL, Redis, server data, configuration and model cache:
  internal NVMe under `/mnt/ai-store/immich`.
- Expected filesystem identities: `IMMICH_MEDIA_UUID` and `IMMICH_NVME_UUID`
  in the root-private runtime `storage.env`; never copy their live values here.

The SSD's former 1,365 media files / 621,141,235,058 logical bytes were relocated
to the internal 2 TB HDD and Node0 NFS branch with complete SHA-256 and native
metadata verification. Only the verified legacy paths and 118 emptied
directories were removed after application and recovery acceptance. At that
stage, before the later import, about 1.86 TB / 1.69 TiB was available. Plex
and the five paused media automation services were restored. The WD 5 TB drive
had four pending sectors at the migration check; its mergerfs branch remains
`=NC`, preserving existing access while preventing new allocation. Replacement
or repair is separate work. See `EdSys-Master/docs/MEDIA_STORAGE.md`.

The copy reconciliation accounted for 11,515 unique source media files:
7,432 stills, 250 visible videos and 3,833 hidden Live Photo clips. All 433 prior
assets remained unchanged. Original-byte readback and all 3,833 expected Live
Photo relationships passed. The source's 7,691 logical records resolve to
7,682 unique masters: nine aliases across six shared-master groups were proved,
rather than treated as missing downloads. All 7,254 managed XMPs passed; 428
prior-duplicate XMP exceptions and separate alias evidence were preserved in
the private encrypted recovery scope without overwriting prior metadata.

All 18 queues and per-asset indexing prerequisites passed before upgrade, and
temporary CPU tuning was restored. Exact 2.5.6 video-policy checks accounted
for all 250 visible videos: 85 nonempty encoded files on the SSD and 165 valid
no-transcode/no-remux originals. GPU ML does not change video transcoding policy.

Private source Compose/environment, account data, originals, database dumps,
inventories, hashes, authentication material and logs never belong in Git/RAG.
`render_compose.py` consumes a protected resolved source and writes protected
Compose JSON/YAML at `/mnt/ai-store/immich/config/compose.yaml`, mode 0600.
`IMMICH_MEDIA_LOCATION=/usr/src/app/upload` keeps new originals on the SSD;
`/data` is an internal-storage compatibility mapping. Actual upload path and
byte checks passed. Do not print rendered configuration.

## Explicit version and ML profiles

The reviewed profiles are `profiles/v3.3.5-cpu.json` and
`profiles/v3.3.5-cuda.json`. They pin server and matching ML image manifests;
PostgreSQL and Redis retain their qualified image identities. Supply the
selected file through `render_compose.py --profile`. A source using a reviewed
3.3.5 tag or digest refuses an omitted profile, preventing accidental rendering
of legacy 2.5.6 images. This automatic detection does not cover unreviewed
future image versions. Legacy source artifacts retain their
deliberate pre-upgrade/rollback behavior.

Qualify a separate CPU candidate first using isolated matching database and
media paths. Accept production CPU behavior before changing only the matching
ML backend to CUDA. Do not point a disposable candidate at the production PG
directory or start both clusters on the same data. Runtime acceptance:

- CPU 3.3.5 storage, migration, API and original/index parity:
  **verified in production before CUDA cutover, including semantic search**.
- CUDA provider/model behavior and bounded coexistence/release:
  **verified with actual GPU-backed ML and a disposable upload/index/delete test**;
  **verified GPU attribution and idle model release; Plex/Frigate remained healthy; minimum observed free VRAM 1,002 MiB (observed headroom, not a guarantee)**.
- Final source and indexing parity:
  **verified all 11,515 asset/reference IDs, 3,833 Live links, accounts/collections, all enabled indexing prerequisites and 19 idle queues in two snapshots 30 seconds apart**.

The profiles use model revision `v2` while retaining `ViT-B-32__openai`,
`buffalo_l` and `PP-OCRv5_mobile`. ML has one worker, one request thread,
batch size one for face/OCR work, model TTL 60 seconds and no preload settings.
Only the CUDA ML service receives GPU device 0. Server video policy is retained.
The normal caps remain server four CPU/4 GiB, PostgreSQL two CPU/2 GiB,
ML two CPU/3 GiB and Redis half a CPU/256 MiB.

At the owner's request, the three new v3 scheduled integrity scans—checksum,
missing-file and untracked-file scans—are explicitly disabled. Existing nightly
processing and backup schedules are unchanged. The v3 migration normalizes the
Opus codec identifier from `libopus` to `opus` without changing its meaning.
The previous `accelDecode=false` setting was explicitly restored; hardware video
acceleration remains disabled. CUDA access remains confined to ML.

## Guarded service and access

Production binds `127.0.0.1:2283` and `192.168.50.50:2283`. The final private
Docker subnet is `10.254.228.16/28`; the isolated candidate uses internal
`10.254.228.0/28` without a host port. No additional listener is introduced.

Install `guard.py` root-owned under `/opt/edsys-immich/` and `immich.service`
under `/etc/systemd/system/`. Populate root-owned mode-0600
`/mnt/ai-store/immich/config/storage.env` from `storage.env.example` with
independently verified identities. The unit requires and binds to both mount
units, verifies exact writable filesystems/canonical directories/media marker,
and refuses an empty PostgreSQL 14 directory. Foreground Compose uses
`restart: no` for every container and disables automatic host-path creation.
Docker stop/restart propagates through the systemd guard.

```bash
sudo systemctl status immich.service
sudo systemctl restart immich.service
```

The guarded systemd unit is the production start path. Do not start production
Compose directly or restore `unless-stopped` restart policies. Its protected
log is `/mnt/ai-store/immich/logs/service.log`.

The old `192.168.50.78:2283` endpoint forwards to `.50:2283` through the temporary
`edsys-immich-legacy` socket proxy on family-services. Unchanged clients still
depend on that VM. Original source containers remain stopped with restart
disabled, and unrelated family-services applications stay on their host.

## Verification and recovery

Focused source checks, from the infrastructure repository:

```bash
python3 -m unittest discover -s services/immich -p 'test_guard.py' -v
python3 -m unittest discover -s services/immich -p 'test_render.py' -v
python3 -m unittest discover -s services/immich -p 'test_runtime_profiles.py' -v
```

The optional `sudo python3 services/immich/test_systemd_guard.py` exercises
uniquely owned disposable mock units and removes them. It does not restart
Docker or production. Physical host reboot/USB unplug and owner phone use
remain separate acceptance claims.

Backup integration belongs to `scripts/backup/edsys-immich-local.py` and the
existing encrypted Restic framework. Referenced originals go directly to
encrypted snapshots; consistent database/configuration/image/reference
checkpoints are staged. All 18 existing local image paths, existing schedules,
retention and earlier recovery stages remain protected. Scoped Immich staging
does not establish health of the unrelated failing broad collector.

An initial local recovery restored all 11,515 originals and 7,254 managed XMPs.
Final pre-upgrade and post-upgrade recovery reused that independent payload only
after authenticating unchanged old/new Restic media-content references and
freshly hashing all 18,769 files. Both final checkpoints passed 60 selected
direct offsite readbacks of originals, XMP and recovery/source metadata.
The final 3.3.5 recovery also restored six recovery members directly from
offsite and actually restored its database into isolated PostgreSQL 14,
verifying exact assets, 3,833 Live links, accounts, 99 people groups and the
three disabled integrity schedules. The test database is stopped and retained.
This does not establish a fresh roughly 50 GB plaintext offsite media restore.
Exact snapshot identities and raw receipts remain private. Existing backup
schedules/retention and all earlier recovery stages remain protected; this
scoped acceptance does not establish health of the unrelated broad collector.

Rollback from 3.3.5 must stop the guarded stack and use the matching retained
pre-upgrade 2.5.6 PostgreSQL directory plus old Compose/images. Never start
2.5.6 against the database after 3.3.5 migrations. The older family-services
checkpoint predates the import and is not its rollback database. Protect new
uploads/account changes before any rollback; retain original media, matched
cold checkpoints and encrypted backups. Full running-application disaster
recovery with regenerated derivatives was not exercised by file/dump recovery.
