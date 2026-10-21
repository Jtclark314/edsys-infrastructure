# EdSys Google Drive Offsite Backup Tooling

Status: active baseline.

This directory contains the host-side tooling for EdSys offsite backups.

## Design

- Orchestrator: `9950x`
- Snapshot engine: `restic`
- Cloud transport: `rclone`
- Offsite target: Google Drive
- Local repository: `/srv/edsys-backup/restic-repo/edsys-critical`
- Offsite mirror: `EdSys Backups/restic/edsys-critical-v3`
- Local framework: `/srv/edsys-backup`
- Private config: `/etc/edsys-backup`

The scripts are designed to protect critical service state first. They exclude replaceable bulk media by default.

## Network recovery exports — deployed 2026-10-20

`edsys-network-recovery.py` adds six independent recovery roles to the existing
collector: router guest, its hypervisor/VM network mapping, primary and secondary
Pi-hole/Unbound, UniFi Network, and Aruba. Source capture and full archive hash
verification were qualified at deployment. The aggregate intentionally remains
degraded because Aruba's read-only exports omit hidden authentication/key
material. Qualification covers source exports and archive integrity; fenced
application restoration, future-run freshness and offsite recovery require their
own evidence.
The root-owned private policy is `/etc/edsys-backup/network-recovery.json` (0600).
The sanitized `network-recovery.example.json` has approval disabled for every
source; replace and review all placeholders before enabling a source. Native
UniFi/Aruba methods require separate explicit approval and an exact exporter hash.
Do not run the full installer/backup to qualify this addition.

Coverage and consistency:

- Router: full `/etc` configuration (Netplan/renderer/resolver, nftables includes,
  dnsmasq, sysctls, chrony, units and drop-ins), custom `/usr/local` service code,
  DHCP leases, installed package versions and timestamped active firewall,
  interface/route/policy/service references. Those live references are evidence,
  not commands to replay. Pair this with the exact VM config/QEMU command and
  hypervisor bridge/NIC/MAC mapping; guest interface names alone are insufficient.
- Each Pi-hole: full configuration, Unbound DNSSEC/root-key and service references,
  Core/Web source revisions/trees, deployed `/opt/pihole` helpers, CLI,
  Unbound prestart/helper code and package-provided service units, FTL/Unbound
  binaries and package versions. Protect `/var/lib/unbound` and follow actual
  configuration includes for any root-key/root-hints path outside selected roots.
  Include enabled custom jobs and their service-user SSH recovery dependencies.
  The deployed scope also protects `/usr/local` and the private service-user
  `.ssh` directory: the primary's enabled Gravity Sync timer needs its separate
  executable and identity, alongside the already captured `/etc/gravity-sync`.
  Qualify these paths and their permissions before approving a policy; keep all
  identity material in private exports and encrypted recovery, never Git/RAG.
  `gravity.db` and an existing `macvendor.db` use read-only SQLite online backups
  with restored-destination integrity checks. The raw DB/WAL/SHM files are not
  tarred. The known-corrupt `pihole-FTL.db*`, forceDisk `pihole-tmp.db*`,
  and explicitly configured query-history paths/sidecars are never opened or
  included; follow actual FTL path overrides in the private policy. Teleporter is not used. Only reviewed per-profile volatile paths and
  globs are excluded; legitimate code named `core`, `log`, `cache` or `tmp` is not
  silently filtered.
- UniFi: exact controller/Mongo container image identities, mount/configuration
  references and complete non-Mongo configuration/keystore/Compose/env/init code.
  **Live Mongo data roots must be explicitly excluded.** A root-owned,
  hash-pinned native exporter produces a fresh native Network backup. Its actual
  native-created timestamp is checked separately from capture time (26 hours
  maximum); copying an older monthly `.unf` does not make it fresh. When
  `native_export.location` is `hub`, credentials stay on 9950x: the native API
  exporter runs locally and the reviewed config-only archive streams over pinned
  SSH. Private configuration/file-hash and container identity checkpoints bracket
  the entire native export plus config capture, including container IDs/start
  times and actual Mongo mount exclusions. Native-backup directories are excluded
  from config-only selection because the new native artifact is preserved
  separately. Verified member streams are merged without filesystem extraction. This
  does not infer statistics/database point-in-time recovery from an opaque file.
