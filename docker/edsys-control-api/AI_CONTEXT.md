# AI Context - EdSys Control API

This project implements the first read-only EdSys Control / Status API.

## Purpose

Expose the current EdSys source-of-truth YAML as structured, queryable JSON for dashboards, local AI, ChatGPT/Codex-assisted workflows, future voice interaction, and monitoring.

## Source-Of-Truth Model

The API reads from `EdSys-Master`:

- `data/network-map.yml`
- `data/service-catalog.yml`

The API is not the source of truth. It reflects the YAML. When inventory changes, update `EdSys-Master` first.

## What Belongs Here

- FastAPI code.
- YAML loading and normalization.
- Read-only search/filter endpoints.
- Safe optional reachability checks.
- Docker Compose deployment definition.
- Tests and API documentation.
- Lightweight static dashboard.

## What Does Not Belong Here

- Passwords.
- API keys.
- Cloudflare tunnel tokens.
- VPN credentials.
- SSH private keys.
- `.env` files.
- Runtime databases.
- Raw audit dumps.
- Docker volumes.
- Logs and backups.
- Restart/reboot/control actions in this MVP.

## Rules For AI Assistants

- Keep the API read-only unless Jeremy explicitly approves a control-system design.
- Preserve compatibility with missing optional YAML fields.
- Preserve extra audited YAML fields; do not discard useful inventory detail.
- Do not infer new services from live ports inside the API.
- Do not perform SSH login checks from this API.
- Keep health checks short, unauthenticated, and cached.
- Do not hard-code Jeremy's LAN secrets or credentials.
- Do not deploy to 9950x unless explicitly asked.

## Security Policy

This API exposes internal infrastructure metadata. It should stay LAN-only until protected by an upstream access layer.

The project can contain `.env.example` placeholders. It must not contain a real `.env`.

## Roadmap

- Deploy to 9950x on port `8099`.
- Add Prometheus-style `/metrics`.
- Add Uptime Kuma integration.
- Add Home Assistant sensor integration.
- Add authenticated write/control actions only after a separate safety review.
