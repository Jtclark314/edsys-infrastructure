# Private application backup and recovery

Scope: only the new Stirling PDF and changedetection.io services. This does not
repair, invoke, or add dependencies to the broad EdSys backup collector or its
general restore test. No source file authorizes deployment by itself.

## Capture and acceptance

The root helper uses `/run/edsys-backup.lock` and the existing root-private
`/etc/edsys-backup/edsys-backup.conf`. It never runs `forget`, `prune`, `unlock`,
initialization, a Docker daemon restart, Compose `up`, or any other service.
The helper uses `/usr/bin/python3` with the existing system PyYAML package
(`python3-yaml`) for safe YAML parsing; this is also a restore-host prerequisite.

Only `edsys-stirling-pdf` and `edsys-changedetection` are eligible. Container IDs,
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
  private API/session state, watches and snapshots. The store must parse, have a
  `watching` mapping and retain enabled private API access with a nonempty key.
  An intentionally empty watch list is valid; an empty/missing store is not.
- Every other top-level runtime entry except `images/`, the two captured app
  directories, and explicit `tmp`/`candidate` names (including `tmp-` and
  `candidate-` prefixes and their dot-prefixed variants). Thus private deployment
  envs and `proxy.json` are protected. Symlinks/special files fail closed.
- Both owning Docker source folders, the private-web-apps source folder, installed
  root application/backup units and timer, user proxy unit, exact installed
  control/mount-check/backup helpers and private mount UUID guard.
- The separate `images/` tree directly in the same Restic snapshot. At least two
  nonempty `.tar`, `.tar.gz`, or `.tar.zst` image archives are required. Root
  commissioning must prove these are the exact two qualified application images.
  Direct inclusion avoids repeatedly copying large exports into archive staging;
  unchanged exports use Restic deduplication.

Staging is `/srv/edsys-backup/staging/private-web-apps`. Generation directories
are write-once by policy. Archives include member hashes, sizes, modes, UID/GID
and complete inventories. Required payloads, source artifacts and images must be
present and nonempty. Unsupported links, missing state and tampering fail closed.
Private manifests, configuration, state and credentials never enter Git or output.

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
protection of completed PDFs/templates/signatures. Large-size checks use sparse
metadata fixtures; no multi-GiB content is written/read. No live Docker or
repository operation is performed.
Retention tests prove preservation of CURRENT/payload, two prior accepted sets,
failed/unknown/symlink/tampered directories, malformed receipts and newer receipts.

Syntax/unit validation and offline tests qualify the implementation only. First
production capture, direct-offsite restore and real isolated app restoration must
be recorded separately by the commissioning owner.