- Aruba: ordinary authenticated read-only running/startup export, all saved
  configuration-slot and boot/image/version references, and complete prompt/pager
  handling. Never use `write memory`, change paging/configuration, or enable
  `include-credentials`. Hidden authentication/key material is a separate recovery
  limit and must not silently become a complete export claim. Useful topology
  exports with hidden credentials unavailable are published only through a
  distinct `LIMITED.json` pointer/status; any complete `CURRENT.json` and its
  previous generation stay protected. The aggregate remains degraded. A later
  complete capture becomes current without an older limited pointer masking it.

All SSH uses explicit existing keys and `StrictHostKeyChecking=yes`; host keys
are not learned or changed. The capture runs at nice15/IO-priority7, within the
existing collector's 15-minute bound. Each source has an independent lock and
atomic, manifest-verified last-good pointer under private
`staging/network-recovery/sources/`. Files, source metadata, directory permissions,
bounded extended attributes (including ACL/capability metadata when supported)
and symlink definitions are preserved. Source path components use reversible
byte-wise percent encoding in tar member names (including literal percent and
backslash); RECOVERY retains exact original paths. Do not decode/extract them
blindly on Windows. Links are **metadata only**, never followed
or materialized by the verifier. Selected file/config changes refuse publication;
excluded log/tmp churn does not. Network counter changes are timestamped reads,
while exact software/container configuration changes block publication.

Received archive/member hashes are verified without extraction. Required SQLite
and native-backup members cannot be omitted. Streams, metadata, file counts,
subprocess output, deadline and free-space reserve are bounded. Completed stages
keep current/previous owned copies per complete/limited pointer; original
sources and Restic retention are unchanged. Failed attempts preserve last-good data, report explicit failed/stale
freshness and write `FAILED` to the existing collection manifest. Native backup
creation time remains part of freshness after collection; status also checks the
published pointer/summary/archive exists. Every required source must be fresh and complete for the aggregate to be healthy; one working
router export does not imply protected UniFi or Aruba. The existing backup JSON
adds a separate `network_recovery` result even when the overall encrypted snapshot
succeeds. A dry-run never collects. Mandatory `.incoming` exclusion is passed
both in the template and directly to Restic, independent of custom exclusions.
The separate ephemeral native-work root is also excluded directly and in the
template, including when a parent directory is selected; completed archives stay
under the existing network stage and retain the original inclusion semantics.

Retain separately qualified, matching UniFi controller/Mongo image payloads under
root0700 `/var/lib/edsys-backup/network-software`. That explicit include protects
completed bundles through the existing daily encrypted backup; `.incoming` is
excluded in both the template and the direct Restic arguments. This is retained
recovery software, with no additional recurring image capture. The private policy
pins the exact running image identities; a future image/version change requires
a newly qualified matching payload bundle and policy refresh before capture can
be accepted. A saved image or archive hash alone does not establish a successful
fenced controller restore or an offsite recovery copy.

Native exporter protocol (only after exact method review):

1. The executable and all ancestors are root-owned and not group/world writable;
   the private policy pins its SHA-256. It is called only with
   `--output-directory <new-private-directory>`, with no credential arguments.
   Native exporters install only under root-owned
   `/usr/local/libexec/edsys-network-recovery/`. All native work (local Aruba,
   hub UniFi or native worker mode) uses fixed root0700
   `/var/lib/edsys-backup/network-native-work`, with strict root-owned,
   nonwritable ancestry and a free-space reserve. `/srv` ownership and the strict
   ancestry fence remain unchanged; `/tmp` is not used for native work. Only
   this attempt's owned temporary directory is cleaned. It reads its own
   protected credential file; normal Google/desktop
   sessions are not substituted. The framework bounds/kills only its process
   group on failure/deadline and never prints native stderr.
