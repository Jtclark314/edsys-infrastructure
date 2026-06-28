# Operations Helpers

Status: helper scripts for local EdSys operations. Prefer report-only behavior unless a script explicitly documents its write target.

## Scripts

- `docker-cleanup-report.sh` - prints Docker disk/reclaimable/exited-container/unused-volume candidates without pruning or deleting anything.
- `edsys-healthchecks-ping.sh` - generic systemd `ExecStartPost` ping helper. It reads `HC_PING_URL` from a private environment file and prints no ping URLs.
- `bootstrap-healthchecks.sh` - creates local Healthchecks records, private systemd environment files, and systemd drop-ins for EdSys timer pings. The generated files belong under `/etc/edsys-healthchecks/` and `/etc/systemd/system/*.service.d/`, not Git.

Review scripts before running and keep runtime output outside the repo unless it has been sanitized.
