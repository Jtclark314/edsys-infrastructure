# Foothills Schedule & Procurement Control deployment

Private 9950x deployment for the application source at
`/home/jeremy/code/foothills-project-portal/schedule-procurement`.

## Runtime

- AI Store: `/mnt/ai-store/foothills-schedule-procurement`
- Exact listeners: `192.168.50.50:3037` and `100.87.137.47:3037`
- Container: `foothills-schedule-procurement`
- Environment: `/etc/edsys/schedule-procurement.env` (not in Git)
- Planned Access URL: `https://schedule.foothillsproject.com` — **to be confirmed**

No wildcard listener, public router forward, operational database mount into Ask
Foothills, or source document is defined here.

## Deployment

```bash
cd /srv/edsys/edsys-infrastructure/docker/schedule-procurement
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://192.168.50.50:3037/readyz
curl --fail http://100.87.137.47:3037/readyz
```

Direct application API/UI requests require a validated Cloudflare Access JWT.
The health endpoints reveal only application/storage readiness.

## Ask Foothills boundary

Ask mounts only the `publications/` directory read-only at
`/srv/foothills/operational/` so atomic replacement of `current.sqlite3` is
visible without a container restart. The operational
database, immutable `publication-history/`, audits, users, uploads, imports,
reports, and backups are not shared.

## External route acceptance still required

Before declaring the planned hostname operational:

1. Confirm `schedule.foothillsproject.com` is available.
2. Create a dedicated Cloudflare Access self-hosted application and one-time-PIN
   policy.
3. Replace the temporary verified Access audience in the private environment
   with the new dedicated audience.
4. Add the tunnel/Caddy route to exact LAN origin `192.168.50.50:3037`.
5. Verify unauthenticated redirect, header-spoof denial, Owner sign-in, Kevin's
   Editor role, and Viewer denial.

All five remain **to be confirmed** until observed.