2. It writes regular bounded artifacts beneath that directory and prints one
   JSON object. Common fields are `native_created_utc` and `artifacts`, mapping
   safe labels to absolute paths. UniFi requires `method: native-unifi-backup`,
   `settings_complete: true`, and the `network-backup` artifact. Aruba requires
   `method: aruba-read-only`, `read_only`, `prompt_complete`,
   `startup_slots_complete`, a truthful boolean `credentials_complete`, and `running-config`,
   `startup-config`, `boot-metadata` artifacts. Additional safe artifact labels
   are allowed. Passwords/cookies/raw responses belong only in protected files,
   never stdout or command arguments.
3. The framework checks actual timestamp freshness, required artifact/coverage
   flags and containment. An Aruba `credentials_complete: false` result with all
   other export guards passing is retained as LIMITED, not complete protection;
   missing slot/prompt/read-only proof still fails closed. Archive verification
   repeats native method/completeness/timestamp checks, rather than assuming a
   valid hash means complete recovery. It hashes every artifact into the archive
   manifest.
   Hash/readability and native export are distinct from a fenced restore test.

Qualification and rollback:

```bash
python3 -m unittest discover -s scripts/backup/tests -p 'test_network_recovery.py' -v
python3 scripts/backup/edsys-network-recovery.py status
```

The operator first preserves deployed helpers/collector/backup files, policy,
credentials and current known-good artifacts privately. Deploy only the reviewed
owned files; do not change host keys, timers, source settings or retention. Run
one bounded source capture, independently verify its archive and exact member
selection, then qualify a narrowly scoped encrypted snapshot/isolated restore
under the existing backup lock. UniFi application probes must have no production
volumes, published ports or network/AP reachability and must never reconnect,
adopt or provision real devices. Native switch exports must retain documented
credential/boot-image limitations. No firmware/update/install or service restart
is part of collection. Exact offsite snapshot parity/direct recovery remain
separate gates; use the existing offsite schedule. Rollback restores only the
preserved collector/helper/policy files and retains sources, staged last-good
exports and encrypted snapshots.

## Local image and Immich originals — deployed October 20, 2026

The deployed `edsys-image-originals.py` extends the existing daily remote
collector. Live collection and isolated database recovery passed the scoped
October 20 qualification described below.
It protects the entire current local image tree and the existing Immich original
uploads/library/profile files, one consistent PostgreSQL custom-format dump,
roles, resolved Compose configuration, exact image IDs/digests, extension versions
and a private reference/hash manifest. It preserves duplicate source paths and
original videos. It excludes regenerable thumbnails and encoded-video derivatives.
The owner's principal personal-photo collection is iCloud/iPhone; this local scope
does **not** establish protection of that broader collection or use Apple access.

PostgreSQL references and `pg_dump --snapshot` use one exported, held read-only
repeatable-read snapshot. Each source is checked before/after streaming; a second
file inventory, current reference query and application-config comparison reject
uploads, deletions, missing references or source/config changes during collection.
Uploads remain available. The native `pg_restore --file=/dev/null` fully decodes
the dump without connecting to or changing a database. This qualifies dump
readability, not a running application/database restoration.

Archives stream over the existing strictly pinned SSH route to root-private
`/srv/edsys-backup/staging/image-originals/.incoming`. Hashes are recomputed from
every received archive member without extraction. Only a complete verified
generation is published through an atomic `CURRENT.json` pointer; the two newest
completed generations remain staged. A failed attempt retains the last-good
pointer, records failed freshness in private `status.json`, and emits `FAILED`
into the existing collection manifest. Other critical backups continue. Existing
backup JSON/report fields separately expose originals collection status/time;
overall snapshot success does not imply every collector or offsite copy succeeded.
Reports treat a last successful collection older than 26 hours (or invalid/future
time) as stale. A backup dry-run does not invoke this original-file collector.

The existing staging include protects only complete archives; mandatory incoming
exclusion is also passed directly by `edsys-backup.sh`, independent of customized
exclude files. Do not weaken `/mnt/media/**` or global video exclusions. No timer,
offsite policy, source retention or repository retention changes are introduced.
The existing backup script still has retention/prune behavior; qualification
uses a narrowly scoped Restic backup under its existing lock, without invoking
the full script. Do not run the full installer merely to deploy this helper.

