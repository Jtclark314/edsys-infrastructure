# Consistent bedroom endpoint recovery

The existing daily EdSys local backup invokes this root collector before Restic;
no additional timer, listener or endpoint agent is installed. Its stable runtime
is `/opt/edsys-bedroom-recovery`, independent of a checkout/worktree. The backup
and offsite jobs remain separate. Collection never stops/restarts a service,
opens a desktop or sends speech, player/input/volume/overlay commands.

## Scope

- **Pi:** strict existing SSH host identity; all 602 reviewed display 0.7.1 files,
  seven online Kodi SQLite snapshots, current voice/display wrappers and units,
  audio identity, Kodi control configuration, screen transport key, firewall,
  boot configuration and scoped Kodi/PM4K settings. Before/after native read-only
  player snapshots retain only item hashes, player/speed/position and PID. A
  changed item/speed or changed exact paused position rejects that capture;
  ordinary advancing playback is allowed. User media, caches and transient
  reply/overlay files are excluded. This is selected service reconstruction,
  not a disk image or full OS restore.
- **Nimo:** existing SSH identity and exact machine/user plus eleven owned app,
  DLL, corrected-script and private-settings files. Capture stays in memory on
  Windows. The live WebView profile/session, status files, household recordings
  and other applications are excluded. Recovery needs an ordinary private HA
  sign-in; a browser-ready status is not authentication/voice acceptance.
- **Audiobookshelf:** verify the existing container's exact bind mounts and pin
  its resolved reviewed image ID (its existing image tag is `latest`). Online
  SQLite includes account/configuration and listening progress; the Compose
  definition and optional adjacent `.env` are private payloads. Media and the
  metadata/cache tree are excluded. No provider key or user row is printed.

SQLite uses a bounded read transaction and online backup API into memory. Schema
and row multiset digests match the same pinned source snapshot and captured
database. Source size is bounded by logical page count, including WAL growth.
Standalone serialized copies normalize only their read/write journal header to
version 1; SQLite integrity/foreign-key and logical parity are rechecked on the
hub. Live databases/WAL are never edited or checkpointed by the collector.

## Publication, failure and freshness

Root-private incoming files live below
`/srv/edsys-backup/staging/bedroom-recovery/.incoming`. The backup runner excludes
that exact subtree unconditionally, as well as HA's `.incoming`, independently
of private exclude-file drift. Every archive/member/hash, source pin, database,
path, size and timestamp is validated before a completed generation is renamed
into `generations/<UTC>-<nonce>` and `current.json` is atomically replaced.
Directories/files are fsynced. Partial incoming data never becomes selected.

Endpoints advance independently. A sleeping/offline Nimo is never awakened:
verified fresh Pi/ABS data still publishes alongside verified prior Nimo data
with its **original** capture timestamp and `stale` status. Missing/corrupt prior
data is marked `missing` rather than blocking other endpoints. No fresh endpoint
means no replacement of the last-good pointer. Status reports each source;
any failed/reused source or source older than 26 hours is degraded. Collector
failure is nonfatal to unrelated Restic backup. Status:
`/var/lib/edsys-backup/bedroom-recovery.json` and `collect.py status`.

Generation pruning is deliberately not added in this slice; existing historical
rollback packages stay untouched. The Restic repository's existing retention
policy remains authoritative. Monitor completed-stage disk growth and select a
narrow endpoint-generation retention policy before sustained expansion.

## Installation and verification

Prepare a root-private `expected.json` with exactly `pi` (602 reviewed ZIP member
hashes), `nimo` (eleven reviewed owned-file hashes; script pins from current
source) and `abs_image_id` (the reviewed current immutable Docker image ID).
Private settings/key/session material never belongs in Git or this README.

```sh
python3 -m unittest discover -s services/bedroom-recovery-backup/tests -v
python3 services/bedroom-recovery-backup/install.py --expected PRIVATE_JSON
sudo python3 services/bedroom-recovery-backup/install.py --expected PRIVATE_JSON --apply
sudo python3 /opt/edsys-bedroom-recovery/collect.py stage
sudo python3 /opt/edsys-bedroom-recovery/collect.py status
```

Deploy the reviewed narrow `scripts/backup/edsys-backup.sh` and status changes
after checking installed-source drift; preserve their owner/mode. These hooks
do not themselves run Restic or synchronize Drive during installation. A real
stage/readback must verify current code, private bindings, eight online SQLite
snapshots, logical parity, preserved media state and exact archive hashes.
Then separately verify selection and isolated local/offsite restores through
the existing encrypted chain. Readability does not establish application restore
or microphone/playback acceptance. All voice/playback tests remain separately held.

## Recovery

Restore only into an isolated private directory first. Read the top manifest,
verify outer hashes, archive CRC/member scopes and all recorded SQLite logical
digests/integrity before use. Preserve newer user queues/progress and bindings;
do not restore an old complete profile over a newer one. A replacement HA/Pi/ABS
must be fenced from production controllers until deliberately promoted. Restore
private keys/settings only through the approved encrypted recovery process.

To withdraw this collector, remove only its two reviewed backup hook/status
blocks after comparing current scripts. Keep completed generations and Restic
snapshots. Preserve hard `.incoming` exclusions while either collector uses them.
No endpoint process/profile/audio setting needs to change.
