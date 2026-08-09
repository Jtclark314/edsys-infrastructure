# Basecamp Foothills Offsite Backup

This service protects every recovery-critical Foothills application and host
configuration on Basecamp before the normal encrypted EdSys Restic snapshot.

## Recovery chain

1. Basecamp creates `C:\Foothills\OffsiteBackup\current` with:
   - SQLite online backups for Task List, ASI, active Observation Tracker V3,
     retained Observation Tracker V2 rollback, Speakr, and the Agent DVR file
     database;
   - the latest verified Unit Selections and Kindle Drop archives;
   - Unit Selections intake and configuration;
   - Task List, ASI, Observation Tracker V3 evidence/derivatives/reports,
     verified V3 backups, V3 recovery configuration/operations, retained V2
     rollback files, Portal, camera, Agent DVR, and Speakr files;
   - portal content and media;
   - scheduled-task XML, service definitions, firewall rules, SMB share
     definitions, OpenSSH host configuration and keys, and other encrypted-only
     recovery material.
2. `9950x` triggers a fresh stage when needed, transfers it, validates every
   manifest hash, reruns SQLite/ZIP/JSON checks, and atomically activates:
   `/mnt/ai-store/foothills-basecamp-offsite/current`.
3. The normal Restic job includes that path plus the canonical Foothills
   project tree at `/mnt/ai-store/foothills-project`.
4. The separate offsite job mirrors the encrypted Restic repository to Google
   Drive.

The Basecamp and 9950x stages contain private runtime data and credentials.
They must remain access-restricted and must never enter Git, RAG, ordinary
Google Drive folders, or unencrypted archives.

Observation Tracker recovery paths deliberately keep the active V3 database at
`apps/observation-tracker/data/observation_tracker.sqlite3` so existing restore
and verification tooling continues to find the production source of truth.
The disabled V2 database and files are isolated below
`apps/observation-tracker/legacy-v2/` until that rollback runtime is explicitly
retired.

## Schedule and ordering

- Basecamp Scheduled Task `Foothills Offsite Backup`: daily at 01:30.
- 9950x pull: daily at 01:50, with a fresh-stage fallback.
- EdSys Restic: daily at 02:15 and ordered after the pull and local Foothills
  catalog staging.
- Google Drive offsite mirror: after the local snapshot.
- Freshness/integrity recheck: 06:30, 12:30, and 18:30.
- Google Drive auth and latest-snapshot parity: 06:45.
- Representative local encrypted Restic restore test: Sunday at 07:30.
- Representative restore directly from the Google Drive Restic mirror: Sunday
  at 08:30.

## Install on Basecamp

Copy this directory to Basecamp and run from elevated PowerShell:

```powershell
.\Install-BasecampFoothillsBackup.ps1
```

## Install on 9950x

Install the Python helpers under `/usr/local/libexec`, install and enable the
systemd units, add these paths to `/etc/edsys-backup/includes.txt`, and install
`edsys-backup-basecamp.conf` as an `edsys-backup.service` drop-in:

```text
/mnt/ai-store/foothills-project
/mnt/ai-store/foothills-basecamp-offsite
/mnt/ai-store/foothills-unit-selections-basecamp-backups
```

## Acceptance

Do not call the chain healthy until all of these pass:

1. Basecamp Scheduled Task returns `0`.
2. The Basecamp and 9950x manifest generation/count/bytes agree.
3. Every manifest file hash reconciles.
4. SQLite integrity and foreign-key checks pass.
5. Nested ZIP CRC, path-safety, and database checks pass.
6. The local Restic snapshot contains the Basecamp stage and Foothills tree.
7. Google Drive contains that exact Restic snapshot ID.
8. A full `rclone check` and isolated restore of representative Basecamp and
   Foothills project files directly from Google Drive pass. The recurring
   restore test validates both the Task List and active Observation Tracker V3
   databases against the staged manifest and reruns SQLite integrity and
   foreign-key checks.