The deadline is at most 15 minutes; both sides use nice 15 / best-effort IO class
2 priority 7. Before collection, the hub requires three estimated complete stages
plus 1 GiB free reserve (roughly 10 GiB for the currently observed sources), and
the remote requires twice the estimated database size plus 1 GiB for its private
temporary dump. Stream/file/manifest sizes and file counts are bounded. Original
files stream directly; no multi-GiB original-file staging is created on the VM.
Interrupted `.incoming` data stays excluded; it is never accepted as last-good.
Private status/archives/config/manifests and actual image filenames must remain
outside Git, Obsidian and RAG. Ordinary stdout contains counts and fixed reasons.

Qualification and future maintenance:

1. Run the focused offline suite, shell syntax/ShellCheck and whitespace checks.
   Preserve the deployed collector/backup/helper and selections privately, then
   deploy only reviewed owned files. Confirm existing exact staging inclusion and
   incoming exclusion using the actual private selection files.
2. Run one bounded stage at low CPU/IO priority. Independently run
   `edsys-image-originals.py verify`; compare source/reference counts and hashes.
   No app restart, upload, playback action or source modification is needed.
3. Under the existing backup flock, create a scoped encrypted snapshot with the
   mandatory incoming exclusion. Do not forget, prune, unlock or invoke the full
   backup script. Confirm exact archived payload selection, including videos.
4. Restore that snapshot only into a root-private isolated target. Run the same
   verifier with `--stage-root` pointing at the restored stage, verify both archive
   hashes and every member, and decode the restored PostgreSQL dump again with
   its recorded compatible toolchain. Do not extract over production or start a
   duplicate application. A fenced database/application restore is a separate gate.
5. Let the existing offsite schedule copy the snapshot; qualify exact snapshot
   parity and selected direct-offsite recovery independently. Until then, report
   new local protection as local-only, with broader iCloud originals unverified.

Encrypted local acceptance: scoped snapshot `28301259` restored all 59 selected
image/current-repair files with exact size/SHA-256 matches. Both restored image
archives passed every-member verification and the custom dump decoded with the
retained exact PostgreSQL image. No full backup/prune/unlock was invoked.
The new offsite copy awaits the existing mirror schedule; exact offsite parity
remains unverified. Current acceptance is documented in
`EdSys-Master/docs/BACKUP_AND_RECOVERY.md`.

Rollback restores the privately preserved collector/backup/helper files and
stops future collection through that existing hook. Retain published stages,
source data and encrypted snapshots; rollback does not delete recovery material.

Offline check (no production access):

```bash
python3 -m unittest discover -s scripts/backup/tests -p 'test_image_originals.py' -v
```

### Accepted local collection and database recovery

The private completed generation preserves all **18 local image paths** (about
128 MiB, including duplicates), plus **433 active Immich assets**: 408 images
and 25 original videos. The Immich archive has 436 original/marker/profile files
and four recovery members. Full source-stream hashes, received archive-member
hashes and independent archive verification passed. All original counts/bytes and
the four production container identities/start times/running states were preserved;
configured production health checks remain healthy. No upload or service restart
was performed.

Initial qualification failed closed without publication. Docker's unordered
mount-response list caused a false configuration-change guard; canonicalizing
only that collection fixed the issue while preserving exact-value change checks.
The focused offline suite now passes **25 tests**, including reorder versus real
configuration changes and snapshot-constructor cleanup. Ruff, shell syntax,
warning/error-level ShellCheck and whitespace checks pass; older informational
ShellCheck findings elsewhere in the existing collector are unchanged.

An exact cached PostgreSQL image was transferred internally and retained privately
with its hash. A hub-only restore used that image, fresh bootstrap credentials,
no network/ports, 768 MiB RAM and half a CPU. The image needs to generate its
PostgreSQL configuration: the first read-only-root probe refused that write;
the successful probe supplied only a private writable configuration directory
alongside private PGDATA/recovery binds. Its root remained read-only. Restored
PostgreSQL and extension versions plus all 433 asset ID/path/type tuples match
the checkpoint exactly. Both archives were verified again. The owned probe and
all temporary database/configuration/credential data were removed; the qualified
image/export and content-free receipts remain private recovery material.

