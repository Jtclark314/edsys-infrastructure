# Foothills Schedule & Procurement Control deployment

Private 9950x deployment for the application source at
`/home/jeremy/code/foothills-project-portal/schedule-procurement`.

## Runtime

- AI Store: `/mnt/ai-store/foothills-schedule-procurement`
- Exact listeners: `192.168.50.50:3037` and `100.87.137.47:3037`
- Container: `foothills-schedule-procurement`
- Environment: `/etc/edsys/schedule-procurement.env` (not in Git)
- Access URL: `https://procure.foothillsproject.com`

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

## External route

The production route is:

`procure.foothillsproject.com` -> Cloudflare Access -> `edsys-ingress` tunnel ->
Caddy -> exact LAN origin `192.168.50.50:3037`.

The Access application is dedicated to this service. The origin environment
must contain that application's audience rather than the prior interim
audience. An unauthenticated redirect, header-spoof denial, DNS, tunnel, Caddy,
and origin readiness must pass after any route change. Real Owner and Kevin
sign-ins and Kevin's exact Editor assignment remain **to be confirmed** until
observed.
