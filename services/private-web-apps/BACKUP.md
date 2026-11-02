# Private application backup and recovery

Scope: only Stirling PDF, changedetection.io, its stateless browser/egress helpers,
and their private MCP recovery artifacts. This does not
repair, invoke, or add dependencies to the broad EdSys backup collector or its
general restore test. No source file authorizes deployment by itself.

## Capture and acceptance

The root helper uses `/run/edsys-backup.lock` and the existing root-private
`/etc/edsys-backup/edsys-backup.conf`. It never runs `forget`, `prune`, `unlock`,
initialization, a Docker daemon restart, Compose `up`, or any other service.
The helper uses `/usr/bin/python3` with the existing system PyYAML package
(`python3-yaml`) for safe YAML parsing; this is also a restore-host prerequisite.

Only `edsys-stirling-pdf` and `edsys-changedetection` are eligible for cold capture. Container IDs,
names and complete bind contracts are checked before stopping anything. Stirling
requires all four state binds; changedetection additionally requires its two
captured read-only initialization/health scripts. Unexpected durable mounts fail
closed. Each previously running container is stopped separately, its persistent directory is
captured within a 180-second bound, and its prior running state is restored in
`finally` before hashing, image processing or Restic. A previously stopped
container stays stopped. Docker stop has a 45-second graceful budget. Busy
interactive work can be interrupted during this nightly capture. A forced-kill
exit or OOM kill does not qualify as a consistent capture; the prior running
state is restored and that backup attempt fails without replacing last-good.

Schema-2 captures also inspect `edsys-changedetection-browser` and
`edsys-changedetection-egress`. Neither helper is stopped, started or copied as
application state by this backup. They must exist, including when intentionally
stopped, and have no durable storage except captured read-only source bindings.
The exact three-service changedetection Compose project is rendered privately;
its pinned image references, resolved local image IDs, project/service labels,
configuration hashes and mounts must match the deployed containers.
The source `docker/changedetection/images.lock.json` must have `schema_version: 1`
and an `images` mapping with `app`, `browser`, and `egress` records. Each record's
`image_id` must match its deployed Docker image ID; producer provenance fields
are preserved. Its `source_sha256` map must cover both Dockerfiles, both patches,
the app guard, egress proxy and browser requirement input/lock; every recorded
build-input hash must match captured source. App and egress may share one
qualified image and retained export.
The source lock is checked again during isolated restore validation. Source/env
hashes and deployment identity are rechecked after capture. A missing sidecar,
uncaptured volume, changed deployment, or missing export fails without replacing
the last accepted backup. No Compose `up`, build or pull is invoked.

A root-private restart intent allows `ExecStopPost` to restore an interrupted
capture. The exact saved container ID, expected bind and mount guard must still
match. An intent older than 15 minutes cannot start a stopped container without
operator review. This avoids resurrecting a service intentionally stopped later.
The helper confirms Docker running state; deployment acceptance must also prove
actual application health and behavior after restart.

The backup unit is `After=` and `PartOf=edsys-private-web-apps.service`, without
pulling that application unit in. Stopping/restarting the application unit first
stops an active backup and completes its recovery intent; only then does the
application unit stop its containers. `KillMode=mixed` signals the helper first,
allowing an in-flight Docker stop to finish before its `finally` recovery. The
150-second stop budget covers the bounded graceful stop and restart checks.
Use `systemctl start edsys-private-web-apps-backup.service` for live captures;
calling `backup` directly bypasses this systemd shutdown ordering. An inactive
application unit is not started just to protect intentionally stopped app state.
The application lifecycle helper stops changedetection, browser, egress, then
Stirling by exact container name, without reading Compose or a missing mount.
Its application unit needs a 300-second stop budget for the four bounded stops;
the backup unit's 150-second recovery budget remains sufficient for one capture.

Protected content:

- Stirling's complete persistent `stirling-pdf/` directory, including required
  nonempty `configs/settings.yml` parsed as a YAML mapping, and durable
  pipeline inputs/outputs, completed PDFs, signatures and templates beneath that
  root. Browser-held documents and tmpfs processing files are outside its
  persistence contract. Static UI symlinks must stay outside the persistent tree;
  use nested bind directories for durable custom assets.
  Required directories are `configs`, `signatures`, `templates` and `pipeline`;
  they may be empty except for required settings. `configs/custom_settings.yml`
  is optional and may be empty or a YAML mapping. There is no native database in
  this OSS-only profile.
- changedetection's complete `changedetection/` datastore, including settings,
  private API/session state, watches and snapshots. Version 0.60.10 serializes
  `settings.application` in `changedetection.json` and watches in separate
  `{uuid}/watch.json` files; it does not require a top-level `watching` mapping.
  Settings must parse as mappings and retain enabled private API access with a
  nonempty string key.
  An intentionally empty watch list is valid; an empty/missing store is not.
- Every other top-level runtime entry except `images/`, the two captured app
  directories, and explicit `tmp`/`candidate` names (including `tmp-` and
  `candidate-` prefixes and their dot-prefixed variants). Thus private deployment
  envs and `proxy.json` are protected. Symlinks/special files fail closed.
- Both owning Docker source folders, the private-web-apps source folder, the
  complete `services/stirling-mcp` source folder with required `server.py`
  entrypoint, and the complete `services/changedetection-mcp` source folder.
  The latter requires both Python entrypoints, launcher, installer, dependency
  lock, API/catalog files and license/notice files. Installed root application/backup units and timer, user proxy unit,
  exact installed control/mount-check/backup helpers and private mount UUID guard.
  Installed control and backup helper bytes must match their captured source.
- The separate `images/` tree directly in the same Restic snapshot. At least two
  nonempty `.tar`, `.tar.gz`, or `.tar.zst` archives are required. `archives.json`
  lists Docker exports as `{file, image_id, sha256, size}` records; filenames must
  be basenames, and every entry's file, hash and size must match the actual image
  inventory. Both captured app image IDs and both deployed stateless helper image
  IDs must have matching retained exports.
  A new app image without its export fails validation before any Restic write or
  acceptance. Root commissioning additionally proves actual Docker load/restore.
  Direct inclusion avoids repeatedly copying large exports into archive staging;
  unchanged exports use Restic deduplication.
  MCP runtime/venv archives are additional artifacts in this tree, not Docker
  image entries. The changedetection MCP runtime receipt must match its archive,
  active immutable release and captured source, as described below. Private
  registration, API key and prechange Codex configuration are protected under
  `deploy/`. Live MCP release symlinks,
  artifact directories and user documents outside this runtime root are not
  traversed or copied.

Staging is `/srv/edsys-backup/staging/private-web-apps`. Generation directories
are write-once by policy. Archives include member hashes, sizes, modes, UID/GID
and complete inventories. Required payloads, source artifacts and images must be
present and nonempty. Unsupported links, missing state and tampering fail closed.
Private manifests, configuration, state and credentials never enter Git or output.

## changedetection MCP recovery contract

The installed runtime is
`/home/jeremy/.local/share/edsys-changedetection-mcp/releases/TIMESTAMP-VERSION`,
selected by `current`. The helper only validates that link and the named runtime
files; it never traverses the live artifact directory or user documents.
The selected release must retain its virtual environment and match the source
hashes in `images/changedetection-mcp-runtime.json`:

| Receipt field | Required value |
| --- | --- |
| `kind` | `isolated-python-runtime` |
| `file`, `sha256`, `size` | An immutable `changedetection-mcp-runtime-*.tar.zst` basename and its exact inventory hash/size |
| `release` | The selected `TIMESTAMP-VERSION` directory basename |
| `restore_parent` | `/home/jeremy/.local/share` |
| `source_commit` | Qualified source commit |
| `source_hashes` | SHA-256 for `server.py`, `bridge.py`, `run.sh`, `requirements.lock`, `api-spec.json`, `catalog-source.json` |

Root commissioning creates and restores the qualified archive, including the
venv. Scheduled backup verifies its exact bytes and source/release association;
it does not execute the MCP or requalify package behavior. Keep a new immutable
archive name when changing a release.

Required private deployment artifacts are:

