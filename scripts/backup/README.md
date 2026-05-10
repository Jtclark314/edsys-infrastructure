# EdSys Google Drive Offsite Backup Tooling

Status: implementation baseline.

This directory contains the host-side tooling for EdSys offsite backups.

## Design

- Orchestrator: `9950x`
- Snapshot engine: `restic`
- Cloud transport: `rclone`
- Offsite target: Google Drive
- Local repository: `/srv/edsys-backup/restic-repo/edsys-critical`
- Offsite mirror: `EdSys Backups/restic/edsys-critical-v2`
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
- `edsys-backup.env.example` is the private config template.
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
sudo systemctl enable --now edsys-backup.timer
```

Use `edsys-gdrive` as the rclone remote name.

## Reliability Model

The backup first writes to a local encrypted restic repository. After the local snapshot succeeds, `rclone sync` mirrors that repository to Google Drive with conservative throttling:

- one transfer at a time
- two checkers
- explicit TPS limit
- slow Google Drive pacer settings
- high retry counts

This is more reliable than using restic directly against the `rclone:` backend for Google Drive, because snapshot creation is decoupled from Google API throttling and the offsite sync can resume cleanly.

## Security

Do not commit:

- `/etc/edsys-backup/backup.env`
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

Never restore over a live service path until the target service, snapshot date, and rollback path are confirmed.
