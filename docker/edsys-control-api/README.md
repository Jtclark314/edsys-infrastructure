# EdSys Control API

Status: deployed on 9950x at `http://192.168.50.50:8099`.

The EdSys Control API is a read-only FastAPI service that exposes the current EdSys source-of-truth YAML as structured JSON for dashboards, local AI, ChatGPT/Codex-assisted workflows, voice queries, and monitoring integrations.

It is not a control system. This first version does not restart, reboot, edit, deploy, or change any live service.

## Architecture

```text
EdSys-Master YAML
  data/network-map.yml
  data/service-catalog.yml
        |
        v
EdSys Control API
  FastAPI normalization, filtering, search, and optional live checks
        |
        v
Dashboards, local AI, future voice assistant, monitoring tools
```

The API must not become a second source of truth. If a device or service changes, update `EdSys-Master` first.

## Folder Map

```text
docker/edsys-control-api/
  README.md
  AI_CONTEXT.md
  compose.yaml
  .env.example
  Dockerfile
  requirements.txt
  app/
    main.py
    config.py
    catalog_loader.py
    models.py
    health_checks.py
    routers/
    static/
  tests/
```

## Source-Of-Truth Files

The API reads these files from a mounted read-only data folder:

- `network-map.yml`
- `service-catalog.yml`

Default container paths:

```text
/data/network-map.yml
/data/service-catalog.yml
```

## Environment Variables

Copy `.env.example` to `.env` when running locally. Do not commit `.env`.

```text
EDSYS_MASTER_DATA_PATH=C:/EdSys-Codex/EdSys-Master/data
EDSYS_NETWORK_MAP=/data/network-map.yml
EDSYS_SERVICE_CATALOG=/data/service-catalog.yml
EDSYS_HEALTH_TIMEOUT_SECONDS=2
EDSYS_ENABLE_LIVE_CHECKS=true
EDSYS_CACHE_SECONDS=30
EDSYS_API_TITLE=EdSys Control API
```

## Local Windows Development

From PowerShell:

```powershell
cd "C:\Users\jtcla\Projects\edsys-infrastructure\docker\edsys-control-api"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
$env:EDSYS_NETWORK_MAP="C:\EdSys-Codex\EdSys-Master\data\network-map.yml"
$env:EDSYS_SERVICE_CATALOG="C:\EdSys-Codex\EdSys-Master\data\service-catalog.yml"
py -m uvicorn app.main:app --host 0.0.0.0 --port 8099
```

Open:

- API: `http://localhost:8099`
- Dashboard: `http://localhost:8099/dashboard`
- OpenAPI docs: `http://localhost:8099/docs`

## Docker Compose

Create `.env` from `.env.example`, then:

```powershell
cd "C:\Users\jtcla\Projects\edsys-infrastructure\docker\edsys-control-api"
docker compose config
docker compose build
docker compose up -d
```

Default local URL:

```text
http://localhost:8099
```

## 9950x Deployment

Current path on 9950x:

```text
/srv/edsys/edsys-infrastructure/docker/edsys-control-api
/srv/edsys/EdSys-Master/data
```

LAN URL:

```text
http://192.168.50.50:8099
```

Mount `/srv/edsys/EdSys-Master/data` read-only to `/data` in the container.

## API Endpoints

- `GET /`
- `GET /api/meta`
- `GET /api/summary`
- `GET /api/categories`
- `GET /api/services`
- `GET /api/services/{service_name}`
- `GET /api/services/critical`
- `GET /api/services/backup-required`
- `GET /api/devices`
- `GET /api/devices/{hostname}`
- `GET /api/devices/{hostname}/services`
- `GET /api/search?q=`
- `GET /api/health`
- `GET /api/health/live`
- `GET /api/health/live/{service_name}`
- `GET /api/health/down`
- `GET /api/health/critical-down`
- `GET /api/export/services.json`
- `GET /api/export/devices.json`
- `GET /dashboard`

## Health Check Behavior

Live checks are safe and read-only:

- HTTP/HTTPS URLs use unauthenticated `HEAD` or `GET`.
- `200-399` means reachable.
- `401` and `403` mean reachable but login is required.
- TCP services use a short socket connection attempt.
- Services without URL or port are skipped.
- Results are cached for `EDSYS_CACHE_SECONDS`.
- The API never performs SSH logins or unknown-port scans.

## Security Notes

- No app login is included in this MVP.
- Keep it LAN-only unless an upstream access layer is added.
- Do not commit `.env`, secrets, tokens, API keys, logs, databases, Docker volumes, or private runtime data.
- This API exposes infrastructure metadata that is useful internally but should not be public without protection.

## Backup And Recovery

The API is stateless. Back up:

- this Git repo for implementation,
- `EdSys-Master` for source-of-truth YAML and docs.

No runtime database is used by this service.

## Known Limitations

- Health checks are shallow reachability checks, not deep application checks.
- No authentication yet.
- No write/control actions yet.
- No Prometheus metrics endpoint yet.
- No Home Assistant or Uptime Kuma integration yet.

## Next Steps

- Deploy on 9950x after review.
- Add `/metrics` for Prometheus-style scraping.
- Add Uptime Kuma integration.
- Add Home Assistant sensors.
- Add authenticated control actions only after a separate design review.