- `changedetection-mcp-api-key`, mode `0600`.
- `changedetection-mcp-registration.toml`, containing the `edsys-changedetection`
  server with the `current/run.sh` launcher, backend `http://10.254.230.18:5000`, and
  `CHANGEDETECTION_API_KEY_FILE` pointing to that protected key file.
  `CHANGEDETECTION_ARTIFACT_DIR` must stay outside the source, runtime release and
  protected application trees.
- `changedetection-mcp-install.json`, whose `release`, `source_commit`,
  `new_server`, and `preserved_all_existing_settings=true` match the installation.
  `before_config_file` names the exact `codex-config-before-changedetection-*.toml`
  basename; `before_sha256` must match that protected prechange file.

The captured key file must equal the enabled native API token in the captured
`changedetection.json`. Comparisons never return credential values. Restoration
uses the captured artifacts, not the current host's MCP installation. Applying a
saved registration or global prechange config remains an explicit operator step.

The stable `snapshot-payload/` directory is selected alongside the stable
runtime `images/` directory. This avoids a separate Restic retention group for
every generation path. A successful backup verifies exact scope tags, full
snapshot ID, path/file inventories, sizes and a returned manifest hash before
atomically publishing `CURRENT.json`. Failed captures/snapshots retain the prior
accepted pointer and payload. After acceptance, a separate private receipt under
`accepted/` binds the generation, full validated snapshot ID and manifest hash.
Local cleanup keeps `CURRENT` and two prior accepted generations. An older
directory is eligible only with the exact owned name/file set, private ownership,
a matching valid receipt/manifest and rechecked archive hashes. Cleanup unlinks
only those named staging files; it never recursively traverses another tree.

Failed, unaccepted, unknown, changed or unexpectedly newer generations remain
private for diagnosis. App data, image exports, rollback assets and encrypted
Restic history are untouched. A cleanup failure reports `staging_cleanup.status`
as `deferred` in the run result/`last-attempt.json`; the verified backup remains
accepted. Diagnose preserved failures or deferred cleanup before they accumulate
enough to block the next space preflight. This changes only local generated
staging, with no Restic retention or repository-policy change.
An operator can retry the same strict cleanup without stopping apps using
`sudo /usr/local/libexec/edsys-private-web-apps-backup cleanup-staging`. It takes
the shared lock and still refuses to delete unknown or failed generations.

New captures use manifest schema 2 and tag `edsys-private-web-apps-v2`. Explicit
full-ID schema-1 snapshots tagged `edsys-private-web-apps-v1` remain restorable
without requiring the newly added MCP or sidecars. Restore checks that the
snapshot's format tag matches its manifest. A new capture cannot fall back to
schema 1 when expanded prerequisites are absent. Receipt/CURRENT format stays 1;
local accepted-generation cleanup recognizes both manifest versions. Existing
Restic history, retention settings and offsite schedules are unchanged.

Each app-state archive is bounded at 32 GiB and 100,000 archive entries. Before
either app stops, free-space checks allow for uncompressed archives, conservative
metadata overhead, a full new encrypted repository copy and image exports, plus
a 2 GiB free reserve. If staging and the repository share a filesystem, both
copies count against that filesystem. The estimate does not rely on deduplication
or reflinks. Insufficient space or oversized state fails before cold capture.

The initial workload must fit a measured 180-second cold-copy window at 03:30.
This is a safety ceiling, not a qualified 32 GiB throughput promise. Each accepted
generation writes a full uncompressed state copy locally even when Restic
deduplicates its data; the three accepted local generations are retained as above.
Heavy pipeline use can increase daily writes and stop duration. Before growth
exhausts these bounds, qualify a different consistent capture for these services.
Completed PDFs are never silently excluded or deleted
to make a capture fit. Timeout/copy failure restores prior running state and
retains the previous accepted backup.

## Operator installation and first acceptance

These steps are for the commissioning owner after app/access artifacts are ready;
they have not been executed by authoring the helper.

