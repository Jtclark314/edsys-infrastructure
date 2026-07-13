# Operations Helpers

Status: helper scripts for local EdSys operations. Prefer report-only behavior unless a script explicitly documents its write target.

## Scripts

- `edsys-container-recovery.py` - manifest-driven, health-gated Docker recovery and audit.
- `install-container-recovery.sh` - validated installer for the recovery manifest, systemd units, Docker drop-in, and live-restore setting.
- `docker-cleanup-report.sh` - prints Docker disk/reclaimable/exited-container/unused-volume candidates without pruning or deleting anything.
- `edsys-healthchecks-ping.sh` - generic systemd `ExecStartPost` ping helper. It reads `HC_PING_URL` from a private environment file and prints no ping URLs.
- `bootstrap-healthchecks.sh` - creates local Healthchecks records, private systemd environment files, and systemd drop-ins for EdSys timer pings. The generated files belong under `/etc/edsys-healthchecks/` and `/etc/systemd/system/*.service.d/`, not Git.
- `edsys-rag-watch-sync.py` - debounced user-service watcher for reviewed `*RAG Summary.md` changes. It triggers the existing `edsys-rag-sync.service`; the five-minute timer remains the fallback.
- `edsys-rag-enrich-metadata.py` - local-only metadata enrichment builder for `/mnt/ai-store/rag/enrichment/metadata.sqlite`; it reads the clean mirror and never rewrites Obsidian notes.
- `sync-edsys-repos.sh` - non-destructive five-minute fast-forward sync for the three authoritative `/srv/edsys` checkouts. It requires clean trees, tracks `origin/main`, refuses divergence, and never resets, cleans, commits, pushes, or deletes files.
- `install-git-sync.sh` - root-only installer for the Git sync script, systemd service/timer, and bounded logrotate policy. Use `--enable` to enable the timer and `--rotate-log` to rotate an existing log after review; replaced files are backed up under `/var/backups/edsys-git-sync/`.
- `systemd/edsys-rag-watch-sync.service` and `systemd/edsys-rag-enrich-metadata.service` - review/install templates for the watcher/enrichment flow. Copy scripts to `/usr/local/bin/` or adjust paths before enabling.
- `systemd/edsys-git-sync.service`, `systemd/edsys-git-sync.timer`, and `logrotate/edsys-git-sync` - deployment sources for the five-minute repository synchronization and its 1 MiB/14-rotation compressed log bound.

Review scripts before running and keep runtime output outside the repo unless it has been sanitized.

## Tests

The lightweight RAG watcher/enrichment tests live under `scripts/ops/tests/`.
Run them with any Python environment that has `pytest` installed, for example:

```bash
/home/jeremy/code/edsys-ai-portal/.venv/bin/python -m pytest -q scripts/ops/tests
```

The Git sync integration test is self-contained and uses temporary local bare
repositories:

```bash
scripts/ops/tests/test-sync-edsys-repos.sh
```
