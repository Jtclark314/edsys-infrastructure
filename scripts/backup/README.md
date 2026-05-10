# EdSys Google Drive Offsite Backup Tooling

Status: implementation baseline.

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

## Files

- `install-9950x.sh` creates directories, installs templates, and installs systemd units without enabling the timer.
- `edsys-init-restic.sh` validates the `rclone` remote and initializes the encrypted restic repository.
- `edsys-backup.sh` runs collection, backup, retention, and report generation.
- `edsys-collect-remotes.sh` gathers selected read-only remote config exports into staging.
- `edsys-restic-check.sh` runs a repository health check.
- `edsys-restore-test.sh` performs a small restore test from the latest snapshot.
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

The offsite mirror is a separate operation handled by `edsys-offsite-sync.sh`. It refuses to run unless the `edsys-gdrive` rclone remote has a dedicated Google OAuth client ID and secret configured.

This is more reliable than using restic directly against the `rclone:` backend for Google Drive, because snapshot creation is decoupled from Google API throttling and the offsite sync can resume cleanly.

## Creating a Dedicated Google OAuth Client for rclone

Do this before running a full offsite sync.

1. Open Google Cloud Console.
2. Create a project named `EdSys Backup Rclone`.
3. Enable the Google Drive API.
4. Configure the OAuth consent screen.
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
```

Do not paste the client secret, rclone token, refresh token, or config contents into public logs or Git.

## Offsite Sync

After the custom OAuth client is confirmed:

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

Do not use restic directly over Google Drive and do not run `rclone serve restic` for this system.

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