This establishes live original-file collection and isolated PostgreSQL recovery.
The new encrypted snapshot/direct-offsite recovery gates are reported separately
by the enclosing maintenance closeout. A running Immich application restore,
new upload/display acceptance and the principal iCloud/iPhone photo collection
remain outside this accepted local scope.

As of the 2026-07-13 Remotion hardening pass, the default 9950x include set also protects the operator repos under `/home/jeremy/code`, selected shell/Codex hub state, `/opt/edsys-workhorse` runtime state, and durable Remotion deliverables under `/mnt/ai-store/remotion/outputs`. Replaceable Remotion browser caches/temp data, Codex package caches, and Loki/renovate cache-heavy paths remain excluded.

The EdCore SDR archive at `/mnt/ai-store/edsys-sdr` is also selected. Raw IQ
captures and routine audio recordings are explicitly excluded; reviewed
configuration backups, metadata, decoded output, spectra, satellite products,
and curated material remain inside the encrypted Restic set.

The Basecamp Foothills extension under `basecamp-foothills/` creates one
private, manifest-verified recovery stage for all critical Basecamp
applications and host service configuration. It transfers that stage before
Restic, protects the canonical Foothills project tree and existing Unit
Selections history, checks Google authorization and exact latest-snapshot
parity, and performs a weekly isolated representative restore. See
[`basecamp-foothills/README.md`](basecamp-foothills/README.md).
The separate weekly direct-offsite restore uses the Google Drive Restic
backend rather than the local repository, so remote readability is proved
instead of inferred from upload parity.

## Model Lab history

The existing backup run also stages Model Lab's private SQLite history using the
online-backup helper with a separate source, lock and stage. Defaults are
`/opt/edsys-workhorse/edsys-ai-portal/data/model-lab` and
`/srv/edsys-backup/staging/model-lab/{current,previous}`. Override
`MODEL_LAB_HISTORY_DIR` when the Portal state volume moves. No timer is added.
The stage contains a verified hash/integrity manifest and a consistent database;
Restic includes the existing staging tree. It contains private chat content and
must never enter Git or RAG. A missing history file before first Lab use is
normal; snapshot failures for an existing file fail the backup.

For isolated verification, use the same helper with `--stage-root` pointing to
the Model Lab stage and the `verify` subcommand. Restore its
`current/databases/history.sqlite` with the Portal stopped; preserve displaced
DB/WAL/SHM files for rollback, set the app owner's 0600 permissions, and verify
owner-authenticated history after startup. Restoring over a running database is
not supported. Portal deployment checkpoints independently retain a consistent
history snapshot, while image rollback keeps the current history volume.

## Files

- `install-9950x.sh` creates directories, installs templates, and installs systemd units without enabling the timer.
- `edsys-init-restic.sh` validates the `rclone` remote and initializes the encrypted restic repository.
- `edsys-backup.sh` runs collection, backup, retention, and report generation.
- The backup removes only restic-confirmed stale locks before starting and waits
  up to `RESTIC_LOCK_RETRY` (five minutes by default) for legitimate repository
  activity to finish. Status-only queries use `--no-lock` so an interrupted
  inspection cannot block the next scheduled backup.
- `edsys-collect-remotes.sh` gathers selected read-only remote config exports into staging.
- `edsys-restic-check.sh` runs a repository health check.
- `edsys-restore-test.sh` performs a small restore test from the latest snapshot.
- `edsys-codex-state-stage.py` uses SQLite's online backup API to atomically stage every Codex SQLite database plus small state indexes under the private backup staging tree.
- `edsys-codex-state-restore-test.sh` restores that staged snapshot from restic into an isolated target and verifies hashes plus SQLite integrity without touching live Codex state.
- `edsys-backup.conf.example` is the private config template.
- `includes.9950x.example` and `excludes.example` are path selection templates.
- `systemd/` contains service and timer units.

## First-Time Setup On 9950x

```bash
cd /srv/edsys/edsys-infrastructure/scripts/backup
sudo ./install-9950x.sh
sudo rclone --config /etc/edsys-backup/rclone.conf config
sudo /srv/edsys-backup/scripts/edsys-init-restic.sh
sudo /srv/edsys-backup/scripts/edsys-backup.sh --dry-run
sudo /srv/edsys-backup/scripts/edsys-backup.sh
sudo /srv/edsys-backup/scripts/edsys-backup-check.sh
```

