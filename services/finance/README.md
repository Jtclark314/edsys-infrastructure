# EdSys Finance integration

EdSys Finance runs on 9950x under `/srv/edsys-finance`. Its existing application,
Compose definitions, locked dependencies, systemd templates, migrations and
recovery scripts are owned by
`Jtclark314/foothills-project-portal/edsys-finance`, with the canonical checkout at
`/home/jeremy/code/foothills-project-portal/edsys-finance`. This integration record
keeps the infrastructure catalog connected to that source without duplicating it.

The accepted release is `finance-1.0.2`. PostgreSQL remains on its existing
volume and pinned PostgreSQL 16 image. Only API/web are rebuilt for application
releases. The API and web run as non-root users with read-only filesystems,
temporary `/tmp`, dropped capabilities, resource limits and bounded logs.

Private HTTPS is `https://9950x.taile832fe.ts.net:8448`, through the exact
Tailscale Serve route to loopback port 8095. Existing Serve routes are preserved;
Funnel is disabled. LAN HTTP on `192.168.50.50:8094` remains available. The proposed
`finance.edsyslab.com` route is not deployed. It is not an operating dependency.

`scripts/install-operations.sh` in the owning application installs:

- `edsys-finance-sync.timer`: persistent daily catch-up at 01:35 local time plus
  up to five minutes of jitter; manual sync uses the same Item lock and code.
- `edsys-backup.service.d/60-finance.conf`: consistent Finance staging before
  the existing encrypted backup and aggregate completion status afterward.
- `edsys-offsite-sync.service.d/60-finance.conf`: aggregate offsite completion
  status after the existing successful copy; dry/test runs do not advance it.

Finance's recovery stage is `/srv/edsys-backup/staging/finance/current`, covered
by the existing encrypted Restic include. It contains a consistent custom dump,
private environment, release source/exports and a hash/image manifest. Recovery
credentials, raw bank records, private acceptance traces and financial reports
stay outside all repositories and RAG. The named original rollback set remains
under `/srv/edsys-finance/backups`; restoring it replaces the database and loses
later changes, so retain a current dump first.

Use the owning application's `README.md`, `docs/OPERATIONS.md`,
`docs/BACKUP_NOTES.md` and `scripts/rollback.sh` for operation and recovery.
EdSys-Master's `docs/EDSYS_FINANCE.md` and reviewed Obsidian summary record
sanitized current state. Owner review of pay, reserve, variable spending and
monthly bills is required for a cash forecast; successful bank sync does not
prove historical statement completeness.

## Local Finance Tools companion

The Codex analysis/reporting companion was installed and operator-verified on
9950x on 2026-11-10 Eastern. Source is the owning application's
`edsys-finance/integrations/finance_tools/`; that directory owns the bounded
local bridge, analysis, separate planning workspace, report engine and
operating reference. Independent functional forward-testing, report layout
QA and disposable local restoration passed.

The companion uses the existing `edsys-finance-api` container for read-only
main-account queries and the existing conditional cash forecast. It adds no
web listener, public route, bank synchronization, paid analyst submission or
ledger migration. Owner datasets and report packages belong in the private
local workspace `~/.local/share/edsys-finance-tools`, outside Git and RAG. The
MCP server and skill are named `edsys-finance`; there is no separate web UI.

An immutable installed release is selected under
`~/.local/share/edsys-finance-tools/runtime/current`, with CLI and MCP launchers
in `~/.local/bin`. The installed recovery hook
`edsys-backup.service.d/61-finance-tools.conf` runs the root-owned
`/usr/local/libexec/edsys-finance-tools-backup` helper before the existing
encrypted backup. It stages an online SQLite backup and hash-checked report
artifacts under `/srv/edsys-backup/staging/finance-tools/current`, retaining
the prior stage. The existing Restic include covers it; no additional timer
or Finance container restart is introduced. Local staging and an actual
disposable restore passed database integrity and artifact-hash checks;
new-stage encrypted/offsite retrieval remains a separate verification gate.

See the owning [Finance Tools README](/home/jeremy/code/foothills-project-portal/edsys-finance/integrations/finance_tools/README.md)
and `EdSys-Master/docs/EDSYS_FINANCE.md` for scope and verified acceptance.

## Scheduled workbook maintenance

The authorized source is the owning application's
[scheduled workbook integration](https://github.com/Jtclark314/foothills-project-portal/blob/main/edsys-finance/integrations/scheduled_workbook/README.md).
Commissioned and operator-verified on 2026-11-11 Eastern, its dedicated
`edsys-finance-workbook.timer` runs at 06:00 and 18:00
America/New_York, running the existing all-connection bank ingestion before
main-account analysis and current-month native Google Sheets maintenance.
The daily 01:35 bank-only timer remains unchanged. The new job has no web UI
or public route. Its actual systemd run passed native readback, visual review,
transaction reconciliation and independent preservation checks for every other tab.

Private job configuration, embedded managed-cell state and latest/success
receipts belong under `~/.local/share/edsys-finance-tools/scheduled-workbook`.
The installed Finance Tools backup extension snapshots only those checkpoints
under the runner's shared lock; run logs and exports are excluded. Live staging
and a disposable local restore passed database, artifact-hash and checkpoint
binding checks. New-stage encrypted/offsite retrieval remains unverified.
The existing backup hook and Restic stage include require no additional timer
or include rule.

### Dedicated Finance Sheets connection

The owning application's
[Finance Sheets integration](https://github.com/Jtclark314/foothills-project-portal/blob/main/edsys-finance/integrations/finance_sheets/README.md)
provides a root Unix-socket broker and unprivileged MCP worker for the pinned
workbook. Native preflight, a full systemd retry and independent live acceptance
passed on 2026-11-11 Eastern. Generic Drive transport returned HTTP 404 during
repair; its metadata reads later recovered, with cause unverified. The scheduled
finance workflow uses this independent dedicated connection.

`edsys-finance-sheets.service` uses `Type=notify` so the dependent workbook unit
waits for its protected socket. Writes stay within the current-month tab and
configured year; year mismatch stops operational preflight/writes, while
historical reads remain available. There is no web UI or public listener.
Existing root-held authorization is reused without copying or rewriting it.
The private nine-field `/etc/edsys-finance-sheets/config.json` includes the
workbook year. Existing backup includes already select it and its referenced
root auth; no helper, staging or include change was added. A disposable local
settings copy/restore passed schema, hash and permission checks. New encrypted
snapshot/offsite capture remains unverified.
