# Operations Helpers

Status: helper scripts for local EdSys operations. Prefer report-only behavior unless a script explicitly documents its write target.

## Scripts

- `docker-cleanup-report.sh` - prints Docker disk/reclaimable/exited-container/unused-volume candidates without pruning or deleting anything.
- `edsys-healthchecks-ping.sh` - generic systemd `ExecStartPost` ping helper. It reads `HC_PING_URL` from a private environment file and prints no ping URLs.
- `bootstrap-healthchecks.sh` - creates local Healthchecks records, private systemd environment files, and systemd drop-ins for EdSys timer pings. The generated files belong under `/etc/edsys-healthchecks/` and `/etc/systemd/system/*.service.d/`, not Git.
- `edsys-rag-watch-sync.py` - debounced user-service watcher for reviewed `*RAG Summary.md` changes. It triggers the existing `edsys-rag-sync.service`; the five-minute timer remains the fallback.
- `edsys-rag-enrich-metadata.py` - local-only metadata enrichment builder for `/mnt/ai-store/rag/enrichment/metadata.sqlite`; it reads the clean mirror and never rewrites Obsidian notes.
- `systemd/edsys-rag-watch-sync.service` and `systemd/edsys-rag-enrich-metadata.service` - review/install templates for the watcher/enrichment flow. Copy scripts to `/usr/local/bin/` or adjust paths before enabling.

Review scripts before running and keep runtime output outside the repo unless it has been sanitized.

## Tests

The lightweight RAG watcher/enrichment tests live under `scripts/ops/tests/`.
Run them with any Python environment that has `pytest` installed, for example:

```bash
/home/jeremy/code/edsys-ai-portal/.venv/bin/python -m pytest -q scripts/ops/tests
```
