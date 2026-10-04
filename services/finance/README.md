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