Use `edsys-gdrive` as the rclone remote name.

## Reliability Model

The local backup is independent of Google Drive. `edsys-backup.sh` creates or updates the local encrypted restic repository only.

Each local backup is tagged with its UTC `RUN_ID` so the generated JSON report can record the exact snapshot created by that run.

Remote Pi-hole config exports intentionally exclude volatile `pihole-FTL.db*` query-history database files from the tar stream. Core Pi-hole config remains protected; query history should be promoted through a separate backup policy only if it becomes recovery-critical.

The offsite mirror is a separate operation handled by `edsys-offsite-sync.sh`. It refuses to run unless the `edsys-gdrive` rclone remote has a dedicated Google OAuth client ID and secret configured.

This is more reliable than using restic directly against the `rclone:` backend for Google Drive, because snapshot creation is decoupled from Google API throttling and the offsite sync can resume cleanly.

## Creating a Dedicated Google OAuth Client for rclone

Do this before running a full offsite sync. The active 9950x setup completed the first full v3 mirror as run `20260629T020604Z`; keep the live config private and rotate credentials if exposed.

1. Open Google Cloud Console.
2. Create a project named `EdSys Backup Rclone`.
3. Enable the Google Drive API.
4. Configure the OAuth consent screen.
   Publish the internal/personal app to **Production** after the owner-only
   consent review. Google testing-mode refresh tokens can expire after seven
   days and are not acceptable for unattended backups.
5. Create an OAuth Client ID.
6. Choose application type `Desktop app`.
7. Copy the generated Client ID and Client Secret.
8. On `9950x`, run:

```bash
sudo rclone --config /etc/edsys-backup/rclone.conf config
```

Edit or recreate the remote named `edsys-gdrive`.

Use:

- Storage: Google Drive
- Client ID: paste Jeremy's generated client ID
- Client Secret: paste Jeremy's generated client secret
- Scope: full Drive access
- Shared Drive / Team Drive: no, unless intentionally using a Shared Drive

Then verify:

```bash
sudo RCLONE_CONFIG=/etc/edsys-backup/rclone.conf rclone about edsys-gdrive:
sudo /srv/edsys-backup/scripts/edsys-backup-status.sh
sudo /usr/local/libexec/edsys-gdrive-auth-check --auth-only
```

Do not paste the client secret, rclone token, refresh token, or config contents into public logs or Git.

## Offsite Sync

After the custom OAuth client is confirmed, or when manually verifying the existing active setup:

```bash
sudo /srv/edsys-backup/scripts/edsys-offsite-sync.sh --test-only
sudo /srv/edsys-backup/scripts/edsys-offsite-sync.sh --dry-run
sudo /srv/edsys-backup/scripts/edsys-offsite-sync.sh --mode=balanced
```

Modes:

- `conservative`: 1 transfer, 2 checkers, TPS 4.
- `balanced`: 2 transfers, 4 checkers, TPS 8.
- `fast`: 4 transfers, 8 checkers, TPS 12. Use only after balanced is stable.

If rate-limit errors appear, reduce mode or lower transfers/checkers/TPS in `/etc/edsys-backup/edsys-backup.conf`.

Production backup writes use the local repository followed by the mirror above.
Do not run a network-listening `rclone serve restic` service. The controlled
Foothills and HA/bedroom isolated restore checks may read the Drive repository
through Restic's rclone backend over stdin/stdout, with `--no-lock`, a private
isolated target and no repository-mutating operation. This proves remote
readability without a new listener; it does not change the backup write path.

## Security

Do not commit:

- `/etc/edsys-backup/backup.env`
- `/etc/edsys-backup/edsys-backup.conf`
- `/etc/edsys-backup/rclone.conf`
- `/etc/edsys-backup/restic-password`
- any raw restore output
- backup reports that include private paths or secrets

The restic repository is encrypted. It may contain private recovery material needed for disaster recovery.

## Restore Basics

```bash
sudo /srv/edsys-backup/scripts/edsys-restore-test.sh
sudo restic --repo /srv/edsys-backup/restic-repo/edsys-critical snapshots
```

