# Home Assistant native backup export

Exports a completed protected HAOS application backup through the established
9950x → `pve-node3` → QEMU guest-agent → `hassio_supervisor` route. This collector
creates no native backup and changes no HA, TV, voice, player or app setting.
It adds no listener or credential and does not extend the existing Restic include
set. Source and mocked tests alone do not establish deployed/offsite acceptance.

## Deployment checkpoint — October 16 evening

The initial protected `ab270a93` generation completed on 9950x. The actual
hardened systemd service then proved idempotent reuse and validated all seven
encrypted members against the current private key. Its daily 01:40 timer is
active. The knowledge-refresh timer was restored and its normal shared-lock
refresh passed. Encrypted snapshot/mirror and isolated file-recovery evidence
is recorded in the canonical
[backup runbook](https://github.com/Jtclark314/EdSys-Master/blob/main/docs/BACKUP_AND_RECOVERY.md#home-assistant-recovery).
Full HA/MA application restoration remains a separate maintenance gate.

## Ownership and selection

HA's native backup configuration owns daily creation at 01:00, app selection,
native protection and seven local automatic copies. Those settings must be
configured and read back separately. The independent collector timer runs at
01:40 America/New_York, before the existing local encrypted Restic job at 02:15
and Google Drive mirror at 04:15. An HA/export failure does not become a fatal
dependency of unrelated critical backups. Do not add a fatal `ExecStartPre=` or
`Requires=` to `edsys-backup.service`.

The collector chooses the newest completed eligible backup, at most 26 hours old
(with at most five minutes of future clock skew). `--backup-id <8-hex-ID>` pins an
initial export to the native metadata ID. It refuses duplicate candidate IDs.
An older backup outside that freshness limit is not accepted as a successful
new run. Its last good completed stage remains available for recovery.

Eligibility requires native protection, HA with explicit database inclusion,
and payloads/metadata for all three current apps:

- `core_matter_server`
- `core_mosquitto`
- `d5369777_music_assistant`

Current native archives still use `addons` in outer metadata even though the
Supervisor API now calls them apps. Managed archives may be named
`automatic_backup_...tar` while their metadata slug is an unrelated eight-hex
ID. The fixed read-only guest inventory binds the exact safe filename to that
metadata ID. A managed backup can be labeled `partial` while including all
required HA/database/apps; selection validates actual coverage.

## Private files and atomic publication

| Path | Purpose |
| --- | --- |
| `/etc/edsys-backup/home-assistant-recovery-key` | Existing owner-configured native recovery key, root-owned mode 0600; never print it |
| `/srv/edsys-backup/.home-assistant-incoming/` | Root 0700 incoming work outside the selected staging tree, on the same filesystem |
| `/srv/edsys-backup/staging/home-assistant/<native-ID>/` | Completed generation, root 0700 |
| `native-backup.tar` | Exact protected native archive, root 0600 |
| `recovery-key` | Exact native key copy, root 0600, protected offsite by encrypted Restic |
| `manifest.json` | Private hashes, byte counts, normalized coverage and stable source identity, root 0600 |
| `status.json` | Latest success/failure, check time and fixed failure code, root 0600 |
| `last-success.json` | Last successful backup ID/date, preserved on failure, root 0600 |

The local password for the enclosing Restic repository must remain recoverable
from the approved password manager/offline store independently of this host.
Native protection plus a staged native key does not replace that prerequisite.
Never upload the archive or native key to an ordinary Drive folder, Git or RAG.

Each source file is opened without following symlinks. Export checks the source
device/inode/size/mtime/ctime before and after each bounded one-MiB chunk and
validates each chunk's SHA-256 and exact offset/length. Before publication it
rechecks the full source identity/hash, full local archive hash, outer metadata
and the recovery-key copy. Every export and idempotent reuse also proves the
current hub key matches the same stable native archive using the installed
Supervisor `SecureTarArchive.validate` on all encrypted component members.
The key travels only in protected SSH/QGA stdin, never in a command argument,
diagnostic or status. A missing validator, bad key or changed archive refuses
publication. Truncated QGA output, changed files, malformed
metadata or incomplete transfers fail closed. No guest output, archive bytes or
key values are printed. `--progress` emits aggregate byte counts only.

Only a nonzero SSH/QGA invocation or `TimeoutExpired` can retry the exact same
fixed read request, up to three retries after the initial attempt, with 1/2/4
second backoff and a four-minute total per-call budget. Changed/unsafe content,
guest semantic failures, incomplete responses and truncation fail immediately.
Chunks remain one MiB. The export has the existing 30-minute deadline and
35-minute service timeout; retries do not establish a diagnosis for transient
QGA failures. Each fixed read takes the remote node3 lock
`/run/lock/edsys-ha-qga-300.lock` with a ten-second bounded wait. The existing
knowledge refresh uses the same lock with a two-second wait; lock contention
returns code 75 without invoking QGA and is eligible only for the collector's
bounded transport retries. This coordinates these two workers, not every
possible guest-agent client. Operators must avoid concurrent manual QGA work
or take that same remote lock. No global host wrapper is installed.

Outer tar validation rejects links, special members, duplicate names and path
traversal. It never extracts archive members. It checks encrypted component
presence and explicit database metadata. Cryptographic key/member validation
does not restore the nested HA database or prove HA/MA application recovery;
those still require a controlled isolated restore exercise.

Completed files are flushed and verified, then the generation is atomically
renamed into selected staging. No incomplete generation is selected by the
existing backup include. Re-running against the same stable source verifies
the existing stage and reuses it; a mismatch does not overwrite it. The first
slice does not delete or prune historical completed generations. Only its own
failed incoming directory is removed; an interrupted process may leave an
unselected incoming directory for explicit inspection.

## Install and initial export

After the exact source and authorized deployment are reviewed, preserve the
existing native configuration and key. Ensure the key is privately installed
at the path above without putting its value in a command, log or document.

```sh
sudo install -d -o root -g root -m 0700 \
  /srv/edsys-backup/staging/home-assistant \
  /srv/edsys-backup/.home-assistant-incoming
sudo install -o root -g root -m 0644 \
  services/home-assistant-backup/edsys-home-assistant-backup.service \
  services/home-assistant-backup/edsys-home-assistant-backup.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
# Pin the known completed native ID for the initial export:
sudo python3 services/home-assistant-backup/collect.py --backup-id <native-ID> --progress
sudo systemctl enable --now edsys-home-assistant-backup.timer
```

The unit executes the canonical source at
`/srv/edsys/edsys-infrastructure/services/home-assistant-backup/collect.py`.
Root uses `runuser -u jeremy` for existing key-only SSH. Host-key checking is
strict; no automatic new host trust is added. The unit's root-private runtime
directory holds a nonblocking collector lock. A 30-minute export deadline and
35-minute service timeout bound runaway work. The archive limit is 8 GiB.

## Verification and failure handoff

```sh
pytest -q services/home-assistant-backup/tests
sudo systemctl show edsys-home-assistant-backup.service \
  -p Result -p ExecMainStatus -p ActiveState
sudo systemctl show edsys-home-assistant-backup.timer \
  -p ActiveState -p LastTriggerUSec -p NextElapseUSecRealtime
sudo cat /srv/edsys-backup/staging/home-assistant/status.json
sudo cat /srv/edsys-backup/staging/home-assistant/last-success.json
```

The two JSON status files contain only aggregate status/IDs/times. Treat a
failed service, a failed current status, a stale check or a last-good backup
older than 26 hours as an export problem; a preserved archive is not proof that
the latest schedule succeeded. The existing `edsys-backup-status.sh` now calls
the bounded `health.py` projection: failed/missing service or status, unverified
key, invalid IDs/times and backup/check age over 26 hours report `unhealthy`.
It reports current collector status separately from the last good export and
does not claim that either has reached Restic or Google Drive. It reads only
the two status files and bounded systemd metadata, never manifests or keys;
an unhealthy export does not stop other backup status sections. Missing/insecure configuration can
prevent a status write; the systemd result still fails explicitly.

After export, run the existing local backup and separate mirror through their
own established procedures. Read back the exact new snapshot's archive and
manifest selection, then the same snapshot's presence in
`edsys-gdrive:EdSys Backups/restic/edsys-critical-v3`. Existing retention is
30 daily, 12 weekly and 12 monthly. `snapshots --latest 1` is per-group; use
`max_by(.time)` when identifying the globally newest snapshot. Preserve actual
snapshot IDs in private evidence.

Acceptance requires an isolated restore of the selected archive/manifest/key,
matching hashes, and a separately controlled HA/MA application restore with
duplicate controllers/automations fenced. Existing Foothills restore proofs do
not qualify HA/MA. No physical speech/playback test is a collector prerequisite;
the owner's live-test hold remains in force.

## Narrow rollback

Disable only `edsys-home-assistant-backup.timer` and revert its two unit files
and this source if necessary. Preserve the native schedule/key and completed
generations until their recovery coverage has been reviewed. Do not restore a
HA backup, rotate the key, delete archives or modify the main Restic/offsite
jobs as an incidental collector rollback. No historical generation cleanup is
implemented here.