1. Ensure both apps are qualified, the runtime mount guard is accepted, private
   deployment envs and `proxy.json` exist, and exact image exports are under
   `/mnt/ai-store/edsys-private-services/images`. The owning source directories
   must be promoted to `/srv/edsys/edsys-infrastructure` before first capture.
2. Install `backup.py` as root-owned mode `0755` at
   `/usr/local/libexec/edsys-private-web-apps-backup`; install the supplied backup
   service and timer under `/etc/systemd/system`, reload units, but leave the
   timer disabled until acceptance. Required existing names are
   `edsys-private-web-apps.service`,
   `/usr/local/libexec/edsys-private-web-apps-control`,
   `/usr/local/libexec/edsys-private-web-apps-mount-check`,
   `/etc/edsys-private-web-apps/mount-uuid`, and Jeremy's user
   `edsys-private-web-apps-proxy.service`.
3. In an idle window run `sudo systemctl start edsys-private-web-apps-backup.service`.
   Inspect the sanitized receipt with
   `sudo /usr/local/libexec/edsys-private-web-apps-backup status`.
   Retain its full `snapshot_id`; never substitute unqualified `latest`.
4. Run the helper's `restore --snapshot FULL_ID --target NEW_ABSOLUTE_DIRECTORY`
   into a new root-private directory under
   `/srv/edsys-backup/restore-tests/private-web-apps/`. It validates the two exact
   snapshot paths and tags, restores with Restic verification, and checks all
   archive/member/image hashes and modes. It refuses live-state/staging targets.
5. Have the app owners restore the captured tar state into isolated app fixtures
   with the exact saved image, private routes disabled and notifications/external
   effects disarmed. Prove representative real app functions, then remove only
   those isolated fixtures. A successful archive check is not app acceptance.
6. Use the existing offsite mirror procedure for the explicit commissioning
   checkpoint. The helper itself never changes the mirror or its policy. Repeat
   the exact-ID restore with `--offsite` and a different new private target. This
   reads Drive directly using `--no-lock`/`--no-cache`; no remote write is made by
   the restore command. Preserve the accepted root/app/access rollback set.
7. After local/offsite/application acceptance, enable only
   `edsys-private-web-apps-backup.timer`. It runs at 03:30 America/New_York,
   independently of Basecamp and other collectors. `Persistent=false` avoids a
   daytime catch-up stop. The existing offsite mirror normally runs later.

`verify --stage ABSOLUTE_PAYLOAD --image-root ABSOLUTE_IMAGES` checks a selected
already-restored pair without invoking Docker or Restic. `recover-running` is
reserved for an interrupted current capture; it never runs Compose or selects a
different container. If restoration of running state fails, preserve its intent,
review the mount and exact new container, and use the owning app recovery route.

## Offline checks

From this directory run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_backup.py`.
Tests use only temporary fixtures and mocked Docker/Restic. They cover empty and
missing state, archive/image/mode tampering, malformed app content, prior stopped
state, restart after capture failure, failed restart intent, stale intent, exact
snapshot scope, refused live restore, and preservation of last-good state after
capture/snapshot failures. Coverage includes graceful process cancellation,
low-space refusal, shared-filesystem capacity, oversized-state refusal and
protection of completed PDFs/templates/signatures, the actual 0.60.10 settings
format, private API requirements, MCP source recovery, and missing/stale image
exports with last-good preservation before any Restic write. Large-size checks use sparse
metadata fixtures; no multi-GiB content is written/read. No live Docker or
repository operation is performed.
Retention tests prove preservation of CURRENT/payload, two prior accepted sets,
failed/unknown/symlink/tampered directories, malformed receipts and newer receipts.
Expanded tests cover absent or changed sidecars, immutable image selection,
uncaptured volumes, source/config drift, missing or stale MCP release/venv/export,
registration and prechange configuration, native API-key agreement, unchanged
sidecar running state, and isolated schema-1 restore without live MCP checks.
Run `python3 -m unittest -q test_backup.py test_control.py` for both backup and
the exact-name lifecycle stop/failure-cleanup tests.

Syntax/unit validation and offline tests qualify the implementation only. First
production capture, direct-offsite restore and real isolated app restoration must
be recorded separately by the commissioning owner.
