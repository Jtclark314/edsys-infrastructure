# Operations Helpers

Status: helper scripts for local EdSys operations. Prefer report-only behavior unless a script explicitly documents its write target.

## Scripts

- `edsys-container-recovery.py` - manifest-driven, health-gated Docker recovery and audit.
- `install-container-recovery.sh` - validated installer for the recovery manifest, systemd units, Docker drop-in, and deterministic host-shutdown posture with live restore disabled.
- `docker-cleanup-report.sh` - prints Docker disk/reclaimable/exited-container/unused-volume candidates without pruning or deleting anything.
- `edsys-healthchecks-ping.sh` - generic systemd `ExecStartPost` ping helper. It reads `HC_PING_URL` from a private environment file and prints no ping URLs.
- `bootstrap-healthchecks.sh` - creates local Healthchecks records, private systemd environment files, and systemd drop-ins for EdSys timer pings. The generated files belong under `/etc/edsys-healthchecks/` and `/etc/systemd/system/*.service.d/`, not Git.
- `configure-uptime-kuma-alerts.py` - configures and binds the private Uptime Kuma Pushover or Twilio provider from environment-supplied credentials. Take a SQLite-consistent backup first. Pushover TTL is omitted by default because the API rejects `ttl=0`; any explicit `PUSHOVER_TTL` must be a positive integer.
- `edsys-rag-watch-sync.py` - debounced user-service watcher for reviewed `*RAG Summary.md` changes. It triggers the existing `edsys-rag-sync.service`; the five-minute timer remains the fallback.
- `edsys-rag-enrich-metadata.py` - local-only metadata enrichment builder for `/mnt/ai-store/rag/enrichment/metadata.sqlite`; it reads the clean mirror and never rewrites Obsidian notes.
- `sync-edsys-repos.sh` - non-destructive five-minute fast-forward sync for the three authoritative `/srv/edsys` checkouts. It requires clean trees, tracks `origin/main`, refuses divergence, and never resets, cleans, commits, pushes, or deletes files.
- `install-git-sync.sh` - root-only installer for the Git sync script, systemd service/timer, and bounded logrotate policy. Use `--enable` to enable the timer and `--rotate-log` to rotate an existing log after review; replaced files are backed up under `/var/backups/edsys-git-sync/`.
- `edsys-codex-report-runner.py` - private report orchestrator for the read-only Morning Brief and weekly Codex maintenance. It prevents overlap, publishes report/status files atomically, keeps report bodies out of the journal, and retains 31 daily or 13 weekly runs.
- `install-codex-operations.sh` - root-only, idempotent installer for the authoritative Morning Brief, weekly maintenance, and grounded-RAG quality-gate services/timers. File replacement is atomic and a failed replacement transaction restores prior tracked files. `--enable` refuses unless the root-only eval environment, AI Store mount, report generators, Portal evaluator/virtual environment, query contract, and deployed EdSys-Master acceptance wrapper are present.
- `edsys-rag-eval.env.example` - two-key placeholder template for the root-private Portal credential used by the deterministic local acceptance gate. Never put a real value in Git.
- `systemd/edsys-rag-watch-sync.service` and `systemd/edsys-rag-enrich-metadata.service` - review/install templates for the watcher/enrichment flow. Copy scripts to `/usr/local/bin/` or adjust paths before enabling.
- `systemd/edsys-git-sync.service`, `systemd/edsys-git-sync.timer`, and `logrotate/edsys-git-sync` - deployment sources for the five-minute repository synchronization and its 1 MiB/14-rotation compressed log bound.
- `systemd/edsys-morning-brief.*` - daily 06:00 America/New_York report schedule, executed by 9950x even when no Codex Desktop session is open.
- `systemd/edsys-weekly-codex-maintenance.*` - Monday 07:30 America/New_York maintenance and RAG-memory-hygiene report schedule.
- `systemd/edsys-rag-golden-eval.*` - Monday 05:00 America/New_York grounded-RAG quality gate. It calls the existing Portal evaluator through the EdSys-Master acceptance wrapper; it does not duplicate the evaluator or silently enable a cloud judge.

The three system timers are the authoritative always-on executors. Any Codex
Scheduled task is presentation/delivery only and must not be the sole scheduler.
Runtime reports and raw eval results stay outside Git under
`/var/lib/edsys-codex-reports/`, `/var/lib/edsys-rag-eval/`, and
`/mnt/ai-store/rag/evals/`.

The two report units explicitly put `/home/jeremy/.local/bin` first in `PATH`
so scheduled checks use the active standalone Codex installation rather than a
stale system-wide package. The installer verifies that entry point before
enabling any timer.

Provision the private eval environment before enabling:

```bash
sudo install -d -m 0700 -o root -g root /etc/edsys-secrets
sudo test -e /etc/edsys-secrets/edsys-rag-eval.env || \
  sudo install -m 0600 -o root -g root /dev/null /etc/edsys-secrets/edsys-rag-eval.env
sudoedit /etc/edsys-secrets/edsys-rag-eval.env
sudo scripts/ops/install-codex-operations.sh --enable
```

Use only `PORTAL_USERNAME` and `PORTAL_PASSWORD` for the scheduled local gate.
Do not add a cloud API key unless a separately approved, budgeted judge run is
being configured.

The installer validates the exact source-controlled `OnCalendar` expressions,
the query schema, the no-secret evaluator dry run, and private secret ownership
before activation. Runtime path checks use systemd `AssertPath*` directives, so
a missing mount, checkout, wrapper, query file, evaluator, or environment file
causes a visible failed unit rather than a successful-looking skipped run.

Every changed live file is listed in the private
`/var/backups/edsys-codex-operations/<run>/install-manifest.tsv`; `true` means a
prior file is stored beside the manifest, and `false` means the target was new.
A replacement-time failure restores these entries automatically. For a later
operator-directed rollback, first record and stop the three timer states,
review the manifest, restore only `true` entries and remove only reviewed
`false` entries, run `systemctl daemon-reload`, and then restore the previously
recorded timer state. Never remove runtime reports or the private credential as
part of a source-definition rollback.

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

The Codex report-runner tests are also self-contained:

```bash
/home/jeremy/code/edsys-ai-portal/.venv/bin/python -m pytest -q \
  scripts/ops/tests/test_edsys_codex_report_runner.py
```