To restore from the Google Drive mirror, copy it back locally first:

```bash
sudo RCLONE_CONFIG=/etc/edsys-backup/rclone.conf rclone copy "edsys-gdrive:EdSys Backups/restic/edsys-critical-v3" /srv/edsys-backup/restic-repo-restore
sudo RESTIC_PASSWORD_FILE=/etc/edsys-backup/restic-password restic -r /srv/edsys-backup/restic-repo-restore snapshots
```

Never restore over a live service path until the target service, snapshot date, and rollback path are confirmed.

### Codex state recovery

The live Codex databases use WAL mode and must not be trusted as a set of
ordinary files copied mid-write. Every non-dry-run local backup refreshes
`/srv/edsys-backup/staging/codex-state/current` first using SQLite's online
backup API. The direct live `*.sqlite*` files are excluded from restic; the
consistent staging copy is authoritative for database recovery. Other Codex
files remain protected through the normal encrypted `~/.codex` include.

The staging service retains `ProtectHome=read-only`, with a narrow systemd
read-write exception for `~/.codex`. This exception is required because an
active WAL database may need SQLite to create, map, lock, or update its
adjacent `-shm` shared-memory file even when the database connection itself is
read-only. The helper still opens every source database with URI `mode=ro`,
sets `PRAGMA query_only=ON`, and, apart from SQLite-managed `-shm` activity,
writes only the backup payload below the private staging root. No Codex
database-row or application-state write is expected.

The staging and restore-drill timers are safe to enable independently:

```bash
sudo systemctl enable --now edsys-codex-state-stage.timer
sudo systemctl enable --now edsys-codex-state-restore-test.timer
sudo systemctl start edsys-codex-state-stage.service
sudo systemctl start edsys-backup.service
sudo systemctl start edsys-codex-state-restore-test.service
```

The restore drill always targets `/srv/edsys-backup/restore-tests/codex-state/`.
It never writes into `~/.codex`. A live restore requires stopping the exact
Codex app-server/session writers, retaining the current state as rollback, and
separate operator approval.

Successful drills retain the newest three full restore trees and the newest 12
JSON reports by default. Configure `CODEX_RESTORE_KEEP_RUNS` and
`CODEX_RESTORE_KEEP_REPORTS` in the private backup config if that policy
changes. Retention accepts only canonical timestamp names directly below the
dedicated non-symlink roots; it refuses mounted or non-canonical candidates.
Reports publish through a flushed same-directory atomic rename, and a later
successful drill safely removes canonical incoming artifacts left by a hard
interruption.

Re-running `install-9950x.sh` also migrates older customized selection files:
it ensures `/srv/edsys-backup/staging` is an exact include and the live
`/home/jeremy/.codex/*.sqlite*` glob is an exact exclude. Existing files are
privately backed up before an atomic edit; symlinked or non-regular selection
files are refused.

## Bedroom endpoint recovery runtime

The existing local backup refreshes the scoped Pi/Nimo/Audiobookshelf recovery
stage through `/opt/edsys-bedroom-recovery/collect.py`; no new timer is added.
Select **exactly** `/opt/edsys-bedroom-recovery` in the private include file so
the reviewed runtime, private source pins and installed-file manifest recover
alongside the completed data under the already selected staging tree. Do not
replace this with a broad `/opt` include. The scoped collector's independent
endpoint freshness, SQLite logical parity, last-good behavior and restore limits
are documented in `services/bedroom-recovery-backup/README.md`.

## Old Google Drive Paths

Earlier attempts may exist:

- `EdSys Backups/restic/edsys-critical`
- `EdSys Backups/restic/edsys-critical-v2`

Do not delete them until `edsys-critical-v3` has completed and restored successfully.

Manual cleanup command, only after review:

```bash
sudo RCLONE_CONFIG=/etc/edsys-backup/rclone.conf rclone purge "edsys-gdrive:EdSys Backups/restic/edsys-critical"
sudo RCLONE_CONFIG=/etc/edsys-backup/rclone.conf rclone purge "edsys-gdrive:EdSys Backups/restic/edsys-critical-v2"
```
